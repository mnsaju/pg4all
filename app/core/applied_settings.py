"""Compare what pg4all asked for against what PostgreSQL actually applied.

Writing a value into postgresql.conf does not mean the running server uses
it. PostgreSQL rounds memory settings to whole blocks, caps some values at
a compile- or platform-determined maximum, and ignores others outright
depending on how it was built — all silently, at startup, with the
requested value still sitting in the file. The only way to know a conf did
what it says is to ask a running server what it ended up with.

This module is the pure half of that: it turns `pg_settings` rows into the
units the tuner speaks and diffs them. Actually starting a container to
produce those rows is `app/builder/smoke_test.py`.

`pg_settings.setting` is always a bare number (or a string, for enums)
expressed in `pg_settings.unit` — "8kB" for shared_buffers, "kB" for
work_mem, "s" for checkpoint_timeout, empty for dimensionless ones. The
tuner works in megabytes and minutes, so the two have to be reconciled
before any comparison means anything.
"""

import math
import re
from dataclasses import dataclass

from app.core.parameters import ParameterSpec, format_display

# pg_settings.unit is an optional integer multiplier followed by a unit
# token, e.g. "8kB" (blocks of 8 kB) or "min".
_UNIT_RE = re.compile(r"^(\d*)\s*([a-zA-Z]+)$")

_MEMORY_UNITS_IN_MB = {
    "B": 1 / (1024 * 1024),
    "kB": 1 / 1024,
    "MB": 1.0,
    "GB": 1024.0,
    "TB": 1024.0 * 1024.0,
}

_TIME_UNITS_IN_MINUTES = {
    "us": 1 / 60_000_000,
    "ms": 1 / 60_000,
    "s": 1 / 60,
    "min": 1.0,
    "h": 60.0,
    "d": 1440.0,
}

# Memory settings are rounded to whole blocks and time settings to whole
# units, so an exact match is the wrong test. This is loose enough to
# absorb that rounding and far too tight to hide a clamp, which is what
# this comparison exists to catch (effective_io_concurrency dropping from
# 200 to 0, say, not shared_buffers moving by one 8 kB block).
_RELATIVE_TOLERANCE = 0.01


@dataclass(frozen=True)
class SettingComparison:
    key: str
    requested: float | str
    applied: float | str
    requested_display: str
    applied_display: str
    matches: bool


def _parse_unit(unit: str) -> tuple[float, str]:
    """Split a pg_settings unit into its multiplier and token."""
    match = _UNIT_RE.match(unit.strip())
    if not match:
        raise ValueError(f"Unrecognised pg_settings unit: {unit!r}")
    multiplier, token = match.groups()
    return (float(multiplier) if multiplier else 1.0), token


def normalize(spec: ParameterSpec, setting: str, unit: str) -> float | str:
    """Convert one pg_settings row into the spec's own unit."""
    if spec.kind == "enum":
        return setting.strip()

    value = float(setting)
    if not unit.strip():
        return value

    multiplier, token = _parse_unit(unit)
    scaled = value * multiplier

    if spec.kind == "memory_mb":
        if token not in _MEMORY_UNITS_IN_MB:
            raise ValueError(f"{spec.key}: expected a memory unit, got {unit!r}")
        return scaled * _MEMORY_UNITS_IN_MB[token]

    if spec.kind == "minutes":
        if token not in _TIME_UNITS_IN_MINUTES:
            raise ValueError(f"{spec.key}: expected a time unit, got {unit!r}")
        return scaled * _TIME_UNITS_IN_MINUTES[token]

    # Dimensionless in the tuner's terms but carrying a unit in PostgreSQL's
    # — log_min_duration_statement is the one case, and its spec is already
    # written in the same unit (ms), so the scaled number is what we want.
    return scaled


def _matches(requested: float | str, applied: float | str) -> bool:
    if isinstance(requested, str) or isinstance(applied, str):
        return str(requested) == str(applied)
    return math.isclose(requested, applied, rel_tol=_RELATIVE_TOLERANCE, abs_tol=1e-9)


def compare(
    specs: list[ParameterSpec],
    requested: dict[str, float | str],
    rows: list[tuple[str, str, str]],
) -> list[SettingComparison]:
    """Diff requested values against `(name, setting, unit)` rows.

    Only parameters present in both are compared. A parameter the server
    doesn't report is skipped rather than guessed at — that happens when a
    setting is removed or renamed in a major version, which is a real thing
    to notice but not something this function can describe on its own.
    Mismatches sort first.
    """
    by_name = {name: (setting, unit) for name, setting, unit in rows}

    comparisons = []
    for spec in specs:
        if spec.key not in by_name or spec.key not in requested:
            continue
        setting, unit = by_name[spec.key]
        applied = normalize(spec, setting, unit)
        requested_value = requested[spec.key]
        comparisons.append(
            SettingComparison(
                key=spec.key,
                requested=requested_value,
                applied=applied,
                requested_display=format_display(spec, requested_value),
                applied_display=format_display(spec, applied),
                matches=_matches(requested_value, applied),
            )
        )

    return sorted(comparisons, key=lambda c: (c.matches, c.key))


def mismatches(comparisons: list[SettingComparison]) -> list[SettingComparison]:
    return [c for c in comparisons if not c.matches]
