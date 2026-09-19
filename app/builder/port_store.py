"""Remembers which host ports a build chose.

The result page can read them straight off the request, but
`/credentials/<build_id>` reproduces the same run instructions long
afterwards and has only the build directory to go on. The compose file
would carry them for a build with sidecars; a build without any has no
compose file at all, and still publishes a Postgres port.

So they're written next to the Dockerfile, in the same build directory
that's bind-mounted to the host — the same "derive it from what's on
disk" approach `_build_dir_flags` already takes, rather than a second
place to keep in sync.
"""

import json
from pathlib import Path

from app.core import ports

PORTS_FILENAME = "ports.json"


def write_ports(context_dir: Path, host_ports: dict[str, int]) -> Path:
    path = context_dir / PORTS_FILENAME
    path.write_text(json.dumps(host_ports, indent=2, sort_keys=True) + "\n")
    return path


def read_ports(context_dir: Path) -> dict[str, int]:
    """The build's ports, defaulting anything absent.

    Builds made before ports were configurable have no ports.json, and
    they used exactly the defaults — so falling back to them shows those
    builds the same instructions they were given originally.
    """
    path = context_dir / PORTS_FILENAME
    if not path.exists():
        return ports.defaults()

    try:
        stored = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ports.defaults()

    resolved = ports.defaults()
    for key, value in (stored or {}).items():
        if key in resolved:
            try:
                resolved[key] = int(value)
            except (TypeError, ValueError):
                continue
    return resolved
