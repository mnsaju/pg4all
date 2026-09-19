"""Generates the per-build docker-compose.yml for the companion sidecar
services (PgBouncer, the metrics exporter) selected alongside a build.

Written into the same build_output/<build_id>/ directory dockerfile_gen.py
writes the Dockerfile/conf into, so it's reachable at a stable path once
that directory is bind-mounted to the host (see root docker-compose.yml).

The `postgres` service references the already-built image by tag rather
than a `build:` directive — by the time this is written, docker_build.py
has already pushed that image into the host daemon's store via the SDK.
"""

from pathlib import Path

from app.core import ports
from app.core.services import ServiceSpec, compose_fragment, named_volumes

_POSTGRES_SERVICE_TEMPLATE = """\
services:
  postgres:
    image: {image_tag}
    environment:
      POSTGRES_PASSWORD: '{password}'
    ports:
      - "{published}"
    restart: unless-stopped
"""


def render_compose(
    image_tag: str,
    username: str,
    password: str,
    selected_services: list[ServiceSpec],
    host_ports: dict[str, int] | None = None,
) -> str | None:
    """`host_ports` maps a port key from app/core/ports.py to its host-side
    port. Omitted, every service takes its default — which is what the
    generated stack did before any of this was configurable."""
    sidecars = [s for s in selected_services if s.mode == "sidecar"]
    if not sidecars:
        return None

    host_ports = {**ports.defaults(), **(host_ports or {})}

    postgres_spec = ports.get(ports.POSTGRES_PORT_KEY)
    text = _POSTGRES_SERVICE_TEMPLATE.format(
        image_tag=image_tag,
        password=password,
        published=postgres_spec.published(host_ports[ports.POSTGRES_PORT_KEY]),
    )
    for spec in sidecars:
        port_spec = ports.get(spec.key)
        text += "\n" + compose_fragment(
            spec, username, password, port_spec.published(host_ports[spec.key])
        )

    # Named volumes have to be declared at the top level as well as
    # referenced by the services using them, or compose rejects the file.
    volumes = named_volumes(sidecars)
    if volumes:
        text += "\nvolumes:\n"
        text += "".join(f"  {volume}:\n" for volume in volumes)

    return text


def write_compose(
    context_dir: Path,
    image_tag: str,
    username: str,
    password: str,
    selected_services: list[ServiceSpec],
    host_ports: dict[str, int] | None = None,
) -> Path | None:
    text = render_compose(
        image_tag, username, password, selected_services, host_ports
    )
    if text is None:
        return None

    path = context_dir / "docker-compose.yml"
    path.write_text(text)
    return path
