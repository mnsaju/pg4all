"""Triggers the actual `docker build` on the host VM's Docker daemon.

Uses the Docker SDK talking over the socket mounted into the console
container (Docker-outside-of-Docker: /var/run/docker.sock) rather than
shelling out to the `docker` CLI binary, so the console image doesn't
need the CLI installed — just the `docker` Python package and socket
access. That socket is host-root-equivalent: this console must stay
behind trusted-operator access only, never exposed publicly.
"""

from pathlib import Path

import docker
from docker.errors import BuildError, DockerException


class BuildResult:
    def __init__(self, ok: bool, tag: str, log: list[str]):
        self.ok = ok
        self.tag = tag
        self.log = log


def build_image(context_dir: Path, tag: str) -> BuildResult:
    log: list[str] = []
    try:
        client = docker.from_env()
    except DockerException as exc:
        return BuildResult(
            ok=False,
            tag=tag,
            log=[f"Could not reach the Docker daemon: {exc}"],
        )

    try:
        _image, build_log = client.images.build(
            path=str(context_dir), tag=tag, rm=True
        )
        for entry in build_log:
            if "stream" in entry:
                log.append(entry["stream"].rstrip())
        return BuildResult(ok=True, tag=tag, log=log)
    except BuildError as exc:
        for entry in exc.build_log:
            if "stream" in entry:
                log.append(entry["stream"].rstrip())
        log.append(f"Build failed: {exc}")
        return BuildResult(ok=False, tag=tag, log=log)
    except DockerException as exc:
        return BuildResult(ok=False, tag=tag, log=[f"Docker error: {exc}"])
