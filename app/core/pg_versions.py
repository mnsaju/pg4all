"""Supported PostgreSQL major versions.

Hardcoded on purpose for v1 — there is no automated feed for "latest 3
official releases" yet. Update this list by hand when a new major ships:
https://www.postgresql.org/support/versioning/
"""

# (major_version, docker_hub_tag) — tag pins the Debian-slim variant.
SUPPORTED_VERSIONS = [
    ("17", "17-bookworm"),
    ("16", "16-bookworm"),
    ("15", "15-bookworm"),
]


def list_versions() -> list[str]:
    return [major for major, _tag in SUPPORTED_VERSIONS]


def docker_tag_for(major_version: str) -> str:
    for major, tag in SUPPORTED_VERSIONS:
        if major == major_version:
            return tag
    raise ValueError(f"Unsupported PostgreSQL version: {major_version}")
