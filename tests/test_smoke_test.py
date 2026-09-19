"""Integration tests for app/builder/smoke_test.py.

These build a real image and run a real container, so they're marked
`docker` and excluded from the default run (see pytest.ini) — use
`pytest -m docker`. Everything else in the suite stays hermetic.

They are slow and worth it: this is the only place the whole chain is
exercised end to end, and the chain is where the interesting failures
live. The listen_addresses bug — every built image refusing connections
on a published port, because pointing postgres at our own config_file
bypasses the postgresql.conf upstream writes into PGDATA — was invisible
to every unit test in this project and is caught here.
"""

import pytest

pytest.importorskip("docker")

import docker  # noqa: E402
from docker.errors import DockerException  # noqa: E402

from app.builder.docker_build import build_image  # noqa: E402
from app.builder.dockerfile_gen import create_build_context  # noqa: E402
from app.builder.smoke_test import FAILED, PASSED, run_smoke_test  # noqa: E402
from app.core import hardware, parameters, workloads  # noqa: E402
from app.core.conf_generator import generate_conf, render_conf  # noqa: E402

pytestmark = pytest.mark.docker

PG_MAJOR = "17"


def _daemon_or_skip():
    try:
        docker.from_env().ping()
    except DockerException as exc:
        pytest.skip(f"No reachable Docker daemon: {exc}")


def _requested_values(workload_key: str, tier_key: str) -> dict[str, float | str]:
    groups = parameters.build_tuner_groups(
        workloads.get(workload_key), hardware.get(tier_key)
    )
    return {row.spec.key: row.recommended_value for group in groups for row in group.rows}


def _build(tmp_path, monkeypatch, conf_text: str, tag: str):
    from app.builder import dockerfile_gen

    monkeypatch.setattr(dockerfile_gen, "BUILD_OUTPUT_DIR", tmp_path / "build_output")
    context_dir = create_build_context(PG_MAJOR, conf_text)
    result = build_image(context_dir, tag)
    assert result.ok, "\n".join(result.log[-20:])
    return tag


@pytest.fixture
def cleanup_images():
    tags = []
    yield tags
    client = docker.from_env()
    for tag in tags:
        try:
            client.images.remove(tag, force=True)
        except DockerException:
            pass


def test_a_generated_image_starts_and_applies_every_tuned_setting(
    tmp_path, monkeypatch, cleanup_images
):
    """The end-to-end claim the console makes: the values you chose are the
    values the server comes up with."""
    _daemon_or_skip()

    workload, tier = "mixed", "medium"
    conf_text = render_conf(generate_conf(workloads.get(workload), hardware.get(tier)))
    tag = f"pg4all-test/smoke:{PG_MAJOR}-{workload}-{tier}"
    cleanup_images.append(tag)
    _build(tmp_path, monkeypatch, conf_text, tag)

    result = run_smoke_test(tag, _requested_values(workload, tier))

    assert result.status == PASSED, (
        f"{result.summary}\n"
        + "\n".join(f"  {c.key}: {c.requested_display} -> {c.applied_display}"
                    for c in result.mismatches)
        + "\n" + "\n".join(result.log[-20:])
    )
    assert result.comparisons, "no settings were compared"


def test_an_image_that_only_listens_on_loopback_fails_the_smoke_test(
    tmp_path, monkeypatch, cleanup_images
):
    """The regression guard for the bug this module was written to find.

    Drop listen_addresses back to PostgreSQL's built-in default and the
    container still starts perfectly happily — which is exactly why it went
    unnoticed. Only reading the live setting catches it.
    """
    _daemon_or_skip()

    conf_text = render_conf(
        generate_conf(workloads.get("mixed"), hardware.get("medium"))
    ).replace("listen_addresses = '*'\n", "")
    assert "listen_addresses" not in conf_text

    tag = f"pg4all-test/smoke:{PG_MAJOR}-loopback"
    cleanup_images.append(tag)
    _build(tmp_path, monkeypatch, conf_text, tag)

    result = run_smoke_test(tag, _requested_values("mixed", "medium"))

    assert result.status == FAILED
    assert "listen_addresses" in result.summary


def test_a_conf_postgres_rejects_is_reported_as_a_failed_start(
    tmp_path, monkeypatch, cleanup_images
):
    """A conf that builds fine and refuses to start. The image is valid; the
    database is not."""
    _daemon_or_skip()

    conf_text = render_conf(
        generate_conf(workloads.get("mixed"), hardware.get("medium"))
    ) + "wal_level = nonsense\n"

    tag = f"pg4all-test/smoke:{PG_MAJOR}-invalid"
    cleanup_images.append(tag)
    _build(tmp_path, monkeypatch, conf_text, tag)

    result = run_smoke_test(tag, _requested_values("mixed", "medium"))

    assert result.status == FAILED
    assert "did not start" in result.summary


def test_the_test_container_is_always_removed(tmp_path, monkeypatch, cleanup_images):
    """Failure is when the smoke test runs most often, so teardown on the
    failure path is the path that matters."""
    _daemon_or_skip()
    client = docker.from_env()

    conf_text = render_conf(
        generate_conf(workloads.get("mixed"), hardware.get("medium"))
    ) + "wal_level = nonsense\n"
    tag = f"pg4all-test/smoke:{PG_MAJOR}-teardown"
    cleanup_images.append(tag)
    _build(tmp_path, monkeypatch, conf_text, tag)

    before = {c.id for c in client.containers.list(all=True)}
    run_smoke_test(tag, _requested_values("mixed", "medium"))
    after = {c.id for c in client.containers.list(all=True)}

    assert after == before, "the smoke test leaked a container"
