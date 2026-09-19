"""Load test: does the dashboard actually respond to traffic?

Marked `loadtest` and excluded from every default run — it builds an
image, brings up four containers and drives real work through them.
Run it with `pytest -m loadtest`.

This exists because an idle database makes every rate panel flat zero,
so "the panel is empty" and "the panel is broken" look identical from the
outside. That is exactly how the PostgreSQL 17 checkpoint-metric bug
stayed hidden. Here the database is made busy on purpose and each panel's
metric is asserted to have *moved*.

Assertions are directional — commits happened, connections rose, rows
exist — never exact numbers. Throughput on a shared CI box is not
reproducible, and a test that asserts "2000 tps" fails for reasons that
have nothing to do with pg4all.
"""

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("docker")

import docker  # noqa: E402
from docker.errors import DockerException  # noqa: E402

from app.builder import compose_gen, monitoring_gen  # noqa: E402
from app.builder.docker_build import build_image  # noqa: E402
from app.builder.dockerfile_gen import create_build_context  # noqa: E402
from app.core import hardware, services, workloads  # noqa: E402
from app.core.conf_generator import generate_conf, render_conf  # noqa: E402
from tests import stack  # noqa: E402

pytestmark = pytest.mark.loadtest

PG_MAJOR = "17"
PROJECT = "pg4allload"
CONTAINER = f"{PROJECT}-postgres-1"
RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "loadtest" / "run.sh"

# 62xxx: see the note in tests/test_monitoring_stack.py — distinct range per
# stack-based suite, so two of them (or a developer's own stack) never
# collide on `docker compose up`.
HOST_PORTS = {
    "postgres": 62432, "pgbouncer": 62433, "postgres_exporter": 62187,
    "pgadmin": 62050, "prometheus": 62090, "grafana": 62000,
}

# Short and gentle: enough to move every panel, not enough to make the
# machine running the suite unusable.
LOAD_ENV = {
    "PG_CONTAINER": CONTAINER,
    "STEADY_SECONDS": "20",
    "SPIKE_SECONDS": "20",
    "SPIKE_FRACTION": "0.25",
}


def run_loadtest(command: str) -> str:
    import os

    result = subprocess.run(
        ["bash", str(RUNNER), command],
        capture_output=True, text=True, env={**os.environ, **LOAD_ENV},
    )
    assert result.returncode == 0, f"{command} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def psql(query: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "-u", "postgres", CONTAINER, "psql", "-tAc", query],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


@pytest.fixture(scope="module")
def loaded_stack(tmp_path_factory):
    """A generated monitoring stack with the ecommerce load driven through it.

    Module-scoped: building an image and running two load phases is far too
    slow to repeat per test, and every assertion here reads the same run.
    """
    stack.daemon_or_skip()

    from app.builder import dockerfile_gen

    tmp_path = tmp_path_factory.mktemp("loadtest")
    original = dockerfile_gen.BUILD_OUTPUT_DIR
    dockerfile_gen.BUILD_OUTPUT_DIR = tmp_path / "build_output"
    try:
        conf_text = render_conf(
            generate_conf(workloads.get("oltp"), hardware.get("medium"))
        )
        build_dir = create_build_context(PG_MAJOR, conf_text)
    finally:
        dockerfile_gen.BUILD_OUTPUT_DIR = original

    tag = f"pg4all-test/loadtest:{PG_MAJOR}"
    result = build_image(build_dir, tag)
    assert result.ok, "\n".join(result.log[-20:])

    selected = services.resolve(["grafana"])
    values = stack.conf_values("oltp", "medium")
    compose_gen.write_compose(build_dir, tag, "postgres", "testpw", selected, HOST_PORTS)
    monitoring_gen.write_monitoring_files(build_dir, values, PG_MAJOR)

    stack.compose(PROJECT, build_dir, "up", "-d")
    try:
        # Wait for the database, and for Prometheus to have scraped it once,
        # so "before" is a real baseline rather than an absence of data.
        stack.poll_json(
            PROJECT,
            "http://prometheus:9090/api/v1/query?query=pg_up",
            until=stack.has_series,
        )
        run_loadtest("setup")
        run_loadtest("steady")
        run_loadtest("spike")
        run_loadtest("checkpoint")
        # One more scrape interval so the spike is inside Prometheus's window.
        stack.poll_json(
            PROJECT,
            "http://prometheus:9090/api/v1/query?query="
            "max_over_time(sum(pg_stat_activity_count)%5B2m:15s%5D)",
            until=stack.has_series,
        )
        yield build_dir
    finally:
        stack.compose(PROJECT, build_dir, "down", "-v", check=False)
        try:
            docker.from_env().images.remove(tag, force=True)
        except DockerException:
            pass


def test_the_load_actually_committed_transactions(loaded_stack):
    peak = stack.scalar(
        PROJECT, "max_over_time(sum(rate(pg_stat_database_xact_commit[1m]))[3m:15s])"
    )
    assert peak > 5, f"transactions panel would be flat: peak {peak}/s"


def test_the_spike_pushed_connections_well_above_idle(loaded_stack):
    """The connections panel is the one carrying max_connections as a
    threshold, so it has to visibly move for that line to mean anything."""
    peak = stack.scalar(
        PROJECT, "max_over_time(sum(pg_stat_activity_count)[3m:15s])"
    )
    assert peak > 20, f"connections panel barely moved: peak {peak}"


def test_the_workload_moved_the_remaining_panels(loaded_stack):
    """Every other panel's metric, checked in one place: if any stays at
    zero after this much work, that panel is broken rather than quiet."""
    checks = {
        "cache hit ratio": "sum(rate(pg_stat_database_blks_hit[3m]))",
        "locks": "max_over_time(sum(pg_locks_count)[3m:15s])",
        "database size": "sum(pg_database_size_bytes)",
        "longest transaction": "max_over_time(max(pg_stat_activity_max_tx_duration)[3m:15s])",
        "checkpoints requested": "pg_stat_checkpointer_num_requested_total",
        "up": "pg_up",
    }
    flat = {
        name: stack.scalar(PROJECT, query)
        for name, query in checks.items()
        if stack.scalar(PROJECT, query) <= 0
    }
    assert not flat, f"panels with no data after a full load run: {flat}"


def test_the_purchases_are_really_in_the_database(loaded_stack):
    orders = int(psql("SELECT count(*) FROM pg4all_loadtest.orders") or 0)
    items = int(psql("SELECT count(*) FROM pg4all_loadtest.order_items") or 0)
    assert orders > 100, f"only {orders} orders written"
    assert items >= orders, "every order should have at least one line item"


def test_cleanup_removes_everything_it_created(loaded_stack):
    """Runs last in the module: the schema is gone afterwards, so anything
    depending on the data must come before it."""
    assert psql("SELECT to_regclass('pg4all_loadtest.orders')")

    run_loadtest("cleanup")

    assert psql("SELECT to_regclass('pg4all_loadtest.orders')") == ""
    leftover = psql(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'pg4all_loadtest'"
    )
    assert leftover in ("0", ""), f"{leftover} tables survived cleanup"
    assert psql(
        "SELECT count(*) FROM information_schema.schemata "
        "WHERE schema_name = 'pg4all_loadtest'"
    ) == "0"
