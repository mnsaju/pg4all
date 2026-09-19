"""Helpers for tests that bring up a real generated stack.

Not a test module — pytest won't collect it. Shared by
tests/test_monitoring_stack.py and tests/test_loadtest.py, which both need
to build an image, run the compose file pg4all generated for it, and ask
Prometheus questions.
"""

import json
import subprocess
import time

import docker
from docker.errors import DockerException

from app.core import hardware, parameters, workloads


def daemon_or_skip():
    import pytest

    try:
        docker.from_env().ping()
    except DockerException as exc:
        pytest.skip(f"No reachable Docker daemon: {exc}")


def conf_values(workload: str, tier: str) -> dict[str, float | str]:
    """What the conf actually asks for, exactly as _run_build computes it."""
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


def compose(project: str, build_dir, *args, check=True):
    return subprocess.run(
        ["docker", "compose", "-p", project, *args],
        cwd=build_dir, capture_output=True, text=True, check=check,
    )


def poll_json(project: str, url: str, until=None, timeout: int = 180):
    """Curl from a throwaway container on the stack's own network.

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
                "docker", "run", "--rm", "--network", f"{project}_default",
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


def has_series(body) -> bool:
    return bool(body.get("data", {}).get("result"))


def scalar(project: str, query: str, default: float = 0.0) -> float:
    """The first value of an instant query, or `default` if it has none."""
    from urllib.parse import quote

    body = poll_json(
        project, f"http://prometheus:9090/api/v1/query?query={quote(query)}", timeout=20
    )
    results = body.get("data", {}).get("result") or []
    if not results:
        return default
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, ValueError):
        return default
