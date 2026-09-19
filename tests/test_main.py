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
from app.builder import credential_store, dockerfile_gen, smoke_test
from app.builder.docker_build import BuildResult
from app.builder.smoke_test import SmokeResult

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
    # Without this the route boots a real container per test against the
    # real daemon — slow, and dependent on which images happen to exist on
    # the host. tests/test_smoke_test.py covers the real thing behind a
    # marker; here it's a canned pass.
    monkeypatch.setattr(
        main_module,
        "run_smoke_test",
        lambda tag, requested: SmokeResult(
            status=smoke_test.PASSED,
            summary="PostgreSQL started and all tuned settings took effect.",
            log=["Started test container."],
        ),
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


def test_validate_endpoint_returns_findings_for_an_overcommitted_set(client):
    resp = client.post(
        "/validate",
        data={
            "pg_version": "17", "workload": "oltp", "tier": "small",
            "p_max_connections": "500", "p_work_mem": "64",
        },
    )
    assert resp.status_code == 200
    assert "Worst-case memory exceeds the tier" in resp.text
    assert "error" in resp.text


def test_validate_endpoint_is_quiet_for_a_sane_set(client):
    resp = client.post(
        "/validate",
        data={
            "pg_version": "17", "workload": "mixed", "tier": "medium",
            "services": ["pgbouncer"],
        },
    )
    assert resp.status_code == 200
    assert "Worst-case memory exceeds the tier" not in resp.text


def test_build_is_blocked_by_an_error_level_finding(client):
    resp = _submit_build(
        client, tier="small", p_max_connections="500", p_work_mem="64"
    )
    assert resp.status_code == 422
    assert "Build stopped before it started" in resp.text
    assert "Worst-case memory exceeds the tier" in resp.text
    assert "Build succeeded" not in resp.text


def test_blocked_build_can_be_overridden_by_acknowledging(client):
    resp = _submit_build(
        client, tier="small", p_max_connections="500", p_work_mem="64",
        acknowledge="1",
    )
    assert resp.status_code == 200
    assert "Build succeeded" in resp.text


def test_warning_level_findings_do_not_block_the_build(client):
    resp = _submit_build(client, p_synchronous_commit="off")
    assert resp.status_code == 200
    assert "Build succeeded" in resp.text
    assert "synchronous_commit is off" in resp.text


def test_confirm_page_preserves_the_submitted_selection(client):
    resp = _submit_build(
        client, tier="small", p_max_connections="500", p_work_mem="64",
        services=["pgbouncer", "pgadmin"], extensions=["pg_stat_statements"],
    )
    assert resp.status_code == 422
    # Everything needed to rebuild the exact same request must be echoed
    # back, or "Build anyway" would silently build something different.
    assert 'name="p_max_connections" value="500"' in resp.text
    assert 'name="services" value="pgbouncer"' in resp.text
    assert 'name="services" value="pgadmin"' in resp.text
    assert 'name="extensions" value="pg_stat_statements"' in resp.text
    assert 'name="tier" value="small"' in resp.text


def test_result_page_shows_the_smoke_test_outcome(client):
    resp = _submit_build(client)
    assert resp.status_code == 200
    assert "Smoke test passed" in resp.text


def test_a_failing_smoke_test_does_not_invalidate_the_build(client, monkeypatch):
    """The image exists on the daemon either way. Reporting the failure is
    the job here; deciding what to do about it is the operator's."""
    monkeypatch.setattr(
        main_module,
        "run_smoke_test",
        lambda tag, requested: SmokeResult(
            status=smoke_test.FAILED,
            summary="PostgreSQL did not start with this configuration.",
            log=["FATAL: could not start"],
        ),
    )
    resp = _submit_build(client)
    assert resp.status_code == 200
    assert "Build succeeded" in resp.text
    assert "Smoke test failed" in resp.text
    assert "Username: postgres" in resp.text  # credentials still handed over


def test_smoke_test_is_skipped_when_the_build_fails(client, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "build_image",
        lambda context_dir, tag: BuildResult(ok=False, tag=tag, log=["Build failed"]),
    )

    def _fail(tag, requested):
        raise AssertionError("smoke test must not run against an image that failed to build")

    monkeypatch.setattr(main_module, "run_smoke_test", _fail)
    resp = _submit_build(client)
    assert resp.status_code == 200
    assert "Build failed" in resp.text
    assert "Smoke test" not in resp.text
