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
