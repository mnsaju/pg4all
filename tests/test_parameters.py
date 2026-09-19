from app.core import hardware, parameters, workloads

_RESTART_REQUIRED_KEYS = {
    "shared_buffers",
    "max_connections",
    "max_worker_processes",
    "wal_buffers",
    "autovacuum_max_workers",
}

_NO_RESTART_KEYS = {
    "work_mem",
    "effective_cache_size",
    "checkpoint_timeout",
    "synchronous_commit",
}


def test_every_parameter_pgtune_emits_is_covered():
    """pg4all once tuned seventeen parameters and PGTune emitted seventeen
    — but not the same seventeen. Five of PGTune's had been swapped for
    five of pg4all's own, and the drop went unnoticed until
    max_parallel_workers turned out to be silently capping every machine
    with more than eight cores.

    huge_pages is the one deliberate omission: PostgreSQL already defaults
    it to "try", so emitting it changes nothing.
    """
    covered = {spec.key for spec in parameters.PARAMETER_SPECS}
    pgtune_emits = {
        "max_connections", "shared_buffers", "effective_cache_size",
        "maintenance_work_mem", "checkpoint_completion_target", "wal_buffers",
        "default_statistics_target", "random_page_cost",
        "effective_io_concurrency", "work_mem", "min_wal_size", "max_wal_size",
        "max_worker_processes", "max_parallel_workers_per_gather",
        "max_parallel_workers", "max_parallel_maintenance_workers",
    }
    assert not (pgtune_emits - covered), sorted(pgtune_emits - covered)


def test_the_parallelism_chain_is_complete():
    """Three settings form a chain and all three have to be set: leaving
    max_parallel_workers out let PostgreSQL's default of 8 override both
    the others without saying so."""
    covered = {spec.key for spec in parameters.PARAMETER_SPECS}
    assert {
        "max_worker_processes",
        "max_parallel_workers",
        "max_parallel_workers_per_gather",
        "max_parallel_maintenance_workers",
    } <= covered


def test_categories_match_expected_seven_groups():
    categories = {spec.category for spec in parameters.PARAMETER_SPECS}
    assert categories == set(parameters.CATEGORY_ORDER)
    assert "durability" in categories


def test_restart_required_matches_postgresql_semantics():
    for key in _RESTART_REQUIRED_KEYS:
        assert parameters.get(key).restart_required is True

    for key in _NO_RESTART_KEYS:
        assert parameters.get(key).restart_required is False


def test_build_tuner_groups_covers_every_parameter():
    workload = workloads.get("mixed")
    tier = hardware.recommend_for_workload(workload.key)
    groups = parameters.build_tuner_groups(workload, tier)

    total_rows = sum(len(group.rows) for group in groups)
    assert total_rows == len(parameters.PARAMETER_SPECS)


def test_comparison_glyph_reflects_direction():
    workload = workloads.get("oltp")
    tier = hardware.get("medium")
    groups = parameters.build_tuner_groups(workload, tier)

    rows_by_key = {row.spec.key: row for group in groups for row in group.rows}
    shared_buffers_row = rows_by_key["shared_buffers"]
    assert shared_buffers_row.recommended_value > shared_buffers_row.default_value
    assert shared_buffers_row.comparison == "→"  # recommended is higher


def test_synchronous_commit_never_recommended_off():
    for workload_key in ("web", "oltp", "dw", "mixed", "desktop"):
        workload = workloads.get(workload_key)
        tier = hardware.recommend_for_workload(workload_key)
        groups = parameters.build_tuner_groups(workload, tier)
        rows_by_key = {row.spec.key: row for group in groups for row in group.rows}

        row = rows_by_key["synchronous_commit"]
        assert row.recommended_value == "on"
        assert row.comparison == "="  # never a silent durability trade-off


def test_checkpoint_timeout_formats_conf_value_in_minutes():
    spec = parameters.get("checkpoint_timeout")
    assert parameters.format_conf_value(spec, 20) == "20min"
    assert parameters.parse_value(spec, "20min") == 20.0
