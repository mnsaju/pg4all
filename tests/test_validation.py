"""Tests for app/core/validation.py.

Pure arithmetic over a parameter set, so these need no Docker and no
FastAPI. Each test starts from the recommended set for a workload/tier
pair — which should itself be clean — and breaks exactly one thing, so a
finding can only come from the parameter under test.
"""

import pytest

from app.core import hardware, parameters, validation, workloads


def recommended_values(workload_key: str, tier_key: str) -> dict[str, float | str]:
    workload = workloads.get(workload_key)
    tier = hardware.get(tier_key)
    groups = parameters.build_tuner_groups(workload, tier)
    return {row.spec.key: row.recommended_value for group in groups for row in group.rows}


def summaries(findings) -> str:
    return " | ".join(f.summary for f in findings)


@pytest.mark.parametrize("workload", [w.key for w in workloads.WORKLOADS])
@pytest.mark.parametrize("tier", [t.key for t in hardware.TIERS])
def test_every_recommended_preset_is_error_free(workload, tier):
    """No preset the product recommends may describe an unrunnable server.

    The regression test that matters most: every workload × tier pair, so
    a future change to a tuning formula that pushes a preset past its own
    memory budget fails here instead of at 3am on someone's host.

    Errors only, not warnings — the presets do legitimately warn (see the
    two tests below). Errors are the line they may never cross.
    """
    findings = validation.validate(
        recommended_values(workload, tier), hardware.get(tier)
    )
    assert not validation.has_errors(findings), summaries(findings)


@pytest.mark.parametrize("tier", [t.key for t in hardware.TIERS])
def test_data_warehouse_presets_stay_under_the_memory_warn_line(tier):
    """The DW profile is the most memory-hungry thing pg4all ships.

    It budgets work_mem from (RAM × 0.75 − shared_buffers), so it runs
    close to the line by design and is the preset most likely to cross it
    if a formula changes. Pinning it here means the warn threshold and the
    tuning formulas can't drift apart silently.
    """
    findings = validation.validate(recommended_values("dw", tier), hardware.get(tier))
    assert "of the tier's RAM" not in summaries(findings)


@pytest.mark.parametrize("tier", [t.key for t in hardware.TIERS])
def test_oltp_presets_advise_a_pooler(tier):
    """PGTune's OLTP profile recommends 300 connections, and pg4all keeps
    that — but 300 backends with nothing in front of them is exactly the
    case the pooler advice exists for. Firing on our own default is
    intended here: PgBouncer is a checkbox on the same page."""
    findings = validation.validate(recommended_values("oltp", tier), hardware.get(tier))
    assert "no pooler selected" in summaries(findings)


def test_overcommitted_memory_is_an_error():
    tier = hardware.get("small")  # 4 GB
    values = recommended_values("oltp", "small")
    values["max_connections"] = 500
    values["work_mem"] = 64  # 500 × 64 MB = 31 GB on a 4 GB box

    findings = validation.validate(values, tier)

    assert validation.has_errors(findings)
    error = findings[0]
    assert error.level == validation.ERROR
    assert "exceeds the tier's RAM" in error.summary
    assert "work_mem" in error.parameter_keys


def test_memory_between_the_warn_threshold_and_ram_warns_without_blocking():
    tier = hardware.get("medium")  # 16 GB
    values = recommended_values("oltp", "medium")
    # Target ~97% of 16 GB: over the warn line, under the hard limit.
    values["shared_buffers"] = 4096
    values["wal_buffers"] = 16
    values["max_connections"] = 100
    values["work_mem"] = 110  # 10.7 GB
    values["autovacuum_max_workers"] = 3
    values["maintenance_work_mem"] = 256  # 0.75 GB

    findings = validation.validate(values, tier)

    assert not validation.has_errors(findings)
    assert "% of the tier's RAM" in summaries(findings)


def test_shared_buffers_over_forty_percent_of_ram_warns():
    tier = hardware.get("medium")
    values = recommended_values("oltp", "medium")
    values["shared_buffers"] = 8192  # 50% of 16 GB
    values["effective_cache_size"] = 12288  # keep the cache rule quiet

    findings = validation.validate(values, tier)

    assert "shared_buffers is 50% of RAM" in summaries(findings)


def test_effective_cache_size_below_shared_buffers_warns():
    tier = hardware.get("medium")
    values = recommended_values("oltp", "medium")
    values["effective_cache_size"] = values["shared_buffers"] - 256

    findings = validation.validate(values, tier)

    assert "below shared_buffers" in summaries(findings)


def test_effective_cache_size_above_ram_warns():
    tier = hardware.get("medium")  # 16 GB
    values = recommended_values("oltp", "medium")
    values["effective_cache_size"] = 24576

    findings = validation.validate(values, tier)

    assert "exceeds the tier's RAM" in summaries(findings)


def test_a_gather_asking_for_more_than_the_cluster_ceiling_warns():
    """The bug this rule missed for a while: it compared per_gather against
    max_worker_processes, the outer limit, so a 32-core machine asking for
    16 per Gather passed cleanly while max_parallel_workers sat at 8."""
    tier = hardware.get("medium")
    values = recommended_values("oltp", "medium")
    values["max_worker_processes"] = 32
    values["max_parallel_workers"] = 8
    values["max_parallel_workers_per_gather"] = 16

    findings = validation.validate(values, tier)

    assert "above max_parallel_workers" in summaries(findings)


def test_more_parallel_workers_than_the_pool_warns():
    tier = hardware.get("medium")
    values = recommended_values("oltp", "medium")
    values["max_worker_processes"] = 4
    values["max_parallel_workers"] = 16
    values["max_parallel_workers_per_gather"] = 2

    findings = validation.validate(values, tier)

    assert "max_parallel_workers is above max_worker_processes" in summaries(findings)


def test_maintenance_workers_above_the_ceiling_warns():
    tier = hardware.get("medium")
    values = recommended_values("oltp", "medium")
    values["max_parallel_workers"] = 4
    values["max_parallel_maintenance_workers"] = 8

    findings = validation.validate(values, tier)

    assert "max_parallel_maintenance_workers is above" in summaries(findings)


def test_a_consistent_parallelism_chain_is_quiet():
    tier = hardware.get("medium")
    values = recommended_values("oltp", "medium")
    values["max_worker_processes"] = 16
    values["max_parallel_workers"] = 16
    values["max_parallel_workers_per_gather"] = 8
    values["max_parallel_maintenance_workers"] = 4

    findings = validation.validate(values, tier)

    assert "parallel" not in summaries(findings).lower()


def test_many_connections_without_pgbouncer_warns():
    tier = hardware.get("large")  # 32 GB, so the memory rules stay quiet
    values = recommended_values("oltp", "large")
    values["max_connections"] = 400
    values["work_mem"] = 8

    findings = validation.validate(values, tier)

    assert "no pooler selected" in summaries(findings)


def test_many_connections_with_pgbouncer_selected_does_not_warn():
    tier = hardware.get("large")
    values = recommended_values("oltp", "large")
    values["max_connections"] = 400
    values["work_mem"] = 8

    findings = validation.validate(values, tier, frozenset({"pgbouncer"}))

    assert "no pooler selected" not in summaries(findings)


def test_synchronous_commit_off_warns():
    tier = hardware.get("medium")
    values = recommended_values("oltp", "medium")
    values["synchronous_commit"] = "off"

    findings = validation.validate(values, tier)

    assert "synchronous_commit is off" in summaries(findings)


def test_errors_sort_ahead_of_warnings():
    tier = hardware.get("small")
    values = recommended_values("oltp", "small")
    values["max_connections"] = 500
    values["work_mem"] = 64          # error: over RAM
    values["synchronous_commit"] = "off"  # warning

    findings = validation.validate(values, tier)

    levels = [f.level for f in findings]
    assert levels[0] == validation.ERROR
    assert validation.WARNING in levels
    assert levels == sorted(levels, key=lambda lv: 0 if lv == validation.ERROR else 1)
