"""Builds the on-disk context (Dockerfile + postgresql.conf) that gets
handed to the Docker daemon. Debian-slim only for v1 — the official
postgres image tags used here (e.g. "17-bookworm") already are the
Debian-slim lineage; no UBI/RHEL variant yet."""

import uuid
from pathlib import Path

from app.core.pg_versions import docker_tag_for

BUILD_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "build_output"

_DOCKERFILE_TEMPLATE = """\
FROM postgres:{tag}

COPY postgresql.conf /etc/postgresql/postgresql.conf

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
"""


def create_build_context(pg_version: str, conf_text: str) -> Path:
    tag = docker_tag_for(pg_version)
    context_dir = BUILD_OUTPUT_DIR / uuid.uuid4().hex
    context_dir.mkdir(parents=True, exist_ok=False)

    (context_dir / "Dockerfile").write_text(_DOCKERFILE_TEMPLATE.format(tag=tag))
    (context_dir / "postgresql.conf").write_text(conf_text)

    return context_dir
