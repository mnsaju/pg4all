"""Companion service registry for the pre-build selection step.

Two modes:
- "sidecar": a separate container in the generated docker-compose.yml,
  networked to the built Postgres container by service name. Used for
  services that only need a connection to Postgres, not filesystem access.
- "apt": baked into the Postgres image itself via the same apt-get layer
  mechanism app/core/extensions.py already uses for apt-sourced extensions.
  pgBackRest needs direct access to the data directory to do local backups,
  so — unlike PgBouncer or the metrics exporter — it doesn't fit the
  sidecar model; running it in the same container is its own documented
  best practice, not a shortcut taken here.

Patroni is deliberately not offered yet: it replaces how Postgres itself is
started (needs a distributed consensus store, multi-node topology, dynamic
config) rather than sitting beside a single static build, which is a
different architecture from what this registry supports today.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    label: str
    description: str
    mode: str  # "sidecar" | "apt"
    image: str | None  # pinned "image:tag", only set when mode == "sidecar"
    apt_package: str | None  # only set when mode == "apt"
    risk: str  # "low" | "medium" — same vocabulary as extensions.py
    default_selected: bool


SERVICES: list[ServiceSpec] = [
    ServiceSpec(
        "pgbouncer", "PgBouncer",
        "Lightweight connection pooler in front of Postgres, useful once "
        "connection count (not query load) is the bottleneck. Runs as a "
        "separate container on port 6432, pointed at the built Postgres "
        "instance with the same superuser credential.",
        mode="sidecar", image="edoburu/pgbouncer:v1.25.2-p0", apt_package=None,
        risk="low", default_selected=False,
    ),
    ServiceSpec(
        "pgbackrest", "pgBackRest",
        "Backup and point-in-time-recovery tool. Installed as a package "
        "inside the Postgres image itself — it needs direct access to the "
        "data directory, so it doesn't fit the sidecar-container model the "
        "other two use — with a generated single-node stanza config. You "
        "still run stanza-create and backup yourself; pg4all doesn't "
        "schedule backups for you.",
        mode="apt", image=None, apt_package="pgbackrest",
        risk="medium", default_selected=False,
    ),
    ServiceSpec(
        "postgres_exporter", "Monitoring (postgres_exporter)",
        "Exposes Postgres metrics for Prometheus to scrape. Runs as a "
        "separate container on port 9187, connected to the built Postgres "
        "instance with the same superuser credential.",
        mode="sidecar", image="quay.io/prometheuscommunity/postgres-exporter:v0.20.1",
        apt_package=None, risk="low", default_selected=False,
    ),
]

_BY_KEY = {s.key: s for s in SERVICES}


def list_services() -> list[ServiceSpec]:
    return SERVICES


def get(key: str) -> ServiceSpec:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"Unknown service: {key}")


def resolve(keys: list[str]) -> list[ServiceSpec]:
    """Validates a raw list of service keys (e.g. from a submitted form),
    silently dropping anything unrecognized — same defensive pattern as
    extensions.resolve()."""
    seen = dict.fromkeys(keys)  # de-dupe, preserve order
    return [_BY_KEY[k] for k in seen if k in _BY_KEY]


def apt_packages(selected: list[ServiceSpec]) -> list[str]:
    return [s.apt_package for s in selected if s.mode == "apt" and s.apt_package]


def render_pgbackrest_conf(pg_major: str) -> str:
    return (
        "[global]\n"
        "repo1-path=/var/lib/pgbackrest\n"
        "\n"
        "[main]\n"
        "pg1-path=/var/lib/postgresql/data\n"
        "pg1-port=5432\n"
    )


def _pgbouncer_fragment(spec: ServiceSpec, username: str, password: str) -> str:
    return (
        f"  {spec.key}:\n"
        f"    image: {spec.image}\n"
        f"    environment:\n"
        f"      DB_HOST: postgres\n"
        f"      DB_USER: {username}\n"
        f"      DB_PASSWORD: '{password}'\n"
        f"      DB_NAME: postgres\n"
        f"      AUTH_TYPE: scram-sha-256\n"
        f"      POOL_MODE: transaction\n"
        f"    ports:\n"
        f"      - \"6432:6432\"\n"
        f"    depends_on:\n"
        f"      - postgres\n"
        f"    restart: unless-stopped\n"
    )


def _postgres_exporter_fragment(spec: ServiceSpec, username: str, password: str) -> str:
    return (
        f"  {spec.key}:\n"
        f"    image: {spec.image}\n"
        f"    environment:\n"
        f"      DATA_SOURCE_URI: \"postgres:5432/postgres?sslmode=disable\"\n"
        f"      DATA_SOURCE_USER: {username}\n"
        f"      DATA_SOURCE_PASS: '{password}'\n"
        f"    ports:\n"
        f"      - \"9187:9187\"\n"
        f"    depends_on:\n"
        f"      - postgres\n"
        f"    restart: unless-stopped\n"
    )


_FRAGMENT_BUILDERS = {
    "pgbouncer": _pgbouncer_fragment,
    "postgres_exporter": _postgres_exporter_fragment,
}


def compose_fragment(spec: ServiceSpec, username: str, password: str) -> str:
    """One docker-compose service block (as YAML text) for a single
    sidecar spec. Only defined for mode == "sidecar" specs."""
    return _FRAGMENT_BUILDERS[spec.key](spec, username, password)
