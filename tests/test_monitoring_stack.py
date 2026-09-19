"""Integration tests for the generated monitoring stack.

Marked `docker` and excluded from the default run: these build an image
and bring up four containers.

Everything else about the dashboard can be asserted offline — panel
layout, thresholds, which tuned value each one carries. What cannot is
whether the metrics it queries *exist*. A dashboard that references
`pg_stat_bgwriter_checkpoints_req` instead of
`pg_stat_bgwriter_checkpoints_req_total` renders perfectly, with every
panel empty, and no unit test would notice. So the interesting test here
pulls every metric name out of the generated dashboard and asks a real
Prometheus, scraping a real exporter, whether each one has data.
"""

import json
import re
import subprocess
import time

import pytest

pytest.importorskip("docker")

import docker  # noqa: E402
from docker.errors import DockerException  # noqa: E402

from app.builder import compose_gen, monitoring_gen  # noqa: E402
from app.builder.docker_build import build_image  # noqa: E402
from app.builder.dockerfile_gen import create_build_context  # noqa: E402
from app.core import hardware, monitoring, parameters, services, workloads  # noqa: E402
from app.core.conf_generator import generate_conf, render_conf  # noqa: E402

pytestmark = pytest.mark.docker

PG_MAJOR = "17"
PROJECT = "pg4alltest"

# Deliberately unusual, to miss anything already running on this host.
HOST_PORTS = {
    "postgres": 45432,
    "pgbouncer": 46432,
    "postgres_exporter": 49187,
    "pgadmin": 45050,
    "prometheus": 49090,
    "grafana": 43000,
}


def _daemon_or_skip():
    try:
        docker.from_env().ping()
    except DockerException as exc:
        pytest.skip(f"No reachable Docker daemon: {exc}")


def _conf_values(workload: str, tier: str) -> dict[str, float | str]:
    groups = parameters.build_tuner_groups(
        workloads.get(workload), hardware.get(tier)
    )
    recommended = {
        row.spec.key: row.recommended_value for group in groups for row in group.rows
    }
    return {
        spec.key: parameters.parse_value(
            spec, parameters.format_conf_value(spec, recommended[spec.key])
        )
        for spec in parameters.PARAMETER_SPECS
    }


def _compose(build_dir, *args, check=True):
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *args],
        cwd=build_dir, capture_output=True, text=True, check=check,
    )


def _poll_json(build_dir, url: str, until=None, timeout: int = 180):
    """Curl from inside a throwaway container on the stack's own network.

    Prometheus and Grafana are published on loopback, but going through the
    compose network keeps the test independent of the host's port bindings.

    `until` is a predicate over the decoded body. Without it this returns
    the first parseable response, which is the wrong thing to do for a
    Prometheus query: before the first scrape completes, an empty result
    set is perfectly valid JSON, so polling only for "is it JSON yet" reads
    a healthy stack as an empty one.
    """
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--network", f"{PROJECT}_default",
                "curlimages/curl:latest", "-s", "-m", "5", url,
            ],
            capture_output=True, text=True,
        )
        last = (result.stdout or result.stderr).strip()
        if result.returncode == 0 and last:
            try:
                body = json.loads(last)
            except json.JSONDecodeError:
                body = None
            if body is not None and (until is None or until(body)):
                return body
        time.sleep(3)
    raise AssertionError(f"no usable response from {url} within {timeout}s: {last[:300]}")


def _has_series(body) -> bool:
    return bool(body.get("data", {}).get("result"))


@pytest.fixture
def monitoring_stack(tmp_path, monkeypatch):
    """A built image plus its generated monitoring stack, running."""
    _daemon_or_skip()

    from app.builder import dockerfile_gen

    monkeypatch.setattr(dockerfile_gen, "BUILD_OUTPUT_DIR", tmp_path / "build_output")

    workload, tier = "oltp", "medium"
    conf_text = render_conf(generate_conf(workloads.get(workload), hardware.get(tier)))
    build_dir = create_build_context(PG_MAJOR, conf_text)

    tag = f"pg4all-test/monitoring:{PG_MAJOR}"
    result = build_image(build_dir, tag)
    assert result.ok, "\n".join(result.log[-20:])

    selected = services.resolve(["grafana"])
    values = _conf_values(workload, tier)
    compose_gen.write_compose(build_dir, tag, "postgres", "testpw", selected, HOST_PORTS)
    monitoring_gen.write_monitoring_files(build_dir, values, PG_MAJOR)

    _compose(build_dir, "up", "-d")
    try:
        yield build_dir, values
    finally:
        _compose(build_dir, "down", "-v", check=False)
        try:
            docker.from_env().images.remove(tag, force=True)
        except DockerException:
            pass


def test_prometheus_scrapes_the_exporter(monitoring_stack):
    build_dir, _ = monitoring_stack
    body = _poll_json(
        build_dir,
        "http://prometheus:9090/api/v1/query?query=up%7Bjob%3D%22postgres%22%7D",
        until=_has_series,
    )
    results = body["data"]["result"]
    assert results, "Prometheus has no 'up' series for the postgres job at all"
    assert results[0]["value"][1] == "1", f"scrape target is down: {results}"


def test_every_metric_the_dashboard_queries_actually_exists(monitoring_stack):
    """The failure this catches: a mistyped or renamed metric renders a
    perfectly good-looking panel with nothing in it. Names are pulled from
    the generated dashboard so this can't drift away from what ships.
    """
    build_dir, values = monitoring_stack
    dashboard = monitoring.build_dashboard(values, PG_MAJOR)

    names = sorted(
        {
            name
            for panel in dashboard["panels"]
            for target in panel["targets"]
            for name in re.findall(r"\bpg_[a-z_]+\b", target["expr"])
        }
    )
    assert len(names) >= 8, f"expected the dashboard to query more metrics: {names}"

    missing = []
    for name in names:
        try:
            _poll_json(
                build_dir,
                f"http://prometheus:9090/api/v1/query?query={name}",
                until=_has_series,
                timeout=60,
            )
        except AssertionError:
            missing.append(name)

    assert not missing, f"dashboard queries metrics the exporter never publishes: {missing}"


def test_grafana_comes_up_with_the_datasource_working(monitoring_stack):
    build_dir, _ = monitoring_stack

    health = _poll_json(build_dir, "http://grafana:3000/api/health")
    assert health.get("database") == "ok", health

    # Provisioned, so it must exist without anyone configuring it.
    datasource = _poll_json(
        build_dir,
        "http://admin:testpw@grafana:3000/api/datasources/uid/"
        + monitoring.DATASOURCE_UID,
    )
    assert datasource.get("type") == "prometheus", datasource
    assert datasource.get("url") == monitoring.PROMETHEUS_URL


def test_the_dashboard_is_provisioned_and_intact(monitoring_stack):
    build_dir, _ = monitoring_stack

    found = _poll_json(
        build_dir,
        "http://admin:testpw@grafana:3000/api/dashboards/uid/"
        + monitoring.DASHBOARD_UID,
    )
    assert "dashboard" in found, found
    panels = found["dashboard"]["panels"]
    assert len(panels) == 9, [p.get("title") for p in panels]
    assert found["dashboard"]["title"] == monitoring.DASHBOARD_TITLE


def test_grafana_refuses_anonymous_access(monitoring_stack):
    """Sign-up disabled and anonymous off, so the loopback bind isn't the
    only thing standing between a viewer and the data."""
    build_dir, _ = monitoring_stack
    body = _poll_json(build_dir, "http://grafana:3000/api/datasources")
    # Unauthenticated calls get an error document, never the list.
    assert isinstance(body, dict)
    assert "message" in body and not isinstance(body.get("datasources"), list)
