"""Route-level tests for app/main.py, using FastAPI's TestClient.

Every other test file in this project exercises pure functions in
app/core or app/builder directly, with no mocking. This is the first
file that needs to fake anything: the Docker daemon (build_image is
monkeypatched to a canned success, so these never need a real daemon)
and the on-disk stores (credentials/secrets/build_output all redirected
to tmp_path, same pattern tests/test_credentials.py already uses for
credential_store).
"""

import json
import re
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.builder import auth_store, build_store, credential_store, dockerfile_gen, smoke_test
from app.builder.docker_build import BuildResult
from app.builder.smoke_test import SmokeResult
from app.core import auth

_BUILD_ID_RE = re.compile(r"/credentials/([a-f0-9]{32})")

CONSOLE_PASSWORD = "test-console-password"


def _fake_build_image(ok: bool):
    """Stands in for the real docker build. Must accept on_log, because
    that is how the background build streams progress to build_store."""

    def build(context_dir, tag, on_log=None):
        line = "Successfully built" if ok else "Build failed"
        if on_log is not None:
            on_log(line)
        return BuildResult(ok=ok, tag=tag, log=[line])

    return build


@pytest.fixture
def anonymous_client(tmp_path, monkeypatch):
    """A client with no session, for exercising the auth gate itself."""
    # Real scrypt cost is ~32 MB and a fraction of a second per hash. That
    # is the point in production and just tax here, so route tests run it
    # cheap; tests/test_auth.py covers the real parameters.
    monkeypatch.setattr(auth, "SCRYPT_N", 2**8)
    monkeypatch.setattr(auth_store, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(
        auth_store, "PASSWORD_FILE", tmp_path / "secrets" / "console-password.json"
    )
    monkeypatch.setattr(
        auth_store, "SESSION_KEY_FILE", tmp_path / "secrets" / "console-session.key"
    )
    auth_store.set_password(CONSOLE_PASSWORD)

    monkeypatch.setattr(credential_store, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(credential_store, "KEY_FILE", tmp_path / "secrets" / "master.key")
    monkeypatch.setattr(credential_store, "CREDENTIALS_DIR", tmp_path / "credentials")
    monkeypatch.setattr(dockerfile_gen, "BUILD_OUTPUT_DIR", tmp_path / "build_output")
    monkeypatch.setattr(main_module, "BUILD_OUTPUT_DIR", tmp_path / "build_output")
    monkeypatch.setattr(
        main_module,
        "build_image",
        _fake_build_image(ok=True),
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


@pytest.fixture
def client(anonymous_client):
    """Signed in. Every route but /login and /static needs a session now."""
    resp = anonymous_client.post(
        "/login", data={"password": CONSOLE_PASSWORD}, follow_redirects=False
    )
    assert resp.status_code == 303, "fixture could not sign in"
    return anonymous_client


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
        _fake_build_image(ok=False),
    )

    def _fail(tag, requested):
        raise AssertionError("smoke test must not run against an image that failed to build")

    monkeypatch.setattr(main_module, "run_smoke_test", _fail)
    resp = _submit_build(client)
    assert resp.status_code == 200
    assert "Build failed" in resp.text
    assert "Smoke test" not in resp.text


def test_smoke_test_is_held_to_the_conf_not_the_unrounded_slider(client, monkeypatch):
    """format_conf_value rounds on the way into postgresql.conf, so the
    server can only ever apply the rounded value. Holding it to the raw
    slider float reports a mismatch the conf never asked for — which is
    exactly what a real build did before this was fixed (oltp/medium
    writes work_mem = 10MB from a recommendation of 10.24 MB)."""
    captured = {}

    def _capture(tag, requested):
        captured.update(requested)
        return SmokeResult(status=smoke_test.PASSED, summary="ok")

    monkeypatch.setattr(main_module, "run_smoke_test", _capture)
    resp = _submit_build(client, workload="oltp", tier="medium")
    assert resp.status_code == 200

    # Every memory value handed to the smoke test must be a whole number of
    # megabytes, because that is all postgresql.conf can express here.
    assert captured["work_mem"] == 10
    for spec in (s for s in main_module.parameters.PARAMETER_SPECS if s.kind == "memory_mb"):
        assert captured[spec.key] == int(captured[spec.key]), spec.key


def test_tuner_page_offers_a_port_field_per_publishable_service(client):
    resp = client.get("/")
    assert resp.status_code == 200
    for name in ("port_postgres", "port_pgbouncer", "port_pgadmin", "port_postgres_exporter"):
        assert f'name="{name}"' in resp.text
    # pgBackRest is baked into the image, not a sidecar, so it publishes nothing.
    assert 'name="port_pgbackrest"' not in resp.text


def test_custom_ports_reach_the_generated_compose_and_instructions(client):
    resp = _submit_build(
        client, services=["pgbouncer", "pgadmin"],
        port_postgres="15432", port_pgbouncer="16432", port_pgadmin="15050",
    )
    assert resp.status_code == 200
    build_id = _BUILD_ID_RE.search(resp.text).group(1)

    compose = (main_module.BUILD_OUTPUT_DIR / build_id / "docker-compose.yml").read_text()
    assert '"15432:5432"' in compose
    assert '"16432:5432"' in compose
    assert '"127.0.0.1:15050:80"' in compose
    # The SSH tunnel instruction has to name the port actually published.
    assert "ssh -L 15050:127.0.0.1:15050" in resp.text


def test_a_build_without_sidecars_still_records_its_postgres_port(client):
    """There's no compose file to read the port back out of, so the docker
    run instruction depends on ports.json having been written."""
    resp = _submit_build(client, port_postgres="15432")
    assert resp.status_code == 200
    assert "-p 15432:5432" in resp.text

    build_id = _BUILD_ID_RE.search(resp.text).group(1)
    later = client.get(f"/credentials/{build_id}")
    assert "-p 15432:5432" in later.text


def test_duplicate_host_ports_block_the_build(client):
    resp = _submit_build(
        client, services=["pgbouncer"], port_postgres="5432", port_pgbouncer="5432"
    )
    assert resp.status_code == 422
    assert "want host port 5432" in resp.text
    assert "Build succeeded" not in resp.text


def test_an_out_of_range_port_blocks_the_build(client):
    resp = _submit_build(client, port_postgres="70000")
    assert resp.status_code == 422
    assert "out of range" in resp.text


def test_a_port_conflict_warning_does_not_block_the_build(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "published_host_ports", lambda: {5432: "some-other-stack"}
    )
    resp = _submit_build(client)
    assert resp.status_code == 200
    assert "Build succeeded" in resp.text
    assert "already taken by some-other-stack" in resp.text


def test_older_builds_without_ports_json_fall_back_to_defaults(client):
    """ports.json postdates some builds; their instructions must still show
    the ports they were actually given."""
    resp = _submit_build(client)
    build_id = _BUILD_ID_RE.search(resp.text).group(1)
    (main_module.BUILD_OUTPUT_DIR / build_id / "ports.json").unlink()

    later = client.get(f"/credentials/{build_id}")
    assert later.status_code == 200
    assert "-p 5432:5432" in later.text


# --- authentication ---------------------------------------------------

PROTECTED_GETS = ["/", "/builds", "/credentials/deadbeef"]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_protected_pages_redirect_an_anonymous_visitor_to_login(anonymous_client, path):
    resp = anonymous_client.get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_anonymous_posts_are_refused_too(anonymous_client):
    for path in ("/build", "/validate"):
        resp = anonymous_client.post(path, data={}, follow_redirects=False)
        assert resp.status_code == 303, path
        assert resp.headers["location"] == "/login"


def test_an_anonymous_visitor_cannot_read_a_real_build_credential(anonymous_client, client):
    """The point of all of this: /credentials/<id> hands out a Postgres
    superuser password in cleartext, and used to do so to anyone."""
    build_resp = _submit_build(client)
    build_id = _BUILD_ID_RE.search(build_resp.text).group(1)
    password = re.search(r"Password: (\S+)", build_resp.text).group(1)

    anonymous_client.cookies.clear()
    resp = anonymous_client.get(f"/credentials/{build_id}", follow_redirects=False)

    assert resp.status_code == 303
    assert password not in resp.text
    assert build_id not in resp.text


def test_the_login_page_itself_is_reachable_without_a_session(anonymous_client):
    resp = anonymous_client.get("/login")
    assert resp.status_code == 200
    assert 'name="password"' in resp.text


def test_static_assets_are_reachable_so_the_login_page_can_be_styled(anonymous_client):
    resp = anonymous_client.get("/static/style.css")
    assert resp.status_code == 200


def test_signing_in_with_the_wrong_password_is_refused(anonymous_client):
    resp = anonymous_client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 401
    assert "Incorrect password" in resp.text
    assert auth.SESSION_COOKIE_NAME not in resp.cookies

    # and still no access
    assert anonymous_client.get("/", follow_redirects=False).status_code == 303


def test_signing_in_with_the_right_password_grants_access(anonymous_client):
    resp = anonymous_client.post(
        "/login", data={"password": CONSOLE_PASSWORD}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert anonymous_client.get("/").status_code == 200


def test_the_session_cookie_is_httponly_and_samesite(anonymous_client):
    """HttpOnly keeps it away from JavaScript; SameSite=Lax stops another
    site POSTing to /build with the operator's session."""
    resp = anonymous_client.post(
        "/login", data={"password": CONSOLE_PASSWORD}, follow_redirects=False
    )
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_signing_out_ends_the_session(client):
    assert client.get("/").status_code == 200

    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_a_forged_session_cookie_is_refused(anonymous_client):
    anonymous_client.cookies.set(auth.SESSION_COOKIE_NAME, "99999999999.deadbeef")
    assert anonymous_client.get("/", follow_redirects=False).status_code == 303


def test_an_expired_session_is_refused(anonymous_client, monkeypatch):
    key = auth_store.load_session_key()
    stale = auth.issue_session(
        key, issued_at=time.time() - auth.SESSION_MAX_AGE_SECONDS - 60
    )
    anonymous_client.cookies.set(auth.SESSION_COOKIE_NAME, stale)
    assert anonymous_client.get("/", follow_redirects=False).status_code == 303


def test_every_route_is_protected_unless_explicitly_public(anonymous_client):
    """The gate is middleware rather than a per-route dependency so a route
    added later is covered by default. This asserts that stays true."""
    public = set(main_module.PUBLIC_PATHS)
    checked = 0
    for route in main_module.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", set()) or set()
        if not path or path in public or path.startswith("/static"):
            continue
        # Substitute something for path params so the URL resolves.
        url = re.sub(r"\{[^}]+\}", "x", path)
        method = "GET" if "GET" in methods else next(iter(methods), None)
        if method not in ("GET", "POST"):
            continue
        resp = anonymous_client.request(method, url, follow_redirects=False)
        assert resp.status_code == 303, f"{method} {url} was not gated"
        assert resp.headers["location"] == "/login"
        checked += 1
    assert checked >= 5, "expected to have checked the real routes"


# --- background builds ------------------------------------------------

def test_build_redirects_to_its_own_page_instead_of_blocking(client):
    """The POST used to hold the request open through docker build and the
    smoke test. It now hands back a page to watch."""
    resp = client.post(
        "/build",
        data={"pg_version": "17", "workload": "oltp", "tier": "medium"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert re.fullmatch(r"/builds/[a-f0-9]{32}", resp.headers["location"])


def test_a_finished_build_page_shows_the_log_and_the_outcome(client):
    resp = _submit_build(client)
    assert resp.status_code == 200
    assert 'data-state="succeeded"' in resp.text
    assert "Successfully built" in resp.text     # streamed build log
    assert "Smoke test passed" in resp.text
    assert "Username: postgres" in resp.text


def test_the_progress_fragment_matches_the_page(client):
    resp = _submit_build(client)
    build_id = _BUILD_ID_RE.search(resp.text).group(1)

    fragment = client.get(f"/builds/{build_id}/progress")
    assert fragment.status_code == 200
    assert 'data-state="succeeded"' in fragment.text
    # The fragment is the live part only, not a whole document.
    assert "<!doctype html>" not in fragment.text.lower()


def test_a_running_build_reports_itself_as_running(client, monkeypatch, tmp_path):
    """Nothing here mocks the build to completion, so the page has to show
    the in-progress view rather than an empty result."""
    build_dir = main_module.BUILD_OUTPUT_DIR / ("0" * 32)
    build_store.start(
        build_dir,
        build_id="0" * 32,
        image_tag="pg4all/postgres:17-oltp-medium",
        pg_major="17",
        pg_full_version="17.11",
        findings=[],
    )
    build_store.append_log(build_dir, "Step 1/4 : FROM postgres:17-bookworm")

    resp = client.get(f"/builds/{'0' * 32}")
    assert resp.status_code == 200
    assert 'data-state="running"' in resp.text
    assert "Building image" in resp.text
    assert "Step 1/4" in resp.text


def test_a_failed_build_is_reported_without_a_smoke_test(client, monkeypatch):
    monkeypatch.setattr(main_module, "build_image", _fake_build_image(ok=False))

    def _fail(tag, requested):
        raise AssertionError("smoke test must not run against a failed build")

    monkeypatch.setattr(main_module, "run_smoke_test", _fail)

    resp = _submit_build(client)
    assert 'data-state="failed"' in resp.text
    assert "Build failed" in resp.text
    assert "Smoke test" not in resp.text


def test_a_crash_during_the_build_does_not_leave_the_page_spinning(client, monkeypatch):
    """Without the catch-all the record stays "running" forever and the
    reason lives only in the server log."""
    def _explode(context_dir, tag, on_log=None):
        raise RuntimeError("daemon went away")

    monkeypatch.setattr(main_module, "build_image", _explode)

    resp = _submit_build(client)
    assert 'data-state="failed"' in resp.text
    assert "Build aborted" in resp.text
    assert "daemon went away" in resp.text


def test_an_unknown_build_id_is_404(client):
    assert client.get("/builds/" + "f" * 32).status_code == 404
    assert client.get(f"/builds/{'f' * 32}/progress").status_code == 404


def test_the_builds_list_links_to_each_build_page(client):
    resp = _submit_build(client)
    build_id = _BUILD_ID_RE.search(resp.text).group(1)

    listing = client.get("/builds")
    assert f'href="/builds/{build_id}"' in listing.text
    assert "succeeded" in listing.text


# --- monitoring stack --------------------------------------------------

def test_a_monitoring_build_writes_prometheus_and_grafana_config(client):
    resp = _submit_build(client, services=["grafana"])
    assert resp.status_code == 200
    build_id = _BUILD_ID_RE.search(resp.text).group(1)
    build_dir = main_module.BUILD_OUTPUT_DIR / build_id

    for relative in (
        "monitoring/prometheus.yml",
        "monitoring/grafana/provisioning/datasources/prometheus.yml",
        "monitoring/grafana/provisioning/dashboards/pg4all.yml",
        "monitoring/grafana/dashboards/pg4all.json",
    ):
        assert (build_dir / relative).exists(), relative


def test_a_build_without_monitoring_writes_none_of_it(client):
    resp = _submit_build(client, services=["pgbouncer"])
    build_id = _BUILD_ID_RE.search(resp.text).group(1)
    assert not (main_module.BUILD_OUTPUT_DIR / build_id / "monitoring").exists()


def test_monitoring_config_is_not_uploaded_as_docker_build_context(client, monkeypatch):
    """Everything in the build directory at build time is sent to the
    daemon. Monitoring config isn't part of the image, so it must be
    written only after the build has run."""
    seen = {}

    def _capture(context_dir, tag, on_log=None):
        seen["monitoring_present"] = (context_dir / "monitoring").exists()
        return BuildResult(ok=True, tag=tag, log=["Successfully built"])

    monkeypatch.setattr(main_module, "build_image", _capture)
    _submit_build(client, services=["grafana"])
    assert seen["monitoring_present"] is False


def test_the_generated_dashboard_carries_this_builds_max_connections(client):
    """End to end: the number chosen in the tuner reaches the dashboard on
    disk as the connections panel's ceiling."""
    resp = _submit_build(client, services=["grafana"], p_max_connections="250")
    build_id = _BUILD_ID_RE.search(resp.text).group(1)

    dashboard = json.loads(
        (
            main_module.BUILD_OUTPUT_DIR
            / build_id
            / "monitoring/grafana/dashboards/pg4all.json"
        ).read_text()
    )
    panel = next(p for p in dashboard["panels"] if p["title"] == "Connections by state")
    steps = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert steps[-1]["value"] == 250


def test_selecting_grafana_brings_prometheus_and_the_exporter_into_the_compose(client):
    resp = _submit_build(client, services=["grafana"])
    build_id = _BUILD_ID_RE.search(resp.text).group(1)
    compose = (main_module.BUILD_OUTPUT_DIR / build_id / "docker-compose.yml").read_text()

    assert "  postgres_exporter:" in compose
    assert "  prometheus:" in compose
    assert "  grafana:" in compose


def test_the_result_page_explains_how_to_reach_grafana(client):
    resp = _submit_build(client, services=["grafana"], port_grafana="13000")
    assert "ssh -L 13000:127.0.0.1:13000" in resp.text
    assert "Grafana login: admin" in resp.text
    assert "down -v" in resp.text  # the volume warning


# --- deleting a build --------------------------------------------------

def _delete(client, build_id, **fields):
    return client.post(f"/builds/{build_id}/delete", data=fields)


def test_deleting_asks_for_confirmation_first(client):
    resp = _submit_build(client)
    build_id = _BUILD_ID_RE.search(resp.text).group(1)

    confirm = _delete(client, build_id)
    assert confirm.status_code == 200
    assert "This cannot be undone" in confirm.text
    assert "Delete permanently" in confirm.text

    # Nothing has actually gone yet.
    assert (main_module.BUILD_OUTPUT_DIR / build_id).is_dir()
    assert client.get(f"/credentials/{build_id}").status_code == 200


def test_confirming_removes_the_directory_and_the_credential(client):
    resp = _submit_build(client)
    build_id = _BUILD_ID_RE.search(resp.text).group(1)

    done = _delete(client, build_id, confirm="1")
    assert done.status_code == 200
    assert "Deleted" in done.text

    assert not (main_module.BUILD_OUTPUT_DIR / build_id).exists()
    assert client.get(f"/credentials/{build_id}").status_code == 404
    assert client.get(f"/builds/{build_id}").status_code == 404
    assert build_id not in client.get("/builds").text


def test_deleting_one_build_leaves_another_untouched(client):
    keep = _BUILD_ID_RE.search(_submit_build(client).text).group(1)
    drop = _BUILD_ID_RE.search(_submit_build(client, tier="large").text).group(1)

    _delete(client, drop, confirm="1")

    assert client.get(f"/builds/{keep}").status_code == 200
    assert (main_module.BUILD_OUTPUT_DIR / keep).is_dir()


def test_the_image_is_only_removed_when_asked(client, monkeypatch):
    removed = []
    monkeypatch.setattr(
        main_module, "remove_image",
        lambda tag: (removed.append(tag) or (True, f"Removed image {tag}.")),
    )

    build_id = _BUILD_ID_RE.search(_submit_build(client).text).group(1)
    _delete(client, build_id, confirm="1")
    assert removed == []

    build_id = _BUILD_ID_RE.search(_submit_build(client).text).group(1)
    _delete(client, build_id, confirm="1", remove_image="1")
    assert removed == ["pg4all/postgres:17-oltp-medium"]


def test_an_image_shared_with_another_build_is_kept(client, monkeypatch):
    """Two builds of the same version, workload and tier share one image
    tag. Removing 'this build's image' would take an image the other record
    still points at."""
    removed = []
    monkeypatch.setattr(
        main_module, "remove_image",
        lambda tag: (removed.append(tag) or (True, "removed")),
    )

    first = _BUILD_ID_RE.search(_submit_build(client).text).group(1)
    second = _BUILD_ID_RE.search(_submit_build(client).text).group(1)
    assert first != second

    confirm = _delete(client, second)
    assert "other build" in confirm.text
    assert 'name="remove_image"' not in confirm.text  # not even offered

    done = _delete(client, second, confirm="1", remove_image="1")
    assert "Kept image" in done.text
    assert removed == [], "an image another build still points at was removed"


def test_a_failed_image_removal_is_reported_not_swallowed(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "remove_image",
        lambda tag: (False, f"Could not remove image {tag}: in use by a container"),
    )
    build_id = _BUILD_ID_RE.search(_submit_build(client).text).group(1)

    done = _delete(client, build_id, confirm="1", remove_image="1")
    assert "in use by a container" in done.text
    # The rest of the deletion still happened.
    assert not (main_module.BUILD_OUTPUT_DIR / build_id).exists()


def test_a_running_build_cannot_be_deleted(client):
    """It would carry on writing into a directory that no longer exists."""
    build_id = "0" * 32
    build_store.start(
        main_module.BUILD_OUTPUT_DIR / build_id,
        build_id=build_id, image_tag="pg4all/postgres:17-oltp-medium",
        pg_major="17", pg_full_version="17.11", findings=[],
    )

    resp = _delete(client, build_id, confirm="1")
    assert resp.status_code == 409
    assert "still running" in resp.text
    assert (main_module.BUILD_OUTPUT_DIR / build_id).is_dir()


def test_deleting_an_unknown_build_is_404(client):
    assert _delete(client, "f" * 32, confirm="1").status_code == 404


def test_a_traversal_attempt_is_refused(client):
    """build_id reaches shutil.rmtree, so anything that isn't a build id has
    to be rejected before the path is built."""
    for bad in ("..", "....", "not-a-build-id", "A" * 32):
        resp = _delete(client, bad, confirm="1")
        assert resp.status_code == 404, bad


def test_deleting_requires_a_session(anonymous_client):
    resp = anonymous_client.post(
        f"/builds/{'a' * 32}/delete", data={"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
