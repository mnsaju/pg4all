"""Supported PostgreSQL major versions.

Hardcoded on purpose for v1 — there is no automated feed for "latest 3
official releases" yet. Update this list by hand when a new major or
minor ships: https://www.postgresql.org/support/versioning/

Each entry pins the latest official *minor* release for that major —
per project policy, pg4all always ships the latest official delivery,
so there's no separate sub-version picker; picking a major always means
picking its current latest minor too.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PgVersion:
    major: str
    full_version: str  # e.g. "18.6" — latest official minor for this major
    docker_tag: str  # Debian-slim variant, e.g. "18.6-bookworm"


# Last checked against https://www.postgresql.org/ 2026-08-13 (18.6/17.11/16.15 release).
SUPPORTED_VERSIONS: list[PgVersion] = [
    PgVersion("18", "18.6", "18.6-bookworm"),
    PgVersion("17", "17.11", "17.11-bookworm"),
    PgVersion("16", "16.15", "16.15-bookworm"),
]

_BY_MAJOR = {v.major: v for v in SUPPORTED_VERSIONS}


def list_versions() -> list[PgVersion]:
    return SUPPORTED_VERSIONS


def get(major_version: str) -> PgVersion:
    try:
        return _BY_MAJOR[major_version]
    except KeyError:
        raise ValueError(f"Unsupported PostgreSQL version: {major_version}")


def docker_tag_for(major_version: str) -> str:
    return get(major_version).docker_tag
