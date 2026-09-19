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

from app.core.services import ServiceSpec, compose_fragment

_POSTGRES_SERVICE_TEMPLATE = """\
services:
  postgres:
    image: {image_tag}
    environment:
      POSTGRES_PASSWORD: '{password}'
    ports:
      - "5432:5432"
    restart: unless-stopped
"""


def render_compose(
    image_tag: str,
    username: str,
    password: str,
    selected_services: list[ServiceSpec],
) -> str | None:
    sidecars = [s for s in selected_services if s.mode == "sidecar"]
    if not sidecars:
        return None

    text = _POSTGRES_SERVICE_TEMPLATE.format(image_tag=image_tag, password=password)
    for spec in sidecars:
        text += "\n" + compose_fragment(spec, username, password)
    return text


def write_compose(
    context_dir: Path,
    image_tag: str,
    username: str,
    password: str,
    selected_services: list[ServiceSpec],
) -> Path | None:
    text = render_compose(image_tag, username, password, selected_services)
    if text is None:
        return None

    path = context_dir / "docker-compose.yml"
    path.write_text(text)
    return path
