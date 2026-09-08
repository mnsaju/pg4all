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


def test_total_parameter_count_is_seventeen():
    assert len(parameters.PARAMETER_SPECS) == 17


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
