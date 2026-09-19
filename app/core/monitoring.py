"""Prometheus and Grafana configuration for a build's monitoring stack.

Pure text and dict generation, no file I/O and no FastAPI — same split as
app/core/services.py against app/builder/compose_gen.py. Writing these to
a build directory is app/builder/monitoring_gen.py.

pg4all shipped `postgres_exporter` for a while with nothing to consume it:
metrics on 9187 and an assumption the operator had a Prometheus somewhere.
This is the other half — a Prometheus to scrape and keep them, and a
Grafana to draw them.

The dashboard is generated rather than copied from grafana.com, because
pg4all knows something a stock dashboard can't: the values it just chose.
A community dashboard plots connections against an arbitrary axis; this
one draws the line at the `max_connections` this build was given, and
labels the cache-hit panel with the `shared_buffers` behind it. Four of
the nine panels carry a tuned value, which makes the dashboard answer
"is the configuration pg4all generated holding up?" rather than the more
generic "how is Postgres?" — the smoke test's question, over time.

Every panel but one is built from metrics the exporter publishes by
default. The exception is the checkpoint panel, which needs the
stat_checkpointer collector enabled on PostgreSQL 17+ — see
`checkpoint_metrics` below and the exporter fragment in
app/core/services.py.
"""

import json

# Service names on the generated compose network, not host ports — these
# containers talk to each other inside the stack, so moving a published
# port doesn't affect any of this.
EXPORTER_TARGET = "postgres_exporter:9187"
PROMETHEUS_URL = "http://prometheus:9090"

SCRAPE_INTERVAL = "15s"
DATASOURCE_UID = "pg4all-prometheus"
DASHBOARD_UID = "pg4all-postgres"
DASHBOARD_TITLE = "pg4all — PostgreSQL"

# Where the Grafana container finds its provisioned files. Kept out of
# /var/lib/grafana because that path is a named volume; nesting a
# read-only bind mount inside it works but is needlessly subtle.
GRAFANA_DASHBOARD_DIR = "/etc/grafana/dashboards"

# Service keys that mean "this build wants monitoring".
MONITORING_SERVICE_KEYS = frozenset({"prometheus", "grafana"})

# PostgreSQL 17 moved the checkpoint counters out of pg_stat_bgwriter into
# a new pg_stat_checkpointer view, so the metric names differ by major.
# Querying the wrong pair renders a perfectly good-looking panel with
# nothing in it — pg4all knows which major it built, so it can ask for the
# right one. (The exporter's stat_checkpointer collector is off by default;
# app/core/services.py turns it on.)
CHECKPOINTER_VIEW_FROM_MAJOR = 17

_CHECKPOINT_METRICS_LEGACY = (
    "pg_stat_bgwriter_checkpoints_timed_total",
    "pg_stat_bgwriter_checkpoints_req_total",
)
_CHECKPOINT_METRICS_MODERN = (
    "pg_stat_checkpointer_num_timed_total",
    "pg_stat_checkpointer_num_requested_total",
)


def checkpoint_metrics(pg_major: str | int) -> tuple[str, str]:
    """The (timed, requested) checkpoint counter names for this major."""
    try:
        major = int(str(pg_major).split(".")[0])
    except (TypeError, ValueError):
        major = CHECKPOINTER_VIEW_FROM_MAJOR
    if major >= CHECKPOINTER_VIEW_FROM_MAJOR:
        return _CHECKPOINT_METRICS_MODERN
    return _CHECKPOINT_METRICS_LEGACY


def render_prometheus_config() -> str:
    return (
        "global:\n"
        f"  scrape_interval: {SCRAPE_INTERVAL}\n"
        "\n"
        "scrape_configs:\n"
        "  - job_name: postgres\n"
        "    static_configs:\n"
        f"      - targets: ['{EXPORTER_TARGET}']\n"
    )


def render_grafana_datasource() -> str:
    return (
        "apiVersion: 1\n"
        "\n"
        "datasources:\n"
        "  - name: Prometheus\n"
        f"    uid: {DATASOURCE_UID}\n"
        "    type: prometheus\n"
        "    access: proxy\n"
        f"    url: {PROMETHEUS_URL}\n"
        "    isDefault: true\n"
        "    editable: false\n"
    )


def render_grafana_dashboard_provider() -> str:
    return (
        "apiVersion: 1\n"
        "\n"
        "providers:\n"
        "  - name: pg4all\n"
        "    type: file\n"
        "    disableDeletion: false\n"
        # The operator's own edits stick. Regenerating on a later build
        # writes into that build's own directory, so nothing is clobbered.
        "    allowUiUpdates: true\n"
        "    options:\n"
        f"      path: {GRAFANA_DASHBOARD_DIR}\n"
    )


def _datasource() -> dict:
    return {"type": "prometheus", "uid": DATASOURCE_UID}


def _target(expr: str, legend: str | None = None, ref_id: str = "A") -> dict:
    target = {"expr": expr, "refId": ref_id, "datasource": _datasource()}
    if legend:
        target["legendFormat"] = legend
    return target


def _panel(
    panel_id: int,
    title: str,
    description: str,
    targets: list[dict],
    grid: tuple[int, int, int, int],  # x, y, w, h
    panel_type: str = "timeseries",
    unit: str = "short",
    thresholds: list[dict] | None = None,
    threshold_line: bool = False,
) -> dict:
    x, y, w, h = grid
    custom: dict = {}
    if threshold_line:
        # Draws the threshold as a line across the graph rather than only
        # recolouring the series — the point is to see the ceiling.
        custom["thresholdsStyle"] = {"mode": "dashed"}

    return {
        "id": panel_id,
        "type": panel_type,
        "title": title,
        "description": description,
        "datasource": _datasource(),
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": custom,
                "thresholds": {
                    "mode": "absolute",
                    "steps": thresholds
                    or [{"color": "text", "value": None}],
                },
            },
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
    }


def _format_mb(value: float) -> str:
    return f"{value / 1024:.1f} GB" if value >= 1024 else f"{value:.0f} MB"


def build_dashboard(
    conf_values: dict[str, float | str], pg_major: str | int = "17"
) -> dict:
    """The dashboard for one build, with that build's tuning values in it.

    `conf_values` is what the conf actually asks for, in native units — the
    same dict the smoke test is held to, so the dashboard and the smoke
    test can't disagree about what was configured. `pg_major` selects the
    checkpoint metric names, which moved views in PostgreSQL 17.
    """
    max_connections = float(conf_values["max_connections"])
    shared_buffers = float(conf_values["shared_buffers"])
    max_wal_size = float(conf_values["max_wal_size"])
    checkpoint_timeout = float(conf_values["checkpoint_timeout"])
    timed_metric, requested_metric = checkpoint_metrics(pg_major)

    panels = [
        _panel(
            1, "Up",
            "Whether Prometheus can reach the exporter and the exporter can "
            "reach Postgres. If this is 0, nothing else on this dashboard "
            "means anything.",
            [_target("pg_up")],
            grid=(0, 0, 8, 4),
            panel_type="stat",
            thresholds=[
                {"color": "red", "value": None},
                {"color": "green", "value": 1},
            ],
        ),
        _panel(
            2, "Longest running transaction",
            "A transaction open for a long time holds back autovacuum across "
            "the whole database, which is a common root cause of bloat that "
            "looks like a vacuum problem.",
            [_target("max(pg_stat_activity_max_tx_duration)", "longest")],
            grid=(8, 0, 8, 4),
            panel_type="stat",
            unit="s",
            thresholds=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 300},
                {"color": "red", "value": 3600},
            ],
        ),
        _panel(
            3, "Database size",
            "Total size of all databases. Growth rate here is your disk "
            "runway.",
            [_target("sum(pg_database_size_bytes)", "total")],
            grid=(16, 0, 8, 4),
            panel_type="stat",
            unit="bytes",
        ),
        _panel(
            4, "Connections by state",
            f"Against this build's max_connections of {max_connections:.0f} "
            "(the dashed line). Climbing toward it means clients will start "
            "being refused; consider the PgBouncer companion service rather "
            "than raising the ceiling.",
            [_target("sum by (state) (pg_stat_activity_count)", "{{state}}")],
            grid=(0, 4, 12, 8),
            thresholds=[
                {"color": "text", "value": None},
                {"color": "orange", "value": round(max_connections * 0.8)},
                {"color": "red", "value": round(max_connections)},
            ],
            threshold_line=True,
        ),
        _panel(
            5, "Cache hit ratio",
            f"Reads served from shared_buffers ({_format_mb(shared_buffers)} "
            "on this build) rather than from disk. Sustained below ~99% on a "
            "read-heavy workload is the signal that shared_buffers is too "
            "small for the working set.",
            [
                _target(
                    "sum(rate(pg_stat_database_blks_hit[5m])) / "
                    "clamp_min("
                    "sum(rate(pg_stat_database_blks_hit[5m])) + "
                    "sum(rate(pg_stat_database_blks_read[5m])), 1)",
                    "hit ratio",
                )
            ],
            grid=(12, 4, 12, 8),
            unit="percentunit",
            thresholds=[
                {"color": "red", "value": None},
                {"color": "orange", "value": 0.90},
                {"color": "green", "value": 0.99},
            ],
            threshold_line=True,
        ),
        _panel(
            6, "Transactions per second",
            "Baseline load, and the rollback rate beside it. Rollbacks "
            "climbing without a deploy usually means application errors, not "
            "database ones.",
            [
                _target(
                    "sum(rate(pg_stat_database_xact_commit[5m]))", "commits", "A"
                ),
                _target(
                    "sum(rate(pg_stat_database_xact_rollback[5m]))",
                    "rollbacks",
                    "B",
                ),
            ],
            grid=(0, 12, 12, 8),
            unit="ops",
        ),
        _panel(
            7, "Checkpoints: timed vs requested",
            f"This build set max_wal_size to {_format_mb(max_wal_size)} and "
            f"checkpoint_timeout to {checkpoint_timeout:.0f} min. Requested "
            "checkpoints consistently outpacing timed ones means WAL is "
            "filling before the timeout expires — max_wal_size is too small "
            "for the write rate, and checkpoints are happening more often "
            "than intended.",
            [
                _target(f"rate({timed_metric}[15m])", "timed", "A"),
                _target(f"rate({requested_metric}[15m])", "requested", "B"),
            ],
            grid=(12, 12, 12, 8),
            unit="ops",
        ),
        _panel(
            8, "Deadlocks",
            "Rare and always worth knowing about: two transactions took the "
            "same locks in opposite orders and Postgres killed one of them.",
            [_target("sum(rate(pg_stat_database_deadlocks[5m]))", "deadlocks")],
            grid=(0, 20, 12, 8),
            unit="ops",
            thresholds=[
                {"color": "green", "value": None},
                {"color": "red", "value": 0.001},
            ],
        ),
        _panel(
            9, "Locks by mode",
            "Lock contention by type. A rising count of "
            "AccessExclusiveLock usually means DDL is queueing behind live "
            "traffic.",
            [_target("sum by (mode) (pg_locks_count)", "{{mode}}")],
            grid=(12, 20, 12, 8),
        ),
    ]

    return {
        "uid": DASHBOARD_UID,
        "title": DASHBOARD_TITLE,
        "tags": ["pg4all", "postgresql"],
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "timezone": "browser",
        "panels": panels,
    }


def render_dashboard_json(
    conf_values: dict[str, float | str], pg_major: str | int = "17"
) -> str:
    return json.dumps(build_dashboard(conf_values, pg_major), indent=2) + "\n"


def is_selected(service_keys) -> bool:
    return bool(MONITORING_SERVICE_KEYS & set(service_keys))
