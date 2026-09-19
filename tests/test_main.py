"""Route-level tests for app/main.py, using FastAPI's TestClient.

Every other test file in this project exercises pure functions in
app/core or app/builder directly, with no mocking. This is the first
file that needs to fake anything: the Docker daemon (build_image is
monkeypatched to a canned success, so these never need a real daemon)
and the on-disk stores (credentials/secrets/build_output all redirected
to tmp_path, same pattern tests/test_credentials.py already uses for
credential_store).
"""

import re

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.builder import credential_store, dockerfile_gen
from app.builder.docker_build import BuildResult

_BUILD_ID_RE = re.compile(r"/credentials/([a-f0-9]{32})")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(credential_store, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(credential_store, "KEY_FILE", tmp_path / "secrets" / "master.key")
    monkeypatch.setattr(credential_store, "CREDENTIALS_DIR", tmp_path / "credentials")
    monkeypatch.setattr(dockerfile_gen, "BUILD_OUTPUT_DIR", tmp_path / "build_output")
    monkeypatch.setattr(main_module, "BUILD_OUTPUT_DIR", tmp_path / "build_output")
    monkeypatch.setattr(
        main_module,
        "build_image",
        lambda context_dir, tag: BuildResult(ok=True, tag=tag, log=["Successfully built"]),
    )
    return TestClient(main_module.app)


def _submit_build(client, **extra_fields):
    # httpx's TestClient wants repeated form keys as a dict of lists, not a
    # list of tuples (the latter silently encodes to nothing in httpx 0.28).
    data = {"pg_version": "17", "workload": "oltp", "tier": "medium", **extra_fields}
    return client.post("/build", data=data)


def test_index_renders_tuner_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Config Tuner" in resp.text
    assert 'name="services"' in resp.text
    assert 'name="extensions"' in resp.text


def test_index_reflects_query_param_selection(client):
    resp = client.get("/?pg_version=17&workload=oltp&tier=large")
    assert resp.status_code == 200
    assert 'value="17"' in resp.text
    assert 'value="oltp"' in resp.text


def test_build_with_no_services_shows_plain_docker_run(client):
    resp = _submit_build(client)
    assert resp.status_code == 200
    assert "Build succeeded" in resp.text
    assert "docker run -d" in resp.text
    assert "docker compose up -d" not in resp.text
    assert "Username: postgres" in resp.text


def test_build_with_sidecars_shows_compose_instead_of_docker_run(client):
    resp = _submit_build(client, services=["pgbouncer", "postgres_exporter"])
    assert resp.status_code == 200
    assert "docker compose up -d" in resp.text
    assert "docker run -d" not in resp.text


def test_build_with_pgbackrest_shows_docker_run_plus_pgbackrest_instructions(client):
    resp = _submit_build(client, services=["pgbackrest"])
    assert resp.status_code == 200
    assert "docker run -d" in resp.text  # no sidecar, so no compose file
    assert "pgBackRest is installed" in resp.text
    assert "stanza-create" in resp.text


def test_build_with_pgadmin_shows_ssh_tunnel_and_shared_password_note(client):
    resp = _submit_build(client, services=["pgadmin"])
    assert resp.status_code == 200
    assert "ssh -L 5050:127.0.0.1:5050" in resp.text
    assert "admin@pg4all.dev" in resp.text


def test_credentials_page_reproduces_same_instructions_after_the_fact(client):
    build_resp = _submit_build(client, services=["pgbouncer"])
    build_id = _BUILD_ID_RE.search(build_resp.text).group(1)

    resp = client.get(f"/credentials/{build_id}")
    assert resp.status_code == 200
    assert "docker compose up -d" in resp.text
    assert "docker run -d" not in resp.text


def test_credentials_page_for_unknown_build_id_is_404(client):
    resp = client.get("/credentials/does-not-exist")
    assert resp.status_code == 404


def test_builds_list_shows_a_build_just_made(client):
    build_resp = _submit_build(client)
    build_id = _BUILD_ID_RE.search(build_resp.text).group(1)

    resp = client.get("/builds")
    assert resp.status_code == 200
    assert build_id in resp.text
