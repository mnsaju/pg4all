"""The machine a configuration is being generated for.

Three presets, plus any machine you actually have. The presets were never
a simplification of the tuning formulas — those only ever read vCPU, RAM
and storage type, and would have accepted any values. They were a
restriction on the *input*, which meant a 12-core, 48 GB server had to
pretend to be "large (8 vCPU / 32 GB)" and got numbers computed for
hardware it did not have: shared_buffers 8 GB instead of 12, work_mem a
third too small.

A custom machine's key encodes its shape — `12c48g`, or `12c48g-hdd` —
so it survives a round trip through a URL and reads sensibly in the image
tag it ends up in.
"""

import re
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

# Bounds are sanity limits, not recommendations: enough to reject a typo
# like 4000 cores while allowing any machine someone might really run.
MIN_VCPU, MAX_VCPU = 1, 256
MIN_RAM_GB, MAX_RAM_GB = 1, 4096
STORAGE_TYPES = ("ssd", "hdd")

_CUSTOM_KEY_RE = re.compile(r"^(\d{1,3})c(\d{1,4})g(?:-(ssd|hdd))?$")


def custom(vcpu: int, ram_gb: int, storage: str = "ssd") -> HardwareTier:
    """A tier for a machine someone actually has."""
    vcpu, ram_gb = int(vcpu), int(ram_gb)
    if not MIN_VCPU <= vcpu <= MAX_VCPU:
        raise ValueError(f"vCPU must be {MIN_VCPU}-{MAX_VCPU}, got {vcpu}")
    if not MIN_RAM_GB <= ram_gb <= MAX_RAM_GB:
        raise ValueError(f"RAM must be {MIN_RAM_GB}-{MAX_RAM_GB} GB, got {ram_gb}")
    if storage not in STORAGE_TYPES:
        raise ValueError(f"Storage must be one of {STORAGE_TYPES}, got {storage!r}")

    suffix = "" if storage == "ssd" else f"-{storage}"
    return HardwareTier(
        key=f"{vcpu}c{ram_gb}g{suffix}",
        label=f"Custom ({vcpu} vCPU / {ram_gb} GB, {storage.upper()})",
        vcpu=vcpu,
        ram_gb=ram_gb,
        storage=storage,
    )


def is_custom(tier: HardwareTier) -> bool:
    return tier.key not in _BY_KEY

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
    """A preset by name, or a custom machine from its encoded key."""
    if key in _BY_KEY:
        return _BY_KEY[key]

    match = _CUSTOM_KEY_RE.match(key or "")
    if match:
        vcpu, ram_gb, storage = match.groups()
        return custom(int(vcpu), int(ram_gb), storage or "ssd")

    raise ValueError(f"Unknown hardware tier: {key}")


def recommend_for_workload(workload_key: str) -> HardwareTier:
    tier_key = _RECOMMENDED_TIER.get(workload_key, "medium")
    return _BY_KEY[tier_key]
