"""Tests for app/core/hardware.py."""

import pytest

from app.core import hardware, workloads
from app.core.conf_generator import generate_conf


def test_the_three_presets_are_still_there():
    assert [t.key for t in hardware.TIERS] == ["small", "medium", "large"]


def test_a_custom_machine_round_trips_through_its_key():
    """The key travels in a URL and ends up in an image tag, so it has to
    be reconstructable from text alone."""
    tier = hardware.custom(12, 48)
    assert tier.key == "12c48g"
    assert hardware.get("12c48g") == tier

    spinning = hardware.custom(2, 8, "hdd")
    assert spinning.key == "2c8g-hdd"
    assert hardware.get("2c8g-hdd") == spinning


def test_a_custom_key_is_a_valid_docker_tag_component():
    import re

    for tier in (hardware.custom(12, 48), hardware.custom(2, 8, "hdd")):
        assert re.fullmatch(r"[a-zA-Z0-9._-]+", tier.key), tier.key


def test_nonsense_hardware_is_refused():
    for vcpu, ram, storage in (
        (0, 16, "ssd"), (4000, 16, "ssd"), (4, 0, "ssd"),
        (4, 99999, "ssd"), (4, 16, "tape"),
    ):
        with pytest.raises(ValueError):
            hardware.custom(vcpu, ram, storage)


def test_an_unknown_tier_key_is_refused():
    for bad in ("enormous", "", "12c", "c48g", "12x48y"):
        with pytest.raises(ValueError):
            hardware.get(bad)


def test_presets_are_not_custom_and_custom_is():
    assert not hardware.is_custom(hardware.get("medium"))
    assert hardware.is_custom(hardware.custom(12, 48))


def test_the_formulas_already_accepted_arbitrary_hardware():
    """The presets were a restriction on input, not a simplification of the
    maths — a 12/48 machine had to pretend to be large (8/32) and got
    numbers computed for hardware it did not have."""
    pretending = generate_conf(workloads.get("oltp"), hardware.get("large"))
    actual = generate_conf(workloads.get("oltp"), hardware.custom(12, 48))

    assert pretending["shared_buffers"] != actual["shared_buffers"]
    assert actual["max_worker_processes"] == "12"
    # 48 GB at the usual quarter share.
    assert actual["shared_buffers"] == f"{48 * 1024 * 1024 // 4}kB"


def test_max_wal_size_still_matches_the_table_it_replaced():
    """It was a per-tier lookup — small 1GB, medium 4GB, large 8GB — which
    turned out to be exactly RAM/4, so it became a formula that works for
    any machine. The presets must not move."""
    expected = {"small": "1GB", "medium": "4GB", "large": "8GB"}
    for tier in hardware.TIERS:
        conf = generate_conf(workloads.get("oltp"), tier)
        assert conf["max_wal_size"] == expected[tier.key], tier.key


def test_max_wal_size_is_bounded_for_extreme_machines():
    tiny = generate_conf(workloads.get("oltp"), hardware.custom(1, 1))
    huge = generate_conf(workloads.get("oltp"), hardware.custom(64, 1024))
    assert tiny["max_wal_size"] == "1GB"
    assert huge["max_wal_size"] == "16GB"


def test_storage_type_changes_the_planner_costs():
    ssd = generate_conf(workloads.get("oltp"), hardware.custom(4, 16, "ssd"))
    hdd = generate_conf(workloads.get("oltp"), hardware.custom(4, 16, "hdd"))
    assert ssd["random_page_cost"] == "1.1"
    assert hdd["random_page_cost"] == "4"
    assert int(ssd["effective_io_concurrency"]) > int(hdd["effective_io_concurrency"])
