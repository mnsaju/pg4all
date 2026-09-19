"""Tests for app/core/initdb.py.

These settings are context=internal — fixed when the data directory is
created and unchangeable for the life of the cluster — so the version
differences and the encoding trap below are not cosmetic. Getting one
wrong is a dump and reload, not a restart.
"""

from app.core import initdb


def test_checksums_are_requested_the_same_way_on_every_major():
    for major in ("16", "17", "18"):
        assert "--data-checksums" in initdb.render_args(major, checksums=True), major


def test_turning_checksums_off_respects_when_the_default_changed():
    """PostgreSQL defaults them off through 17 and on from 18, and
    --no-data-checksums only exists from 18. Below that the flag does not
    exist, so 'off' means emitting nothing rather than something that would
    fail."""
    for legacy in ("16", "17"):
        assert initdb.render_args(legacy, checksums=False) == "", legacy
    assert initdb.render_args("18", checksums=False) == "--no-data-checksums"


def test_c_collation_always_carries_an_explicit_encoding():
    """--locale=C on its own resolves the encoding to SQL_ASCII, which
    turns off encoding validation entirely and will mangle non-ASCII text.
    Verified against the real image, so this pairing is not optional."""
    args = initdb.render_args("17", collation="C")
    assert "--locale=C" in args
    assert "--encoding=UTF8" in args


def test_the_default_locale_is_left_alone():
    args = initdb.render_args("17", collation="default")
    assert "--locale" not in args
    assert "--encoding" not in args


def test_the_default_wal_segment_size_adds_no_flag():
    args = initdb.render_args("17", wal_segment_mb=initdb.DEFAULT_WAL_SEGMENT_MB)
    assert "--wal-segsize" not in args


def test_a_non_default_wal_segment_size_is_passed():
    assert "--wal-segsize=64" in initdb.render_args("17", wal_segment_mb=64)


def test_unsupported_choices_fall_back_rather_than_being_passed_through():
    """These end up in a shell-ish env var consumed by initdb, so nothing
    unvalidated should reach it."""
    assert initdb.resolve_wal_segment_mb("banana") == initdb.DEFAULT_WAL_SEGMENT_MB
    assert initdb.resolve_wal_segment_mb(7) == initdb.DEFAULT_WAL_SEGMENT_MB
    assert initdb.resolve_wal_segment_mb(None) == initdb.DEFAULT_WAL_SEGMENT_MB
    assert initdb.resolve_collation("klingon") == initdb.COLLATION_IMAGE_DEFAULT
    assert "banana" not in initdb.render_args("17", wal_segment_mb="banana")
    assert "klingon" not in initdb.render_args("17", collation="klingon")


def test_defaults_turn_checksums_on_and_change_nothing_else():
    """The point of the default: a PostgreSQL 16 or 17 image otherwise has
    no corruption detection at all, while an 18 one does."""
    assert initdb.render_args("17") == "--data-checksums"


def test_expected_settings_describe_what_the_server_should_report():
    assert initdb.expected_settings(True, 16) == {
        "data_checksums": "on",
        "wal_segment_size": str(16 * 1024 * 1024),
    }
    assert initdb.expected_settings(False, 64)["data_checksums"] == "off"
    assert initdb.expected_settings(True, 64)["wal_segment_size"] == str(64 * 1024 * 1024)


def test_an_unparseable_major_is_treated_as_current():
    assert initdb.render_args("nonsense", checksums=False) == "--no-data-checksums"
