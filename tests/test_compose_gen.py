from app.builder import compose_gen
from app.core import services


def test_write_compose_returns_none_when_no_sidecars_selected(tmp_path):
    selected = services.resolve(["pgbackrest"])  # apt mode, not a sidecar
    assert compose_gen.write_compose(tmp_path, "pg4all/postgres:17", "postgres", "pw", selected) is None
    assert not (tmp_path / "docker-compose.yml").exists()


def test_write_compose_returns_none_for_empty_selection(tmp_path):
    assert compose_gen.write_compose(tmp_path, "pg4all/postgres:17", "postgres", "pw", []) is None


def test_write_compose_includes_postgres_and_selected_sidecars(tmp_path):
    selected = services.resolve(["pgbouncer", "postgres_exporter"])
    path = compose_gen.write_compose(tmp_path, "pg4all/postgres:17-oltp-medium", "postgres", "s3cret", selected)

    assert path == tmp_path / "docker-compose.yml"
    text = path.read_text()
    assert "image: pg4all/postgres:17-oltp-medium" in text
    assert "pgbouncer:" in text
    assert "postgres_exporter:" in text
    assert "DB_PASSWORD: 's3cret'" in text
    assert "DATA_SOURCE_PASS: 's3cret'" in text


def test_write_compose_excludes_apt_mode_service_from_sidecars(tmp_path):
    selected = services.resolve(["pgbouncer", "pgbackrest"])
    text = compose_gen.write_compose(tmp_path, "pg4all/postgres:17", "postgres", "pw", selected).read_text()
    assert "pgbackrest" not in text


def test_write_compose_binds_pgadmin_to_loopback_only(tmp_path):
    selected = services.resolve(["pgadmin"])
    text = compose_gen.write_compose(tmp_path, "pg4all/postgres:17", "postgres", "pw", selected).read_text()
    assert "pgadmin:" in text
    assert '"127.0.0.1:5050:80"' in text
    assert "0.0.0.0" not in text


def test_pgbouncer_publishes_to_the_port_it_actually_listens_on():
    """edoburu/pgbouncer binds 5432 inside the container; 6432 is only the
    conventional host-side port for a pooler. Mapping 6432:6432 published a
    port nothing was bound to, and pgbouncer still looked healthy in
    `docker compose ps` while refusing every connection."""
    text = compose_gen.render_compose(
        "pg4all/postgres:17-oltp-medium",
        "postgres",
        "secret",
        services.resolve(["pgbouncer"]),
    )
    assert '"6432:5432"' in text
    assert '"6432:6432"' not in text


def test_custom_host_ports_appear_in_the_generated_compose():
    text = compose_gen.render_compose(
        "pg4all/postgres:17-oltp-medium", "postgres", "secret",
        services.resolve(["pgbouncer", "pgadmin", "postgres_exporter"]),
        host_ports={
            "postgres": 15432, "pgbouncer": 16432,
            "postgres_exporter": 19187, "pgadmin": 15050,
        },
    )
    assert '"15432:5432"' in text      # postgres
    assert '"16432:5432"' in text      # pgbouncer listens on 5432 inside
    assert '"19187:9187"' in text      # exporter
    assert '"127.0.0.1:15050:80"' in text  # pgadmin stays loopback-bound


def test_omitting_host_ports_keeps_the_conventional_defaults():
    text = compose_gen.render_compose(
        "pg4all/postgres:17-oltp-medium", "postgres", "secret",
        services.resolve(["pgbouncer"]),
    )
    assert '"5432:5432"' in text
    assert '"6432:5432"' in text


def test_named_volumes_are_declared_at_the_top_level():
    """Compose rejects a file that references a named volume without
    declaring it."""
    text = compose_gen.render_compose(
        "pg4all/postgres:17-oltp-medium", "postgres", "secret",
        services.resolve(["grafana"]),
    )
    assert "\nvolumes:\n" in text
    assert "  prometheus_data:\n" in text
    assert "  grafana_data:\n" in text


def test_no_volumes_block_when_nothing_needs_one():
    text = compose_gen.render_compose(
        "pg4all/postgres:17-oltp-medium", "postgres", "secret",
        services.resolve(["pgbouncer"]),
    )
    assert "\nvolumes:\n" not in text


def test_monitoring_config_is_bind_mounted_from_the_build_directory():
    """Relative paths resolve against the compose file's own directory,
    which is the build directory the instructions tell you to cd into."""
    text = compose_gen.render_compose(
        "pg4all/postgres:17-oltp-medium", "postgres", "secret",
        services.resolve(["grafana"]),
    )
    assert "./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro" in text
    assert "./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro" in text
