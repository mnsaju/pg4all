"""Image tag naming.

Tags used to be derived from version, workload and tier alone —
`pg4all/postgres:17-oltp-medium`. That names a *category*, while a build
is an *event*, and the two are not one-to-one: every build of the same
combination silently took the tag from the one before it.

The damage was not theoretical. On the machine this was found on, 11 build
records shared 3 tags, nine of them on one tag, covering four genuinely
different configurations. A build that had selected `pg_stat_statements`
still had `shared_preload_libraries` in its own generated conf and its own
`init-extensions.sql` on disk — but the tag its compose file names had
long since moved to an image built without either. Running that build's
stack started a database with the extension simply absent, no error
anywhere.

So there are two tags, which is the same split Docker has always used for
releases and `latest`:

- a **build tag**, carrying the build id, which never moves. Generated
  compose files and run instructions use this one, so a build's stack
  keeps starting the image that build actually produced and the smoke test
  actually checked.
- a **series tag**, the old short name, which follows the newest build of
  that combination. Convenient for "give me the current oltp-medium", and
  safe precisely because nothing generated depends on it.

Extra tags cost effectively nothing: an identical build context produces
an identical image, so a second tag is another pointer to the same layers.
"""

# Enough of the build id to be unique in practice without making the tag
# unreadable. Collisions are checked for at build time rather than assumed
# away — see main.py.
BUILD_ID_TAG_LENGTH = 8

REPOSITORY = "pg4all/postgres"


def series_tag(pg_major: str, workload_key: str, tier_key: str) -> str:
    """The moving tag: the newest build of this combination."""
    return f"{REPOSITORY}:{pg_major}-{workload_key}-{tier_key}"


def build_tag(pg_major: str, workload_key: str, tier_key: str, build_id: str) -> str:
    """The immutable tag for one specific build."""
    suffix = build_id[:BUILD_ID_TAG_LENGTH]
    return f"{series_tag(pg_major, workload_key, tier_key)}-{suffix}"


def is_build_tag(tag: str) -> bool:
    """Whether a tag carries a build id suffix.

    Records made before this existed carry a bare series tag, which is
    shared and therefore unsafe to remove on delete.
    """
    _, _, version = (tag or "").partition(":")
    parts = version.split("-")
    if len(parts) < 4:
        return False
    suffix = parts[-1]
    return len(suffix) == BUILD_ID_TAG_LENGTH and all(
        c in "0123456789abcdef" for c in suffix
    )
