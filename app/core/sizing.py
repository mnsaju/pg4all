"""Suggesting a machine from how much data there is and how busy it gets.

Everything else pg4all computes is derived: the tuning formulas come from
PGTune's published algorithm and each one can be pointed at. This module
cannot make that claim, and says so out loud rather than dressing rules of
thumb up as arithmetic.

The reason is structural. PGTune's formulas take RAM and CPU and produce
settings; nothing in them takes data volume. Data volume tells you what
machine to *buy*, not what to set — and the step from "500 GB of data" to
"64 GB of RAM" runs through an estimate of how much of that data is
actually hot, which no tool can know from the outside. So every number
here ships with the reasoning that produced it, and the output is a
starting point to check against reality, not an answer.

The checking is not hypothetical: the Grafana dashboard pg4all generates
has a cache hit ratio panel, and that is exactly the measurement that
tells you whether the guess about the hot set was right. Sustained below
about 99% on a read-heavy workload means the machine this module suggested
has less RAM than the data actually needs.

What is deliberately *not* modelled here is write throughput. It would
legitimately inform max_wal_size, checkpoint_timeout and autovacuum
aggressiveness — but those are conf settings, this is hardware sizing, and
guessing a write rate to feed the conf would be a second layer of estimate
on top of the first. The dashboard's checkpoint panel is the honest way to
discover max_wal_size is too small.
"""

from dataclasses import dataclass, field

# Fraction of total data typically in active use. These are the softest
# numbers in pg4all — conventional starting points, not measurements.
#
# Web sits lowest because an application cache usually absorbs the hottest
# reads before they reach PostgreSQL. OLTP sits highest because the working
# set is whatever is being transacted on right now, and that tends to be a
# meaningful slice. A data warehouse cannot fit its data in memory by
# definition, so the figure there is about having room for scans and sorts
# rather than about caching the table.
_HOT_FRACTION_BY_WORKLOAD = {
    "web": 0.10,
    "oltp": 0.20,
    "mixed": 0.15,
    "dw": 0.10,
    "desktop": 0.25,
}

# Sizes real machines actually come in, so the suggestion is something you
# can order rather than a number like 47 GB.
_RAM_STEPS_GB = (2, 4, 8, 16, 32, 64, 128, 192, 256, 384, 512, 768, 1024)
_VCPU_STEPS = (2, 4, 8, 16, 32, 64, 96, 128)

_MIN_RAM_GB_BY_WORKLOAD = {"desktop": 2}
_DEFAULT_MIN_RAM_GB = 4

MAX_DATA_GB = 1_000_000
MAX_CONCURRENT_QUERIES = 10_000


@dataclass(frozen=True)
class Recommendation:
    vcpu: int
    ram_gb: int
    storage: str
    # One line per number, saying where it came from. The point is that a
    # reader can disagree with a specific step rather than the whole answer.
    notes: list[str] = field(default_factory=list)


def _round_up_to(value: float, steps) -> int:
    for step in steps:
        if value <= step:
            return step
    return steps[-1]


def recommend(
    data_gb: float, concurrent_queries: int, workload_key: str
) -> Recommendation:
    """A starting-point machine for this much data at this concurrency.

    `concurrent_queries` is queries executing at the same instant, not
    connections — an idle connection costs memory but no CPU, and it is
    concurrency that determines how many cores do any good.
    """
    data_gb = max(0.0, min(float(data_gb), MAX_DATA_GB))
    concurrent_queries = max(1, min(int(concurrent_queries), MAX_CONCURRENT_QUERIES))

    hot_fraction = _HOT_FRACTION_BY_WORKLOAD.get(workload_key, 0.15)
    hot_gb = data_gb * hot_fraction
    floor_gb = _MIN_RAM_GB_BY_WORKLOAD.get(workload_key, _DEFAULT_MIN_RAM_GB)
    ram_gb = _round_up_to(max(hot_gb, floor_gb), _RAM_STEPS_GB)

    # One core per concurrent query is the simple, defensible version:
    # a query executing is a core busy. Below that it queues.
    vcpu = _round_up_to(concurrent_queries, _VCPU_STEPS)

    notes = [
        f"Estimated hot data: {hot_gb:.0f} GB — {hot_fraction:.0%} of "
        f"{data_gb:.0f} GB, the usual starting assumption for a "
        f"{workload_key} workload. This is the softest number here; it is a "
        "convention, not a measurement of your data.",
        f"RAM {ram_gb} GB — the next real machine size at or above that hot "
        f"set, so the working set has room to stay cached"
        + (f" (floored at {floor_gb} GB)" if hot_gb < floor_gb else "")
        + ".",
        f"vCPU {vcpu} — roughly one core per concurrent query, rounded up "
        f"from {concurrent_queries}. Queries beyond that queue rather than "
        "run.",
        "Storage SSD/NVMe — on spinning disk the planner has to be told "
        "random reads are expensive, which changes the conf and usually the "
        "outcome. Pick the spinning-disk option above only if that is what "
        "you have.",
        "Check this against reality once it runs: the generated Grafana "
        "dashboard's cache hit ratio panel is what tells you whether the hot "
        "set estimate was right. Sustained below ~99% on a read-heavy "
        "workload means this machine has less RAM than your data wants.",
    ]

    return Recommendation(vcpu=vcpu, ram_gb=ram_gb, storage="ssd", notes=notes)
