"""Triggers the actual `docker build` on the host VM's Docker daemon.

Uses the Docker SDK talking over the socket mounted into the console
container (Docker-outside-of-Docker: /var/run/docker.sock) rather than
shelling out to the `docker` CLI binary, so the console image doesn't
need the CLI installed — just the `docker` Python package and socket
access. That socket is host-root-equivalent: this console must stay
behind trusted-operator access only, never exposed publicly.

The low-level `client.api.build` is used rather than the friendlier
`client.images.build`, because the latter only returns once the build has
finished and the whole point here is to show progress while it runs. The
cost is that the low-level call reports failure as an `error` key in the
stream instead of raising, so that has to be handled explicitly — see
`_consume`.
"""

from collections.abc import Callable
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, ImageNotFound


class BuildResult:
    def __init__(self, ok: bool, tag: str, log: list[str]):
        self.ok = ok
        self.tag = tag
        self.log = log


def remove_image(tag: str) -> tuple[bool, str]:
    """Remove an image tag from the daemon.

    Deliberately not forced: if a container is still running from this
    image, the daemon refuses and that refusal is reported rather than
    overridden. Someone is using it, and a console cleaning up its own
    records is not reason enough to pull it out from under them.
    """
    try:
        client = docker.from_env()
    except DockerException as exc:
        return False, f"Could not reach the Docker daemon: {exc}"

    try:
        client.images.remove(tag)
    except ImageNotFound:
        return False, f"Image {tag} was not on the daemon."
    except APIError as exc:
        reason = getattr(exc, "explanation", None) or str(exc)
        return False, f"Could not remove image {tag}: {reason}"
    except DockerException as exc:
        return False, f"Could not remove image {tag}: {exc}"

    return True, f"Removed image {tag}."


def _emit(log: list[str], on_log: Callable[[str], None] | None, line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    log.append(line)
    if on_log is not None:
        on_log(line)


def _consume(
    stream, log: list[str], on_log: Callable[[str], None] | None
) -> str | None:
    """Drain the build stream, returning an error message or None.

    `client.api.build` yields dicts as the daemon works: `stream` for
    output, `error` when it fails. Unlike the high-level call it raises
    nothing on a failed build, so a caller that ignores `error` would
    treat a failure as a success.
    """
    error: str | None = None
    for entry in stream:
        if not isinstance(entry, dict):
            continue
        if "stream" in entry:
            for line in str(entry["stream"]).splitlines():
                _emit(log, on_log, line)
        elif "error" in entry:
            error = str(entry["error"])
            _emit(log, on_log, error)
        elif "status" in entry:  # layer pulls
            progress = entry.get("progress") or ""
            _emit(log, on_log, f"{entry['status']} {progress}".rstrip())
    return error


def build_image(
    context_dir: Path,
    tag: str,
    on_log: Callable[[str], None] | None = None,
) -> BuildResult:
    """Build `context_dir` as `tag`.

    `on_log` is called with each output line as it arrives, so a caller can
    persist progress while the build runs. Omit it and this behaves as it
    always did, returning the whole log at the end.
    """
    log: list[str] = []
    try:
        client = docker.from_env()
    except DockerException as exc:
        message = f"Could not reach the Docker daemon: {exc}"
        _emit(log, on_log, message)
        return BuildResult(ok=False, tag=tag, log=log)

    try:
        stream = client.api.build(
            path=str(context_dir), tag=tag, rm=True, decode=True
        )
        error = _consume(stream, log, on_log)
    except DockerException as exc:
        _emit(log, on_log, f"Docker error: {exc}")
        return BuildResult(ok=False, tag=tag, log=log)

    if error:
        _emit(log, on_log, "Build failed.")
        return BuildResult(ok=False, tag=tag, log=log)

    return BuildResult(ok=True, tag=tag, log=log)
