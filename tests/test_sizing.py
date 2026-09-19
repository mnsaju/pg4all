"""Tests for app/core/sizing.py.

These pin behaviour and the shape of the reasoning, not the conventions
themselves — the hot-fraction figures are starting points someone may well
want to argue with, and a test asserting 20% is correct would be asserting
something this module explicitly does not claim.
"""

from app.core import sizing


def test_more_data_needs_more_memory():
    small = sizing.recommend(50, 10, "oltp")
    large = sizing.recommend(5000, 10, "oltp")
    assert large.ram_gb > small.ram_gb


def test_more_concurrency_needs_more_cores_but_not_more_memory():
    """Concurrency is a CPU question; data volume is a memory question."""
    quiet = sizing.recommend(500, 4, "oltp")
    busy = sizing.recommend(500, 64, "oltp")
    assert busy.vcpu > quiet.vcpu
    assert busy.ram_gb == quiet.ram_gb


def test_the_workload_changes_how_much_is_assumed_hot():
    """A web workload usually has an application cache in front of it, so
    less reaches PostgreSQL than for raw OLTP."""
    web = sizing.recommend(1000, 10, "web")
    oltp = sizing.recommend(1000, 10, "oltp")
    assert oltp.ram_gb > web.ram_gb


def test_suggestions_are_sizes_machines_actually_come_in():
    for data in (1, 37, 250, 999, 4096):
        rec = sizing.recommend(data, 17, "mixed")
        assert rec.ram_gb in sizing._RAM_STEPS_GB, rec.ram_gb
        assert rec.vcpu in sizing._VCPU_STEPS, rec.vcpu


def test_a_tiny_database_still_gets_a_usable_floor():
    rec = sizing.recommend(1, 1, "oltp")
    assert rec.ram_gb >= 4
    assert rec.vcpu >= 2


def test_absurd_input_is_clamped_rather_than_exploding():
    for data, concurrent in ((-5, -5), (10**12, 10**9), (0, 0)):
        rec = sizing.recommend(data, concurrent, "oltp")
        assert rec.ram_gb in sizing._RAM_STEPS_GB
        assert rec.vcpu in sizing._VCPU_STEPS


def test_every_number_comes_with_its_reasoning():
    """The whole contract of this module: it cannot point at a derivation
    the way the tuning formulas can, so it shows its working instead."""
    rec = sizing.recommend(500, 20, "oltp")
    joined = " ".join(rec.notes)

    assert str(rec.ram_gb) in joined
    assert str(rec.vcpu) in joined
    assert "hot" in joined.lower()
    # It has to say out loud that the softest step is an assumption.
    assert "convention" in joined or "assumption" in joined
    # And point at the measurement that would disprove it.
    assert "cache hit ratio" in joined


def test_an_unknown_workload_falls_back_instead_of_failing():
    rec = sizing.recommend(500, 20, "not-a-workload")
    assert rec.ram_gb > 0 and rec.vcpu > 0
