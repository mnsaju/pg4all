"""Tunable parameter registry for the Config Tuner UI.

Each entry pairs a postgresql.conf key with advisory metadata (impact
score, risk level, whether it needs a restart, and a slider range).
`restart_required` is cross-checked against real PostgreSQL parameter
contexts (settings with context=postmaster need a restart; sighup/user
ones don't) — it isn't just editorial. Impact/risk are advisory, in the
same spirit as the hardware tiers: a starting signal, not a guarantee.

Every slider here has exactly one corresponding line in the generated
conf (see conf_generator.generate_conf) — no hidden, untunable settings.
"""

from dataclasses import dataclass

from app.core import pg_defaults
from app.core.conf_generator import generate_conf
from app.core.hardware import HardwareTier
from app.core.workloads import Workload

CATEGORY_ORDER = ["memory", "connections", "query", "wal", "logging", "autovacuum"]

CATEGORY_LABELS = {
    "memory": "Memory",
    "connections": "Connections",
    "query": "Query",
    "wal": "WAL",
    "logging": "Logging",
    "autovacuum": "Autovacuum",
}


@dataclass(frozen=True)
class ParameterSpec:
    key: str
    category: str
    description: str
    impact: int
    risk: str  # "low" | "medium" | "high"
    restart_required: bool
    min_value: float
    max_value: float
    step: float
    kind: str  # "memory_mb" | "int" | "float"
    unit: str = ""
    decimals: int = 2


PARAMETER_SPECS: list[ParameterSpec] = [
    ParameterSpec(
        "shared_buffers", "memory",
        "Amount of memory dedicated to PostgreSQL's shared buffer cache. "
        "Larger values reduce disk reads for frequently accessed data.",
        impact=92, risk="low", restart_required=True,
        min_value=32, max_value=16384, step=32, kind="memory_mb", unit="MB",
    ),
    ParameterSpec(
        "effective_cache_size", "memory",
        "Planner's estimate of memory available for disk caching (OS "
        "cache + shared_buffers). Doesn't allocate memory itself.",
        impact=78, risk="low", restart_required=False,
        min_value=1024, max_value=32768, step=256, kind="memory_mb", unit="MB",
    ),
    ParameterSpec(
        "work_mem", "memory",
        "Memory per sort/hash operation, per connection. Higher values "
        "reduce disk-based sorts but multiply by max_connections.",
        impact=85, risk="medium", restart_required=False,
        min_value=1, max_value=1024, step=1, kind="memory_mb", unit="MB",
    ),
    ParameterSpec(
        "maintenance_work_mem", "memory",
        "Memory for maintenance operations (VACUUM, CREATE INDEX, ALTER "
        "TABLE). Can safely exceed work_mem.",
        impact=65, risk="low", restart_required=False,
        min_value=16, max_value=4096, step=16, kind="memory_mb", unit="MB",
    ),
    ParameterSpec(
        "max_connections", "connections",
        "Maximum number of concurrent client connections. Lower is often "
        "better when paired with a connection pooler.",
        impact=70, risk="high", restart_required=True,
        min_value=20, max_value=500, step=10, kind="int",
    ),
    ParameterSpec(
        "max_worker_processes", "connections",
        "Maximum background worker processes, including parallel query "
        "and logical replication workers.",
        impact=55, risk="medium", restart_required=True,
        min_value=4, max_value=64, step=1, kind="int",
    ),
    ParameterSpec(
        "max_parallel_workers_per_gather", "query",
        "Maximum workers a single Gather/GatherMerge node may use. More "
        "workers can speed up large scans.",
        impact=60, risk="low", restart_required=False,
        min_value=0, max_value=16, step=1, kind="int",
    ),
    ParameterSpec(
        "random_page_cost", "query",
        "Planner's cost estimate for a non-sequential disk page fetch. "
        "Lower for SSD-backed storage.",
        impact=72, risk="low", restart_required=False,
        min_value=0.5, max_value=10, step=0.1, kind="float", decimals=1,
    ),
    ParameterSpec(
        "effective_io_concurrency", "query",
        "Number of concurrent I/O requests PostgreSQL expects the "
        "storage to handle for bitmap heap scans.",
        impact=55, risk="low", restart_required=False,
        min_value=0, max_value=1000, step=10, kind="int",
    ),
    ParameterSpec(
        "checkpoint_completion_target", "wal",
        "Target fraction of the checkpoint interval to spread writes "
        "over. Higher smooths I/O at the cost of more WAL retained.",
        impact=68, risk="low", restart_required=False,
        min_value=0.1, max_value=1.0, step=0.05, kind="float", decimals=2,
    ),
    ParameterSpec(
        "wal_buffers", "wal",
        "Shared memory for WAL data before it's written to disk. "
        "Auto-sized to 1/32 of shared_buffers by default.",
        impact=48, risk="low", restart_required=True,
        min_value=1, max_value=256, step=1, kind="memory_mb", unit="MB",
    ),
    ParameterSpec(
        "max_wal_size", "wal",
        "Maximum WAL size between automatic checkpoints. Larger values "
        "reduce checkpoint frequency at the cost of longer recovery.",
        impact=62, risk="low", restart_required=False,
        min_value=256, max_value=16384, step=256, kind="memory_mb", unit="MB",
    ),
    ParameterSpec(
        "log_min_duration_statement", "logging",
        "Log statements running longer than this many milliseconds. "
        "-1 disables, 0 logs every statement.",
        impact=35, risk="low", restart_required=False,
        min_value=-1, max_value=60000, step=100, kind="int", unit="ms",
    ),
    ParameterSpec(
        "autovacuum_max_workers", "autovacuum",
        "Maximum concurrent autovacuum worker processes. More workers "
        "help databases with many actively-written tables.",
        impact=58, risk="low", restart_required=True,
        min_value=1, max_value=16, step=1, kind="int",
    ),
    ParameterSpec(
        "autovacuum_vacuum_scale_factor", "autovacuum",
        "Fraction of a table's rows that must change before autovacuum "
        "triggers on it. Lower means more frequent, smaller vacuums.",
        impact=52, risk="low", restart_required=False,
        min_value=0.01, max_value=1.0, step=0.01, kind="float", decimals=2,
    ),
]

_BY_KEY = {p.key: p for p in PARAMETER_SPECS}


def get(key: str) -> ParameterSpec:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"Unknown parameter: {key}")


def _parse_size_to_mb(raw: str) -> float:
    raw = raw.strip()
    for suffix, factor in (("GB", 1024.0), ("MB", 1.0), ("kB", 1.0 / 1024.0)):
        if raw.upper().endswith(suffix.upper()):
            return float(raw[: -len(suffix)]) * factor
    return float(raw)


def parse_value(spec: ParameterSpec, raw: str) -> float:
    if spec.kind == "memory_mb":
        return _parse_size_to_mb(raw)
    return float(raw)


def format_display(spec: ParameterSpec, number: float) -> str:
    if spec.kind == "float":
        number_text = f"{number:.{spec.decimals}f}"
    else:
        number_text = str(int(round(number)))
    return f"{number_text} {spec.unit}".strip()


def format_conf_value(spec: ParameterSpec, number: float) -> str:
    if spec.kind == "memory_mb":
        return f"{int(round(number))}MB"
    if spec.kind == "float":
        return f"{number:.{spec.decimals}f}"
    return str(int(round(number)))


@dataclass(frozen=True)
class ParameterRow:
    spec: ParameterSpec
    default_value: float
    recommended_value: float
    default_display: str
    recommended_display: str
    comparison: str  # "=" | "→" (higher) | "←" (lower)


@dataclass(frozen=True)
class CategoryGroup:
    key: str
    label: str
    rows: list[ParameterRow]


def build_tuner_groups(workload: Workload, tier: HardwareTier) -> list[CategoryGroup]:
    recommended_conf = generate_conf(workload, tier)

    rows_by_category: dict[str, list[ParameterRow]] = {c: [] for c in CATEGORY_ORDER}
    for spec in PARAMETER_SPECS:
        default_value = parse_value(spec, pg_defaults.DEFAULTS[spec.key])
        recommended_value = parse_value(spec, recommended_conf[spec.key])

        if recommended_value == default_value:
            comparison = "="
        elif recommended_value > default_value:
            comparison = "→"
        else:
            comparison = "←"

        rows_by_category[spec.category].append(
            ParameterRow(
                spec=spec,
                default_value=default_value,
                recommended_value=recommended_value,
                default_display=format_display(spec, default_value),
                recommended_display=format_display(spec, recommended_value),
                comparison=comparison,
            )
        )

    return [
        CategoryGroup(key=cat, label=CATEGORY_LABELS[cat], rows=rows_by_category[cat])
        for cat in CATEGORY_ORDER
    ]
