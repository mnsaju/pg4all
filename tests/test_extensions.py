from app.core import extensions


def test_extension_keys_are_unique():
    keys = [e.key for e in extensions.EXTENSIONS]
    assert len(keys) == len(set(keys))


def test_resolve_drops_unknown_keys():
    resolved = extensions.resolve(["pg_stat_statements", "not-a-real-extension"])
    assert [e.key for e in resolved] == ["pg_stat_statements"]


def test_resolve_dedupes_and_preserves_order():
    resolved = extensions.resolve(["pg_trgm", "pgcrypto", "pg_trgm"])
    assert [e.key for e in resolved] == ["pg_trgm", "pgcrypto"]


def test_apt_packages_formats_major_version():
    selected = extensions.resolve(["vector", "pg_cron"])
    packages = extensions.apt_packages(selected, "17")
    assert packages == ["postgresql-17-pgvector", "postgresql-17-cron"]


def test_apt_packages_excludes_contrib_extensions():
    selected = extensions.resolve(["pg_trgm"])
    assert extensions.apt_packages(selected, "17") == []


def test_preload_libraries_default_selection_is_pg_stat_statements():
    default_selected = [e for e in extensions.EXTENSIONS if e.default_selected]
    assert extensions.preload_libraries(default_selected) == ["pg_stat_statements"]


def test_preload_libraries_empty_when_none_require_it():
    selected = extensions.resolve(["pgcrypto", "pg_trgm"])
    assert extensions.preload_libraries(selected) == []


def test_extra_conf_settings_adds_cron_database_for_pg_cron():
    selected = extensions.resolve(["pg_cron"])
    assert extensions.extra_conf_settings(selected) == {"cron.database_name": "'postgres'"}


def test_extra_conf_settings_empty_without_pg_cron():
    selected = extensions.resolve(["pgcrypto"])
    assert extensions.extra_conf_settings(selected) == {}


def test_render_init_sql_quotes_hyphenated_extension_name():
    selected = extensions.resolve(["uuid-ossp"])
    sql = extensions.render_init_sql(selected)
    assert 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";' in sql


def test_render_init_sql_one_line_per_extension():
    selected = extensions.resolve(["pg_stat_statements", "pgcrypto"])
    sql = extensions.render_init_sql(selected)
    assert sql.count("CREATE EXTENSION IF NOT EXISTS") == 2


def test_render_init_sql_empty_for_no_selection():
    assert extensions.render_init_sql([]) == ""
