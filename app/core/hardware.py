"""Hardware tiers.

Deliberately coarse: small/medium/large presets, not a precise sizing
calculator. There is no data-volume or throughput input yet to justify
more precision than that — see the recommendation in project discussion.
The user confirms or overrides the preset before it feeds conf_generator.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareTier:
    key: str
    label: str
    vcpu: int
    ram_gb: int
    storage: str  # "ssd" or "hdd"


TIERS: list[HardwareTier] = [
    HardwareTier("small", "Small (2 vCPU / 4 GB)", vcpu=2, ram_gb=4, storage="ssd"),
    HardwareTier("medium", "Medium (4 vCPU / 16 GB)", vcpu=4, ram_gb=16, storage="ssd"),
    HardwareTier("large", "Large (8 vCPU / 32 GB)", vcpu=8, ram_gb=32, storage="ssd"),
]

_BY_KEY = {t.key: t for t in TIERS}

# Default tier recommended per workload — a starting point, not a
# guarantee; the user can always pick a different tier before generating
# the conf.
_RECOMMENDED_TIER = {
    "web": "medium",
    "oltp": "medium",
    "dw": "large",
    "mixed": "medium",
    "desktop": "small",
}


def get(key: str) -> HardwareTier:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"Unknown hardware tier: {key}")


def recommend_for_workload(workload_key: str) -> HardwareTier:
    tier_key = _RECOMMENDED_TIER.get(workload_key, "medium")
    return _BY_KEY[tier_key]
