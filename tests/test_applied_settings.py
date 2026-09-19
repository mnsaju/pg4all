"""Tests for app/core/applied_settings.py.

Pure unit conversion and diffing over `pg_settings`-shaped rows, so none
of this needs Docker. The units used here are the ones a real PostgreSQL
17 reports: shared_buffers in 8 kB blocks, work_mem in kB,
checkpoint_timeout in seconds, log_min_duration_statement in ms.
"""

import pytest

from app.core import applied_settings, parameters


def spec(key: str):
    return parameters.get(key)


def test_block_based_memory_converts_to_megabytes():
    # 524288 blocks x 8 kB = 4096 MB
    assert applied_settings.normalize(spec("shared_buffers"), "524288", "8kB") == 4096


def test_kilobyte_memory_converts_to_megabytes():
    assert applied_settings.normalize(spec("work_mem"), "10240", "kB") == 10


def test_megabyte_memory_passes_through():
    assert applied_settings.normalize(spec("max_wal_size"), "4096", "MB") == 4096


def test_seconds_convert_to_minutes():
    assert applied_settings.normalize(spec("checkpoint_timeout"), "900", "s") == 15


def test_dimensionless_values_pass_through():
    assert applied_settings.normalize(spec("max_connections"), "300", "") == 300
    assert applied_settings.normalize(spec("random_page_cost"), "1.1", "") == 1.1


def test_a_unit_the_tuner_already_speaks_is_not_rescaled():
    # log_min_duration_statement is declared in ms and reported in ms.
    assert applied_settings.normalize(
        spec("log_min_duration_statement"), "1000", "ms"
    ) == 1000


def test_enum_values_stay_strings():
    assert applied_settings.normalize(spec("synchronous_commit"), "off", "") == "off"


def test_a_memory_unit_on_a_time_parameter_is_rejected():
    """A wrong-dimension unit means an assumption here is broken; guessing
    would produce a comparison that looks fine and means nothing."""
    with pytest.raises(ValueError):
        applied_settings.normalize(spec("checkpoint_timeout"), "900", "8kB")


def test_matching_values_compare_equal():
    rows = [("work_mem", "10240", "kB"), ("max_connections", "300", "")]
    result = applied_settings.compare(
        parameters.PARAMETER_SPECS, {"work_mem": 10, "max_connections": 300}, rows
    )
    assert [c.key for c in result] == ["max_connections", "work_mem"]
    assert all(c.matches for c in result)
    assert applied_settings.mismatches(result) == []


def test_a_clamped_value_is_reported_as_a_mismatch():
    """The case this whole module exists for: the conf asked for 200 and
    the server came up with 0, silently."""
    rows = [("effective_io_concurrency", "0", "")]
    result = applied_settings.compare(
        parameters.PARAMETER_SPECS, {"effective_io_concurrency": 200}, rows
    )
    assert len(result) == 1
    assert not result[0].matches
    assert result[0].requested_display == "200"
    assert result[0].applied_display == "0"


def test_block_rounding_is_not_treated_as_a_mismatch():
    """PostgreSQL rounds shared_buffers to whole 8 kB blocks. Asking for
    100 MB yields 12800 blocks exactly, but a value that rounds off by a
    block must not be reported as the server ignoring the setting."""
    rows = [("shared_buffers", "12799", "8kB")]  # 99.99 MB
    result = applied_settings.compare(
        parameters.PARAMETER_SPECS, {"shared_buffers": 100}, rows
    )
    assert result[0].matches


def test_mismatches_sort_before_matches():
    rows = [
        ("work_mem", "10240", "kB"),                # matches
        ("effective_io_concurrency", "0", ""),      # differs
    ]
    result = applied_settings.compare(
        parameters.PARAMETER_SPECS,
        {"work_mem": 10, "effective_io_concurrency": 200},
        rows,
    )
    assert [c.matches for c in result] == [False, True]


def test_a_parameter_the_server_does_not_report_is_skipped():
    """A setting removed or renamed in a major version simply isn't in
    pg_settings. Skipping beats inventing a comparison against nothing."""
    result = applied_settings.compare(
        parameters.PARAMETER_SPECS,
        {"work_mem": 10, "max_connections": 300},
        [("work_mem", "10240", "kB")],
    )
    assert [c.key for c in result] == ["work_mem"]
