from app.core import ports, services


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
    fragment = services.compose_fragment(
        spec, "postgres", "s3cret", ports.get(spec.key).published(ports.get(spec.key).default)
    )
    assert "DB_USER: postgres" in fragment
    assert "DB_PASSWORD: 's3cret'" in fragment
    assert "DATA_SOURCE_URI" not in fragment


def test_compose_fragment_postgres_exporter_has_credentials_and_no_pgbouncer_settings():
    spec = services.get("postgres_exporter")
    fragment = services.compose_fragment(
        spec, "postgres", "s3cret", ports.get(spec.key).published(ports.get(spec.key).default)
    )
    assert "DATA_SOURCE_USER: postgres" in fragment
    assert "DATA_SOURCE_PASS: 's3cret'" in fragment
    assert "DB_HOST" not in fragment


def test_compose_fragment_is_not_defined_for_apt_mode_service():
    spec = services.get("pgbackrest")
    assert spec.key not in services._FRAGMENT_BUILDERS


def test_compose_fragment_pgadmin_is_bound_to_loopback_only():
    spec = services.get("pgadmin")
    fragment = services.compose_fragment(
        spec, "postgres", "s3cret", ports.get(spec.key).published(ports.get(spec.key).default)
    )
    assert '"127.0.0.1:5050:80"' in fragment
    assert "0.0.0.0" not in fragment


def test_compose_fragment_pgadmin_reuses_postgres_password_as_its_own():
    spec = services.get("pgadmin")
    fragment = services.compose_fragment(
        spec, "postgres", "s3cret", ports.get(spec.key).published(ports.get(spec.key).default)
    )
    assert f"PGADMIN_DEFAULT_EMAIL: {services.PGADMIN_EMAIL}" in fragment
    assert "PGADMIN_DEFAULT_PASSWORD: 's3cret'" in fragment


def test_selecting_grafana_pulls_in_what_it_cannot_work_without():
    """Grafana can't scrape; Prometheus needs something to scrape. Ticking
    one box has to bring the chain or the dashboard is wired to nothing."""
    assert [s.key for s in services.resolve(["grafana"])] == [
        "postgres_exporter", "prometheus", "grafana"
    ]


def test_selecting_prometheus_pulls_in_the_exporter():
    assert [s.key for s in services.resolve(["prometheus"])] == [
        "postgres_exporter", "prometheus"
    ]


def test_selecting_the_whole_chain_explicitly_does_not_duplicate_it():
    assert [s.key for s in services.resolve(["postgres_exporter", "prometheus", "grafana"])] == [
        "postgres_exporter", "prometheus", "grafana"
    ]


def test_dependencies_come_before_the_service_that_needs_them():
    """Cosmetic — depends_on does the real ordering — but the generated
    compose should read in the order things start."""
    keys = [s.key for s in services.resolve(["grafana", "pgbouncer"])]
    assert keys.index("prometheus") < keys.index("grafana")
    assert keys.index("postgres_exporter") < keys.index("prometheus")


def test_named_volumes_are_collected_without_duplicates():
    assert services.named_volumes(services.resolve(["grafana"])) == [
        "prometheus_data", "grafana_data"
    ]
    assert services.named_volumes(services.resolve(["pgbouncer"])) == []


def test_prometheus_restates_the_defaults_it_overrides():
    """Overriding `command` drops the image's own defaults, so the config
    and storage paths have to be repeated alongside the retention flags."""
    spec = services.get("prometheus")
    fragment = services.compose_fragment(
        spec, "postgres", "s3cret", ports.get("prometheus").published(9090)
    )
    assert "--config.file=/etc/prometheus/prometheus.yml" in fragment
    assert "--storage.tsdb.path=/prometheus" in fragment
    assert "--storage.tsdb.retention.time=15d" in fragment
    assert "--storage.tsdb.retention.size=2GB" in fragment


def test_grafana_reuses_the_build_password_and_disables_sign_up():
    spec = services.get("grafana")
    fragment = services.compose_fragment(
        spec, "postgres", "s3cret", ports.get("grafana").published(3000)
    )
    assert f"GF_SECURITY_ADMIN_USER: {services.GRAFANA_ADMIN_USER}" in fragment
    assert "GF_SECURITY_ADMIN_PASSWORD: 's3cret'" in fragment
    assert 'GF_USERS_ALLOW_SIGN_UP: "false"' in fragment
    assert '"127.0.0.1:3000:3000"' in fragment


def test_grafana_dashboards_are_not_mounted_inside_its_data_volume():
    """A read-only bind mount nested inside a named volume works, but reads
    as an accident; the provisioned files live outside /var/lib/grafana."""
    fragment = services.compose_fragment(
        services.get("grafana"), "postgres", "pw",
        ports.get("grafana").published(3000),
    )
    assert "/etc/grafana/dashboards:ro" in fragment
    assert "grafana_data:/var/lib/grafana\n" in fragment
    assert "/var/lib/grafana/dashboards" not in fragment
