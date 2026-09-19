"""Cross-parameter validation for a tuned parameter set.

Every slider in the tuner is independent: each one is individually within
a sane range, but the *combination* can still be nonsense. The obvious
case is memory — `max_connections` goes to 500 and `work_mem` is a free
slider, so on the small tier (4 GB) it takes about four drags to describe
a server that allocates more memory than the machine has. PostgreSQL will
happily start with that conf and then get OOM-killed under real load, so
the image builds "successfully" and fails hours later.

These rules catch that class of mistake before a build is spent on it.
They are advisory arithmetic over the tier's declared resources, in the
same spirit as the tuning formulas themselves — not a substitute for
load-testing the result.

Rules live here, in `app/core/`, and nowhere else: the tuner page renders
findings by POSTing back to `/validate` rather than reimplementing the
arithmetic in JavaScript, so there is exactly one copy of each threshold.
"""

from dataclasses import dataclass

from app.core.hardware import HardwareTier

ERROR = "error"
WARNING = "warning"

# Fraction of physical RAM the worst-case allocation may reach before we
# warn. Calibrated deliberately above what the shipped presets produce:
# the PGTune-derived formulas budget work_mem out of (RAM × 0.75 −
# shared_buffers), so the data-warehouse profile legitimately reaches ~88%
# on the large tier. This line exists to catch an operator dragging
# sliders past anything a formula would produce, not to second-guess the
# formulas — see test_every_recommended_preset_is_error_free.
MEMORY_WARN_FRACTION = 0.95

# Conventional ceiling for shared_buffers as a fraction of RAM. The usual
# starting recommendation is 25%; past ~40% you are usually taking memory
# away from the OS page cache for no measured gain.
SHARED_BUFFERS_WARN_FRACTION = 0.40

# max_connections above this without a pooler in front is the point where
# per-backend memory and context switching normally start to dominate.
POOLER_ADVISED_CONNECTIONS = 200


@dataclass(frozen=True)
class Finding:
    level: str  # ERROR | WARNING
    parameter_keys: tuple[str, ...]
    summary: str
    detail: str


def _mb(values: dict[str, float | str], key: str) -> float:
    return float(values[key])


def _worst_case_memory_mb(values: dict[str, float | str]) -> dict[str, float]:
    """Worst-case resident allocation, broken down by source.

    `work_mem` is charged per connection, which is the conventional
    conservative estimate. It is in fact per sort/hash *node*, so a single
    complex query can exceed its own share several times over — the real
    worst case is unbounded, and this figure is a floor on it, not a cap.
    """
    return {
        "shared_buffers": _mb(values, "shared_buffers"),
        "wal_buffers": _mb(values, "wal_buffers"),
        "work_mem": _mb(values, "max_connections") * _mb(values, "work_mem"),
        "maintenance_work_mem": (
            _mb(values, "autovacuum_max_workers") * _mb(values, "maintenance_work_mem")
        ),
    }


def _format_mb(value: float) -> str:
    if value >= 1024:
        return f"{value / 1024:.1f} GB"
    return f"{value:.0f} MB"


def _check_memory_budget(
    values: dict[str, float | str], tier: HardwareTier
) -> list[Finding]:
    ram_mb = tier.ram_gb * 1024
    parts = _worst_case_memory_mb(values)
    total = sum(parts.values())
    fraction = total / ram_mb

    if fraction <= MEMORY_WARN_FRACTION:
        return []

    breakdown = (
        f"shared_buffers {_format_mb(parts['shared_buffers'])} "
        f"+ wal_buffers {_format_mb(parts['wal_buffers'])} "
        f"+ max_connections × work_mem {_format_mb(parts['work_mem'])} "
        f"+ autovacuum_max_workers × maintenance_work_mem "
        f"{_format_mb(parts['maintenance_work_mem'])} "
        f"= {_format_mb(total)} against {tier.ram_gb} GB of RAM "
        f"({fraction * 100:.0f}%)."
    )

    if fraction > 1.0:
        return [
            Finding(
                level=ERROR,
                parameter_keys=(
                    "shared_buffers", "work_mem", "maintenance_work_mem",
                    "max_connections", "autovacuum_max_workers",
                ),
                summary=(
                    f"Worst-case memory exceeds the tier's RAM "
                    f"({_format_mb(total)} > {tier.ram_gb} GB)"
                ),
                detail=(
                    breakdown
                    + " PostgreSQL will start, then get OOM-killed once enough "
                    "connections run memory-hungry queries at the same time. "
                    "Lower work_mem or max_connections, or move to a larger tier."
                ),
            )
        ]

    return [
        Finding(
            level=WARNING,
            parameter_keys=("shared_buffers", "work_mem", "max_connections"),
            summary=(
                f"Worst-case memory uses {fraction * 100:.0f}% of the tier's RAM"
            ),
            detail=(
                breakdown
                + " That leaves little headroom for the OS page cache, which "
                "PostgreSQL depends on for reads that miss shared_buffers."
            ),
        )
    ]


def _check_shared_buffers_share(
    values: dict[str, float | str], tier: HardwareTier
) -> list[Finding]:
    ram_mb = tier.ram_gb * 1024
    shared_buffers = _mb(values, "shared_buffers")
    fraction = shared_buffers / ram_mb
    if fraction <= SHARED_BUFFERS_WARN_FRACTION:
        return []
    return [
        Finding(
            level=WARNING,
            parameter_keys=("shared_buffers",),
            summary=f"shared_buffers is {fraction * 100:.0f}% of RAM",
            detail=(
                f"{_format_mb(shared_buffers)} of {tier.ram_gb} GB. The usual "
                "starting point is around 25%; beyond roughly 40% you are "
                "taking memory from the OS page cache, which PostgreSQL also "
                "reads through, so the two caches hold the same pages twice."
            ),
        )
    ]


def _check_effective_cache_size(
    values: dict[str, float | str], tier: HardwareTier
) -> list[Finding]:
    ram_mb = tier.ram_gb * 1024
    effective_cache_size = _mb(values, "effective_cache_size")
    shared_buffers = _mb(values, "shared_buffers")
    findings = []

    if effective_cache_size < shared_buffers:
        findings.append(
            Finding(
                level=WARNING,
                parameter_keys=("effective_cache_size", "shared_buffers"),
                summary="effective_cache_size is below shared_buffers",
                detail=(
                    f"{_format_mb(effective_cache_size)} vs "
                    f"{_format_mb(shared_buffers)}. effective_cache_size is the "
                    "planner's estimate of all cache available for this database "
                    "— the OS page cache plus shared_buffers — so it should never "
                    "be the smaller of the two. Too low makes the planner avoid "
                    "index scans it should choose."
                ),
            )
        )

    if effective_cache_size > ram_mb:
        findings.append(
            Finding(
                level=WARNING,
                parameter_keys=("effective_cache_size",),
                summary="effective_cache_size exceeds the tier's RAM",
                detail=(
                    f"{_format_mb(effective_cache_size)} against {tier.ram_gb} GB. "
                    "It allocates nothing, so this won't crash anything, but it "
                    "tells the planner more data is cached than can possibly be, "
                    "which biases it toward index scans that will hit disk."
                ),
            )
        )

    return findings


def _check_parallel_workers(
    values: dict[str, float | str], tier: HardwareTier
) -> list[Finding]:
    per_gather = _mb(values, "max_parallel_workers_per_gather")
    max_workers = _mb(values, "max_worker_processes")
    if per_gather <= max_workers:
        return []
    return [
        Finding(
            level=WARNING,
            parameter_keys=("max_parallel_workers_per_gather", "max_worker_processes"),
            summary=(
                "max_parallel_workers_per_gather is above max_worker_processes"
            ),
            detail=(
                f"{per_gather:.0f} requested per Gather node, but only "
                f"{max_workers:.0f} background worker processes exist in total. "
                "Parallel workers are drawn from that pool, so the excess is "
                "silently never granted — the plan asks for workers it cannot get."
            ),
        )
    ]


def _check_connection_pooling(
    values: dict[str, float | str], tier: HardwareTier, service_keys: frozenset[str]
) -> list[Finding]:
    max_connections = _mb(values, "max_connections")
    if max_connections <= POOLER_ADVISED_CONNECTIONS or "pgbouncer" in service_keys:
        return []
    return [
        Finding(
            level=WARNING,
            parameter_keys=("max_connections",),
            summary=(
                f"{max_connections:.0f} connections with no pooler selected"
            ),
            detail=(
                "Each connection is a backend process with its own memory. Past "
                "roughly 200 on this hardware, throughput is usually limited by "
                "process overhead rather than the database itself. Enabling the "
                "PgBouncer companion service lets you keep max_connections low "
                "while still serving many clients."
            ),
        )
    ]


def _check_synchronous_commit(
    values: dict[str, float | str], tier: HardwareTier
) -> list[Finding]:
    if values.get("synchronous_commit") != "off":
        return []
    return [
        Finding(
            level=WARNING,
            parameter_keys=("synchronous_commit",),
            summary="synchronous_commit is off — committed transactions can be lost",
            detail=(
                "Commits return before their WAL record reaches disk, so an OS "
                "crash or power loss can lose transactions the application was "
                "already told had succeeded. The database stays consistent — this "
                "is not corruption — but the most recent moment of writes is gone. "
                "Deliberate for some workloads; never a default."
            ),
        )
    ]


def validate(
    values: dict[str, float | str],
    tier: HardwareTier,
    service_keys: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Check a tuned parameter set as a whole against the chosen tier.

    `values` holds every key in `parameters.PARAMETER_SPECS` in its native
    unit — megabytes for the memory parameters, plain numbers for the rest,
    and the chosen string for enums. Errors come first, then warnings.
    """
    findings: list[Finding] = []
    findings += _check_memory_budget(values, tier)
    findings += _check_shared_buffers_share(values, tier)
    findings += _check_effective_cache_size(values, tier)
    findings += _check_parallel_workers(values, tier)
    findings += _check_connection_pooling(values, tier, service_keys)
    findings += _check_synchronous_commit(values, tier)
    return sorted(findings, key=lambda f: 0 if f.level == ERROR else 1)


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == ERROR for f in findings)
