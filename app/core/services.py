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

# Grafana's built-in admin. Like pgAdmin's login, the username isn't the
# secret — the password is, and it reuses the build's superuser password
# rather than generating a second credential to track and hand over.
GRAFANA_ADMIN_USER = "admin"


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
    # SPDX id of the referenced image's primary project, for the
    # THIRD_PARTY_NOTICES manifest (app/core/licensing.py). Only meaningful
    # for mode == "sidecar" images the compose file pulls whole; an "apt"
    # service is baked into the Postgres image and its license is picked up
    # by scanning that image instead. Hand-maintained alongside the pinned
    # `image` tag above it, so bumping the pin puts the license right there
    # to reconsider — and it is the *primary* project license only; each
    # image bundles base-layer components under their own licenses too.
    license: str = ""
    # Services this one cannot work without. Grafana can't scrape, so it
    # needs Prometheus, which needs something to scrape — ticking one box
    # brings the whole chain rather than silently producing a dashboard
    # wired to nothing.
    requires: tuple[str, ...] = ()
    # Named volumes this service needs declared at the top of the compose
    # file. Only monitoring has state worth keeping so far.
    named_volumes: tuple[str, ...] = ()


SERVICES: list[ServiceSpec] = [
    ServiceSpec(
        "pgbouncer", "PgBouncer",
        "Lightweight connection pooler in front of Postgres, useful once "
        "connection count (not query load) is the bottleneck. Runs as a "
        "separate container, published on host port 6432 by default, pointed "
        "at the built Postgres "
        "instance with the same superuser credential.",
        mode="sidecar", image="edoburu/pgbouncer:v1.25.2-p0", apt_package=None,
        risk="low", default_selected=False, license="ISC",
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
        apt_package=None, risk="low", default_selected=False, license="Apache-2.0",
    ),
    ServiceSpec(
        "prometheus", "Prometheus",
        "Scrapes the metrics exporter and stores them as a time series, so "
        "there is history to look at rather than only the current value. "
        "Published on 127.0.0.1 only — it has no authentication whatsoever "
        "and its UI exposes every metric and its own running config, so the "
        "loopback bind is the only thing protecting it. Retains 15 days or "
        "2 GB, whichever comes first.",
        mode="sidecar", image="prom/prometheus:v3.14.0", apt_package=None,
        risk="low", default_selected=False, license="Apache-2.0",
        requires=("postgres_exporter",),
        named_volumes=("prometheus_data",),
    ),
    ServiceSpec(
        "grafana", "Grafana dashboard",
        "Dashboards over the Prometheus data, with one dashboard generated "
        "from this build's own tuning values — connections plotted against "
        "the max_connections it chose, checkpoints against its max_wal_size. "
        "Selecting this also brings Prometheus and the metrics exporter, "
        "since a dashboard with nothing scraping is empty. Bound to "
        "127.0.0.1 only, like pgAdmin; reach it over an SSH tunnel. Logs in "
        "as admin with this build's superuser password.",
        mode="sidecar", image="grafana/grafana:13.2.2", apt_package=None,
        risk="medium", default_selected=False, license="AGPL-3.0-only",
        requires=("postgres_exporter", "prometheus"),
        named_volumes=("grafana_data",),
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
        risk="medium", default_selected=False, license="PostgreSQL",
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
    extensions.resolve() — and pulling in anything the selection requires.

    Dependencies come first in the result, so the compose file lists a
    service after the ones it depends on. That's cosmetic (depends_on does
    the real ordering) but makes the generated file read in the order
    things actually start.
    """
    ordered: dict[str, None] = {}

    def add(key: str, seen: frozenset[str]) -> None:
        if key not in _BY_KEY or key in ordered or key in seen:
            return
        for required in _BY_KEY[key].requires:
            add(required, seen | {key})
        ordered[key] = None

    for key in keys:
        add(key, frozenset())
    return [_BY_KEY[k] for k in ordered]


def named_volumes(selected: list[ServiceSpec]) -> list[str]:
    """Every named volume the selection needs, de-duplicated, in order."""
    volumes: dict[str, None] = {}
    for spec in selected:
        for volume in spec.named_volumes:
            volumes[volume] = None
    return list(volumes)


def apt_packages(selected: list[ServiceSpec]) -> list[str]:
    return [s.apt_package for s in selected if s.mode == "apt" and s.apt_package]


def render_pgbackrest_conf(pg_major: str) -> str:
    # pg1-port stays 5432 whatever host port the build publishes. pgBackRest
    # runs inside the Postgres container and connects over its loopback, so
    # this is the container-internal port — the one thing app/core/ports.py
    # never changes. Pointing it at the configured host port would break
    # backups on every build that moved the published port.
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
        # PostgreSQL 17 moved the checkpoint counters out of pg_stat_bgwriter
        # into the new pg_stat_checkpointer view, and this collector — which
        # reads that view — is off by default. Without it, the dashboard's
        # checkpoint panel is silently empty on 17 and 18. The flag is inert
        # on 16, where the view doesn't exist and the old bgwriter metrics
        # are still published, so it's passed unconditionally.
        f"    command:\n"
        f"      - \"--collector.stat_checkpointer\"\n"
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


def _prometheus_fragment(
    spec: ServiceSpec, username: str, password: str, published: str
) -> str:
    # No credentials: Prometheus has no authentication to configure. The
    # loopback bind in `published` is the whole access story.
    return (
        f"  {spec.key}:\n"
        f"    image: {spec.image}\n"
        # Overriding `command` drops the image's own defaults, so the config
        # path and storage path have to be restated alongside the retention
        # limits — omitting them leaves Prometheus looking in the wrong place.
        f"    command:\n"
        f"      - \"--config.file=/etc/prometheus/prometheus.yml\"\n"
        f"      - \"--storage.tsdb.path=/prometheus\"\n"
        f"      - \"--storage.tsdb.retention.time=15d\"\n"
        f"      - \"--storage.tsdb.retention.size=2GB\"\n"
        f"    volumes:\n"
        f"      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro\n"
        f"      - prometheus_data:/prometheus\n"
        f"    ports:\n"
        f"      - \"{published}\"\n"
        f"    depends_on:\n"
        f"      - postgres_exporter\n"
        f"    restart: unless-stopped\n"
    )


def _grafana_fragment(
    spec: ServiceSpec, username: str, password: str, published: str
) -> str:
    return (
        f"  {spec.key}:\n"
        f"    image: {spec.image}\n"
        f"    environment:\n"
        f"      GF_SECURITY_ADMIN_USER: {GRAFANA_ADMIN_USER}\n"
        f"      GF_SECURITY_ADMIN_PASSWORD: '{password}'\n"
        f"      GF_USERS_ALLOW_SIGN_UP: \"false\"\n"
        f"      GF_AUTH_ANONYMOUS_ENABLED: \"false\"\n"
        f"    volumes:\n"
        # Provisioning and dashboards are read-only bind mounts; only
        # Grafana's own database lives in the named volume. Keeping the
        # dashboards out of /var/lib/grafana avoids nesting a bind mount
        # inside a volume, which works but reads as an accident.
        f"      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro\n"
        f"      - ./monitoring/grafana/dashboards:/etc/grafana/dashboards:ro\n"
        f"      - grafana_data:/var/lib/grafana\n"
        f"    ports:\n"
        f"      - \"{published}\"\n"
        f"    depends_on:\n"
        f"      - prometheus\n"
        f"    restart: unless-stopped\n"
    )


_FRAGMENT_BUILDERS = {
    "pgbouncer": _pgbouncer_fragment,
    "postgres_exporter": _postgres_exporter_fragment,
    "pgadmin": _pgadmin_fragment,
    "prometheus": _prometheus_fragment,
    "grafana": _grafana_fragment,
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
