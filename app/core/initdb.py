"""Options fixed when the cluster is created, not in postgresql.conf.

Everything else pg4all tunes can be edited and reloaded. These cannot.
They are `context=internal` in `pg_settings` — decided by `initdb` when the
data directory is first written, and unchangeable for the life of that
cluster. Getting one wrong is not a restart away from being fixed; it is a
dump and reload away.

That is precisely why they belong in a tool that builds the image. The
official postgres entrypoint reads `POSTGRES_INITDB_ARGS`, so pg4all can
set them without giving up official images.

Page size is the one people ask for and the one that genuinely cannot be
done here: `block_size` is compile-time, set by `./configure
--with-blocksize`, so changing it means building PostgreSQL from source and
losing the official image lineage, binary compatibility with standard
tooling, and pg_upgrade between differently-built clusters — for gains
that benchmark as marginal outside narrow analytics cases.

Two version differences matter and are handled here rather than left to
whoever writes the flags:

- `--data-checksums` exists on every supported major, but the *default*
  changed: off through PostgreSQL 17, on from 18. `--no-data-checksums`
  only exists from 18, so turning checksums off on 16 or 17 means emitting
  nothing at all rather than a flag that would fail.
- `--locale=C` on its own silently drops the encoding to SQL_ASCII, which
  disables encoding validation entirely and will mangle non-ASCII text.
  Verified against the real image. C collation is therefore always paired
  with an explicit `--encoding`.
"""

CHECKSUMS_DEFAULT_ON_FROM_MAJOR = 18

DEFAULT_WAL_SEGMENT_MB = 16
WAL_SEGMENT_CHOICES = (16, 32, 64, 128)

COLLATION_IMAGE_DEFAULT = "default"
COLLATION_C = "C"
COLLATION_CHOICES = (COLLATION_IMAGE_DEFAULT, COLLATION_C)

# C collation compares byte-by-byte, so it must not be allowed to drag the
# encoding down with it.
C_COLLATION_ENCODING = "UTF8"


def _major(pg_major: str | int) -> int:
    try:
        return int(str(pg_major).split(".")[0])
    except (TypeError, ValueError):
        return CHECKSUMS_DEFAULT_ON_FROM_MAJOR


def resolve_wal_segment_mb(value) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WAL_SEGMENT_MB
    return candidate if candidate in WAL_SEGMENT_CHOICES else DEFAULT_WAL_SEGMENT_MB


def resolve_collation(value) -> str:
    return value if value in COLLATION_CHOICES else COLLATION_IMAGE_DEFAULT


def render_args(
    pg_major: str | int,
    checksums: bool = True,
    wal_segment_mb: int = DEFAULT_WAL_SEGMENT_MB,
    collation: str = COLLATION_IMAGE_DEFAULT,
) -> str:
    """The POSTGRES_INITDB_ARGS value for these choices, or "" for none."""
    args: list[str] = []

    if checksums:
        # Valid on every supported major; redundant but harmless from 18.
        args.append("--data-checksums")
    elif _major(pg_major) >= CHECKSUMS_DEFAULT_ON_FROM_MAJOR:
        # Only 18+ has a flag to turn them off; below that, off is default.
        args.append("--no-data-checksums")

    wal_segment_mb = resolve_wal_segment_mb(wal_segment_mb)
    if wal_segment_mb != DEFAULT_WAL_SEGMENT_MB:
        args.append(f"--wal-segsize={wal_segment_mb}")

    if resolve_collation(collation) == COLLATION_C:
        # Never --locale=C alone: that resolves the encoding to SQL_ASCII.
        args.append(f"--locale=C --encoding={C_COLLATION_ENCODING}")

    return " ".join(args)


def expected_settings(
    checksums: bool = True, wal_segment_mb: int = DEFAULT_WAL_SEGMENT_MB
) -> dict[str, str]:
    """What `pg_settings` should report once the cluster exists.

    These are what the smoke test holds the running server to. Unlike the
    tunable parameters, a mismatch here cannot be corrected afterwards, so
    it is worth checking the one time it can still be caught.
    """
    return {
        "data_checksums": "on" if checksums else "off",
        "wal_segment_size": str(resolve_wal_segment_mb(wal_segment_mb) * 1024 * 1024),
    }
