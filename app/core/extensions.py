"""PostgreSQL extension registry for the pre-build selection step.

Two sources:
- "contrib": ships inside the official postgres:XX-bookworm image already
  (compiled and installed, just needs `CREATE EXTENSION`).
- "apt": not in the base image, but installable via `apt-get` because the
  official image already has the PGDG repo (apt.postgresql.org) configured
  — adds a real image layer, so these default off.

`pg_stat_statements` is the only entry selected by default: it's the one
extension that's close to a universal recommendation for any Postgres
instance, not a workload-specific choice like the rest.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtensionSpec:
    key: str  # SQL extension name, e.g. "pg_stat_statements"
    label: str
    description: str
    source: str  # "contrib" | "apt"
    apt_package: str | None  # "{major}"-templated, only set when source == "apt"
    requires_preload: bool  # needs shared_preload_libraries, not just CREATE EXTENSION
    default_selected: bool
    risk: str  # "low" | "medium" — same vocabulary as parameters.py's risk badges


EXTENSIONS: list[ExtensionSpec] = [
    ExtensionSpec(
        "pg_stat_statements", "pg_stat_statements",
        "Tracks execution statistics for every query the server runs. "
        "The closest thing to a universal recommendation for any Postgres "
        "instance, not a workload-specific choice.",
        source="contrib", apt_package=None,
        requires_preload=True, default_selected=True, risk="low",
    ),
    ExtensionSpec(
        "pgcrypto", "pgcrypto",
        "Hashing and encryption functions; also provides gen_random_uuid() "
        "on PostgreSQL versions before 16, where it moved into core.",
        source="contrib", apt_package=None,
        requires_preload=False, default_selected=False, risk="low",
    ),
    ExtensionSpec(
        "pg_trgm", "pg_trgm",
        "Trigram-based text similarity, makes LIKE '%x%' and fuzzy-match "
        "queries indexable.",
        source="contrib", apt_package=None,
        requires_preload=False, default_selected=False, risk="low",
    ),
    ExtensionSpec(
        "btree_gin", "btree_gin",
        "Lets GIN indexes cover ordinary scalar types, for combining with "
        "other GIN-indexed columns.",
        source="contrib", apt_package=None,
        requires_preload=False, default_selected=False, risk="low",
    ),
    ExtensionSpec(
        "btree_gist", "btree_gist",
        "Lets GiST indexes cover ordinary scalar types; required for "
        "exclusion constraints over those types.",
        source="contrib", apt_package=None,
        requires_preload=False, default_selected=False, risk="low",
    ),
    ExtensionSpec(
        "uuid-ossp", "uuid-ossp",
        "Legacy UUID-generation functions. Superseded by core's own "
        "gen_random_uuid() on PostgreSQL 16+, but still commonly requested "
        "by name.",
        source="contrib", apt_package=None,
        requires_preload=False, default_selected=False, risk="low",
    ),
    ExtensionSpec(
        "citext", "citext",
        "A case-insensitive text type, for columns like email addresses "
        "without hand-rolled lower() comparisons.",
        source="contrib", apt_package=None,
        requires_preload=False, default_selected=False, risk="low",
    ),
    ExtensionSpec(
        "postgres_fdw", "postgres_fdw",
        "Foreign data wrapper for querying another PostgreSQL database "
        "directly from this one.",
        source="contrib", apt_package=None,
        requires_preload=False, default_selected=False, risk="low",
    ),
    ExtensionSpec(
        "vector", "pgvector",
        "Vector similarity search and storage. Not in the base image — "
        "adds an apt-get layer at build time.",
        source="apt", apt_package="postgresql-{major}-pgvector",
        requires_preload=False, default_selected=False, risk="medium",
    ),
    ExtensionSpec(
        "pg_cron", "pg_cron",
        "In-database cron-style job scheduler. Not in the base image — "
        "adds an apt-get layer, and needs shared_preload_libraries plus a "
        "cron.database_name setting (set to 'postgres' here) to run.",
        source="apt", apt_package="postgresql-{major}-cron",
        requires_preload=True, default_selected=False, risk="medium",
    ),
]

_BY_KEY = {e.key: e for e in EXTENSIONS}


def list_extensions() -> list[ExtensionSpec]:
    return EXTENSIONS


def get(key: str) -> ExtensionSpec:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"Unknown extension: {key}")


def resolve(keys: list[str]) -> list[ExtensionSpec]:
    """Validates a raw list of extension keys (e.g. from a submitted form),
    silently dropping anything unrecognized rather than erroring — same
    defensive pattern as main.py's _resolve_selection."""
    seen = dict.fromkeys(keys)  # de-dupe, preserve order
    return [_BY_KEY[k] for k in seen if k in _BY_KEY]


def preload_libraries(selected: list[ExtensionSpec]) -> list[str]:
    return sorted({e.key for e in selected if e.requires_preload})


def extra_conf_settings(selected: list[ExtensionSpec]) -> dict[str, str]:
    """Extra postgresql.conf settings an extension needs beyond
    CREATE EXTENSION and shared_preload_libraries. Currently only
    pg_cron, which needs a target database named."""
    settings: dict[str, str] = {}
    if any(e.key == "pg_cron" for e in selected):
        settings["cron.database_name"] = "'postgres'"
    return settings


def apt_packages(selected: list[ExtensionSpec], major: str) -> list[str]:
    return [
        e.apt_package.format(major=major)
        for e in selected
        if e.source == "apt" and e.apt_package
    ]


def render_init_sql(selected: list[ExtensionSpec]) -> str:
    if not selected:
        return ""
    lines = [
        "-- Generated by pg4all — extensions selected before build.",
        "",
    ]
    lines += [f'CREATE EXTENSION IF NOT EXISTS "{e.key}";' for e in selected]
    return "\n".join(lines) + "\n"
