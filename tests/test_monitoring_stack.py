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

import re

import pytest

pytest.importorskip("docker")

import docker  # noqa: E402
from docker.errors import DockerException  # noqa: E402

from app.builder import compose_gen, monitoring_gen  # noqa: E402
from app.builder.docker_build import build_image  # noqa: E402
from app.builder.dockerfile_gen import create_build_context  # noqa: E402
from app.core import hardware, monitoring, services, workloads  # noqa: E402
from app.core.conf_generator import generate_conf, render_conf  # noqa: E402
from tests import stack  # noqa: E402

pytestmark = pytest.mark.docker

PG_MAJOR = "17"
PROJECT = "pg4alltest"

# These tests reach everything over the compose network, so the published
# ports are incidental — but compose publishes them regardless, and a
# collision fails the whole `up`. Each stack-based suite therefore owns a
# distinct high range that nothing a developer is likely to be running will
# claim: 61xxx here, 62xxx for the load test.
HOST_PORTS = {
    "postgres": 61432,
    "pgbouncer": 61433,
    "postgres_exporter": 61187,
    "pgadmin": 61050,
    "prometheus": 61090,
    "grafana": 61000,
}


@pytest.fixture
def monitoring_stack(tmp_path, monkeypatch):
    """A built image plus its generated monitoring stack, running."""
    stack.daemon_or_skip()

    from app.builder import dockerfile_gen

    monkeypatch.setattr(dockerfile_gen, "BUILD_OUTPUT_DIR", tmp_path / "build_output")

    workload, tier = "oltp", "medium"
    conf_text = render_conf(generate_conf(workloads.get(workload), hardware.get(tier)))
    build_dir = create_build_context(PG_MAJOR, conf_text)

    tag = f"pg4all-test/monitoring:{PG_MAJOR}"
    result = build_image(build_dir, tag)
    assert result.ok, "\n".join(result.log[-20:])

    selected = services.resolve(["grafana"])
    values = stack.conf_values(workload, tier)
    compose_gen.write_compose(build_dir, tag, "postgres", "testpw", selected, HOST_PORTS)
    monitoring_gen.write_monitoring_files(build_dir, values, PG_MAJOR)

    stack.compose(PROJECT, build_dir, "up", "-d")
    try:
        yield build_dir, values
    finally:
        stack.compose(PROJECT, build_dir, "down", "-v", check=False)
        try:
            docker.from_env().images.remove(tag, force=True)
        except DockerException:
            pass


def test_prometheus_scrapes_the_exporter(monitoring_stack):
    build_dir, _ = monitoring_stack
    body = stack.poll_json(
        PROJECT,
        "http://prometheus:9090/api/v1/query?query=up%7Bjob%3D%22postgres%22%7D",
        until=stack.has_series,
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
            stack.poll_json(
                PROJECT,
                f"http://prometheus:9090/api/v1/query?query={name}",
                until=stack.has_series,
                timeout=60,
            )
        except AssertionError:
            missing.append(name)

    assert not missing, f"dashboard queries metrics the exporter never publishes: {missing}"


def test_grafana_comes_up_with_the_datasource_working(monitoring_stack):
    build_dir, _ = monitoring_stack

    health = stack.poll_json(PROJECT, "http://grafana:3000/api/health")
    assert health.get("database") == "ok", health

    # Provisioned, so it must exist without anyone configuring it.
    datasource = stack.poll_json(
        PROJECT,
        "http://admin:testpw@grafana:3000/api/datasources/uid/"
        + monitoring.DATASOURCE_UID,
    )
    assert datasource.get("type") == "prometheus", datasource
    assert datasource.get("url") == monitoring.PROMETHEUS_URL


def test_the_dashboard_is_provisioned_and_intact(monitoring_stack):
    build_dir, _ = monitoring_stack

    found = stack.poll_json(
        PROJECT,
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
    body = stack.poll_json(PROJECT, "http://grafana:3000/api/datasources")
    # Unauthenticated calls get an error document, never the list.
    assert isinstance(body, dict)
    assert "message" in body and not isinstance(body.get("datasources"), list)
