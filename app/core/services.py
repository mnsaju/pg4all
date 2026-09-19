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

# pgAdmin's own login isn't a secret in itself (it's a fixed, documented
# address, not a generated value) — only the password behind it is, and
# that reuses the build's Postgres superuser password rather than
# generating a second secret to track. Not ".local" — pgAdmin's email
# validator rejects RFC 2606/IANA special-use TLDs (.local, .test,
# .example, .invalid, .localhost) outright, even with delivery checks
# off, and fails silently into a restart loop rather than a clear error.
PGADMIN_EMAIL = "admin@pg4all.dev"


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
        "separate container, published on host port 6432 by default, pointed "
        "at the built Postgres "
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
        "separate container, published on host port 9187 by default, connected "
        "to the built Postgres "
        "instance with the same superuser credential.",
        mode="sidecar", image="quay.io/prometheuscommunity/postgres-exporter:v0.20.1",
        apt_package=None, risk="low", default_selected=False,
    ),
    ServiceSpec(
        "pgadmin", "pgAdmin",
        "Web-based Postgres admin UI, for browsing and querying the built "
        "database without a separate desktop client. Always bound to 127.0.0.1 "
        "only — never published to the network, and that bind is not "
        "configurable — so reach it over an SSH tunnel to the host "
        "(`ssh -L 5050:127.0.0.1:5050 <host>`, then open "
        "http://localhost:5050). The port is configurable; the loopback "
        f"bind is not. Logs in as "
        f"{PGADMIN_EMAIL} with this build's superuser password; add the "
        "Postgres connection yourself the first time you open it — pg4all "
        "doesn't pre-seed it.",
        mode="sidecar", image="dpage/pgadmin4:9.18.0", apt_package=None,
        risk="medium", default_selected=False,
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


def _pgbouncer_fragment(
    spec: ServiceSpec, username: str, password: str, published: str
) -> str:
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
        # The container side is fixed at 5432: edoburu/pgbouncer listens
        # there whatever the host maps it to. 6432 is only the conventional
        # host-side port for a pooler, and mapping 6432:6432 published a
        # port nothing was bound to — every connection through the pooler
        # was refused while pgbouncer looked healthy in `docker compose ps`.
        f"      - \"{published}\"\n"
        f"    depends_on:\n"
        f"      - postgres\n"
        f"    restart: unless-stopped\n"
    )


def _postgres_exporter_fragment(
    spec: ServiceSpec, username: str, password: str, published: str
) -> str:
    return (
        f"  {spec.key}:\n"
        f"    image: {spec.image}\n"
        f"    environment:\n"
        f"      DATA_SOURCE_URI: \"postgres:5432/postgres?sslmode=disable\"\n"
        f"      DATA_SOURCE_USER: {username}\n"
        f"      DATA_SOURCE_PASS: '{password}'\n"
        f"    ports:\n"
        f"      - \"{published}\"\n"
        f"    depends_on:\n"
        f"      - postgres\n"
        f"    restart: unless-stopped\n"
    )


def _pgadmin_fragment(
    spec: ServiceSpec, username: str, password: str, published: str
) -> str:
    return (
        f"  {spec.key}:\n"
        f"    image: {spec.image}\n"
        f"    environment:\n"
        f"      PGADMIN_DEFAULT_EMAIL: {PGADMIN_EMAIL}\n"
        f"      PGADMIN_DEFAULT_PASSWORD: '{password}'\n"
        f"    ports:\n"
        f"      - \"{published}\"\n"
        f"    depends_on:\n"
        f"      - postgres\n"
        f"    restart: unless-stopped\n"
    )


_FRAGMENT_BUILDERS = {
    "pgbouncer": _pgbouncer_fragment,
    "postgres_exporter": _postgres_exporter_fragment,
    "pgadmin": _pgadmin_fragment,
}


def compose_fragment(
    spec: ServiceSpec, username: str, password: str, published: str
) -> str:
    """One docker-compose service block (as YAML text) for a single
    sidecar spec. Only defined for mode == "sidecar" specs.

    `published` is the whole compose `ports:` entry, built by
    app/core/ports.py — it owns which host port and bind address a service
    gets, so that decision isn't spread across four fragment builders."""
    return _FRAGMENT_BUILDERS[spec.key](spec, username, password, published)
