"""Builds the on-disk context (Dockerfile + postgresql.conf) that gets
handed to the Docker daemon. Debian-slim only for v1 — the official
postgres image tags used here (e.g. "17-bookworm") already are the
Debian-slim lineage; no UBI/RHEL variant yet."""

import uuid
from pathlib import Path

from app.core.extensions import ExtensionSpec, apt_packages, render_init_sql
from app.core.pg_versions import docker_tag_for

BUILD_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "build_output"

_APT_LAYER_TEMPLATE = """\
RUN apt-get update \\
 && apt-get install -y --no-install-recommends {packages} \\
 && rm -rf /var/lib/apt/lists/*

"""

_INIT_SQL_COPY = "COPY init-extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql\n"

_PGBACKREST_CONF_COPY = "COPY pgbackrest.conf /etc/pgbackrest/pgbackrest.conf\n"

_DOCKERFILE_TEMPLATE = """\
FROM postgres:{tag}

{apt_layer}COPY postgresql.conf /etc/postgresql/postgresql.conf
{init_sql_copy}{pgbackrest_conf_copy}
CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
"""


def create_build_context(
    pg_version: str,
    conf_text: str,
    extensions: list[ExtensionSpec] | None = None,
    build_id: str | None = None,
    extra_apt_packages: list[str] | None = None,
    pgbackrest_conf: str | None = None,
) -> Path:
    extensions = extensions or []
    extra_apt_packages = extra_apt_packages or []
    build_id = build_id or uuid.uuid4().hex
    tag = docker_tag_for(pg_version)
    context_dir = BUILD_OUTPUT_DIR / build_id
    context_dir.mkdir(parents=True, exist_ok=False)

    packages = apt_packages(extensions, pg_version) + extra_apt_packages
    apt_layer = _APT_LAYER_TEMPLATE.format(packages=" ".join(packages)) if packages else ""
    init_sql_copy = _INIT_SQL_COPY if extensions else ""
    pgbackrest_conf_copy = _PGBACKREST_CONF_COPY if pgbackrest_conf else ""

    (context_dir / "Dockerfile").write_text(
        _DOCKERFILE_TEMPLATE.format(
            tag=tag,
            apt_layer=apt_layer,
            init_sql_copy=init_sql_copy,
            pgbackrest_conf_copy=pgbackrest_conf_copy,
        )
    )
    (context_dir / "postgresql.conf").write_text(conf_text)
    if extensions:
        (context_dir / "init-extensions.sql").write_text(render_init_sql(extensions))
    if pgbackrest_conf:
        (context_dir / "pgbackrest.conf").write_text(pgbackrest_conf)

    return context_dir
