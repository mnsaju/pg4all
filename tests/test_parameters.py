from app.core import hardware, parameters, workloads

_RESTART_REQUIRED_KEYS = {
    "shared_buffers",
    "max_connections",
    "max_worker_processes",
    "wal_buffers",
    "autovacuum_max_workers",
}

_NO_RESTART_KEYS = {"work_mem", "effective_cache_size"}


def test_total_parameter_count_is_fifteen():
    assert len(parameters.PARAMETER_SPECS) == 15


def test_categories_match_expected_six_groups():
    categories = {spec.category for spec in parameters.PARAMETER_SPECS}
    assert categories == set(parameters.CATEGORY_ORDER)


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
