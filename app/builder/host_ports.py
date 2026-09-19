"""What host ports the Docker daemon already has spoken for.

The console can't usefully check host port availability by binding a
socket: it runs in its own network namespace, so a port that looks free
from inside the container may be taken on the host and vice versa. What
it can do is ask the daemon what its running containers publish, which
covers the case that actually bites — another stack already holding 5432.

Ports held by non-Docker processes on the host are invisible here. This
narrows the problem rather than solving it, which is why a conflict is
reported as a warning and never blocks a build.
"""

import docker
from docker.errors import DockerException


def published_host_ports() -> dict[int, str]:
    """Map each published host port to the container name holding it.

    Returns an empty mapping if the daemon can't be reached — an advisory
    check that can't run is not a reason to fail anything.
    """
    try:
        client = docker.from_env()
        containers = client.containers.list()
    except DockerException:
        return {}

    in_use: dict[int, str] = {}
    for container in containers:
        try:
            bindings = container.attrs["NetworkSettings"]["Ports"] or {}
        except (KeyError, TypeError):  # pragma: no cover - malformed inspect
            continue
        for host_bindings in bindings.values():
            for binding in host_bindings or []:
                try:
                    port = int(binding["HostPort"])
                except (KeyError, TypeError, ValueError):
                    continue
                in_use.setdefault(port, container.name)
    return in_use
