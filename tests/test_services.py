from app.core import services


def test_service_keys_are_unique():
    keys = [s.key for s in services.SERVICES]
    assert len(keys) == len(set(keys))


def test_resolve_drops_unknown_keys():
    resolved = services.resolve(["pgbouncer", "not-a-real-service"])
    assert [s.key for s in resolved] == ["pgbouncer"]


def test_resolve_dedupes_and_preserves_order():
    resolved = services.resolve(["postgres_exporter", "pgbouncer", "postgres_exporter"])
    assert [s.key for s in resolved] == ["postgres_exporter", "pgbouncer"]


def test_apt_packages_only_includes_apt_mode_services():
    selected = services.resolve(["pgbouncer", "pgbackrest", "postgres_exporter"])
    assert services.apt_packages(selected) == ["pgbackrest"]


def test_apt_packages_empty_without_pgbackrest():
    selected = services.resolve(["pgbouncer", "postgres_exporter"])
    assert services.apt_packages(selected) == []


def test_render_pgbackrest_conf_has_stanza_and_repo_path():
    conf = services.render_pgbackrest_conf("17")
    assert "repo1-path=/var/lib/pgbackrest" in conf
    assert "[main]" in conf
    assert "pg1-path=/var/lib/postgresql/data" in conf


def test_compose_fragment_pgbouncer_has_credentials_and_no_exporter_settings():
    spec = services.get("pgbouncer")
    fragment = services.compose_fragment(spec, "postgres", "s3cret")
    assert "DB_USER: postgres" in fragment
    assert "DB_PASSWORD: 's3cret'" in fragment
    assert "DATA_SOURCE_URI" not in fragment


def test_compose_fragment_postgres_exporter_has_credentials_and_no_pgbouncer_settings():
    spec = services.get("postgres_exporter")
    fragment = services.compose_fragment(spec, "postgres", "s3cret")
    assert "DATA_SOURCE_USER: postgres" in fragment
    assert "DATA_SOURCE_PASS: 's3cret'" in fragment
    assert "DB_HOST" not in fragment


def test_compose_fragment_is_not_defined_for_apt_mode_service():
    spec = services.get("pgbackrest")
    assert spec.key not in services._FRAGMENT_BUILDERS
