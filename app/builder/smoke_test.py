"""Start a freshly built image, ask the server what it actually applied,
then throw it away.

A successful `docker build` only proves the image assembled. It says
nothing about whether PostgreSQL will accept the conf baked into it, and
PostgreSQL is quiet about disagreeing: it rounds, it clamps to
platform-determined maxima, it ignores settings its build doesn't support
— all at startup, with the requested value still sitting in the file. The
gap between "the image built" and "the database came up with the settings
you asked for" is where this module lives.

The container is ephemeral: no published ports, its own throwaway volume,
and queried over the unix socket with `docker exec` so nothing touches the
host's network. It is removed in a `finally`, including its volume, on
every path out of here — a smoke test that leaks containers on failure
would be worse than no smoke test, because failure is exactly when it runs.
"""

import secrets
import time
from dataclasses import dataclass, field

import docker
from docker.errors import DockerException

from app.core import applied_settings, parameters
from app.core.applied_settings import SettingComparison

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"

READINESS_TIMEOUT_SECONDS = 90
POLL_INTERVAL_SECONDS = 1.0

# Queried alongside the tuned parameters, not as one of them. pg4all points
# postgres at its own config_file, which bypasses the postgresql.conf the
# official image writes into PGDATA — where upstream sets listen_addresses.
# Getting this wrong makes every built image refuse connections on a
# published port, so the smoke test checks it explicitly rather than
# trusting that conf_generator emitted it.
EXPECTED_LISTEN_ADDRESSES = "*"

_SETTINGS_QUERY = (
    "SELECT name || '|' || setting || '|' || coalesce(unit, '') "
    "FROM pg_settings WHERE name IN ({names})"
)


@dataclass
class SmokeResult:
    status: str  # PASSED | FAILED | SKIPPED
    summary: str
    log: list[str] = field(default_factory=list)
    comparisons: list[SettingComparison] = field(default_factory=list)

    @property
    def mismatches(self) -> list[SettingComparison]:
        return applied_settings.mismatches(self.comparisons)


def _exec(container, cmd: list[str], user: str = "postgres") -> tuple[int, str]:
    exit_code, output = container.exec_run(cmd, user=user)
    return exit_code, output.decode("utf-8", errors="replace").strip()


def _wait_until_ready(container, deadline_polls: int) -> tuple[bool, str]:
    """Poll pg_isready until the server answers or the container dies."""
    for _ in range(deadline_polls):
        container.reload()
        if container.status not in ("running", "created"):
            return False, f"Container exited early with status {container.status!r}."

        exit_code, _ = _exec(container, ["pg_isready", "-q", "-U", "postgres"])
        if exit_code == 0:
            return True, "Server accepted connections."

        time.sleep(POLL_INTERVAL_SECONDS)

    return False, (
        f"Server did not accept connections within {READINESS_TIMEOUT_SECONDS}s."
    )


def _query_settings(container, names: list[str]) -> list[tuple[str, str, str]]:
    quoted = ", ".join(f"'{name}'" for name in names)
    exit_code, output = _exec(
        container,
        ["psql", "-U", "postgres", "-At", "-c", _SETTINGS_QUERY.format(names=quoted)],
    )
    if exit_code != 0:
        raise RuntimeError(f"Could not read pg_settings: {output}")

    rows = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def run_smoke_test(tag: str, requested: dict[str, float | str]) -> SmokeResult:
    """Boot `tag`, compare its live settings to `requested`, tear it down."""
    log: list[str] = []
    try:
        client = docker.from_env()
    except DockerException as exc:
        return SmokeResult(
            status=SKIPPED,
            summary="Could not reach the Docker daemon to run the smoke test.",
            log=[str(exc)],
        )

    container = None
    try:
        container = client.containers.run(
            tag,
            detach=True,
            environment={"POSTGRES_PASSWORD": secrets.token_urlsafe(16)},
            # No ports published and no host mounts: the server is reached
            # over its own unix socket via docker exec.
            publish_all_ports=False,
        )
        log.append(f"Started {tag} as {container.short_id}.")

        polls = int(READINESS_TIMEOUT_SECONDS / POLL_INTERVAL_SECONDS)
        ready, message = _wait_until_ready(container, polls)
        log.append(message)
        if not ready:
            log.extend(
                container.logs(tail=40).decode("utf-8", errors="replace").splitlines()
            )
            return SmokeResult(
                status=FAILED,
                summary="PostgreSQL did not start with this configuration.",
                log=log,
            )

        names = [spec.key for spec in parameters.PARAMETER_SPECS]
        rows = _query_settings(container, names + ["listen_addresses"])

        listen_addresses = next(
            (setting for name, setting, _ in rows if name == "listen_addresses"), None
        )
        listen_ok = listen_addresses == EXPECTED_LISTEN_ADDRESSES
        log.append(f"listen_addresses = {listen_addresses!r}")

        comparisons = applied_settings.compare(
            parameters.PARAMETER_SPECS, requested, rows
        )
        mismatched = applied_settings.mismatches(comparisons)

        if not listen_ok:
            return SmokeResult(
                status=FAILED,
                summary=(
                    f"The server started, but listen_addresses is "
                    f"{listen_addresses!r} — it only accepts connections from "
                    "inside its own container, so a published port and any "
                    "companion service will both fail to connect."
                ),
                log=log,
                comparisons=comparisons,
            )

        if mismatched:
            return SmokeResult(
                status=FAILED,
                summary=(
                    f"The server started, but {len(mismatched)} of "
                    f"{len(comparisons)} settings came up different from what "
                    "was requested."
                ),
                log=log,
                comparisons=comparisons,
            )

        return SmokeResult(
            status=PASSED,
            summary=(
                f"PostgreSQL started and all {len(comparisons)} tuned settings "
                "took effect as requested."
            ),
            log=log,
            comparisons=comparisons,
        )

    except (DockerException, RuntimeError) as exc:
        log.append(str(exc))
        return SmokeResult(
            status=FAILED,
            summary="The smoke test could not complete.",
            log=log,
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True, v=True)
            except DockerException as exc:  # pragma: no cover - teardown only
                log.append(f"Warning: could not remove the test container: {exc}")
