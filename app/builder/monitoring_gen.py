"""Writes the Prometheus and Grafana config for a build's monitoring stack.

Mirrors compose_gen: app/core/monitoring.py decides what the files say,
this decides where they go.

They land in `build_output/<build_id>/monitoring/`, beside the generated
docker-compose.yml that bind-mounts them with relative paths — which
resolve against the compose file's own directory, so they work as long as
the operator runs `docker compose up` from the build directory, exactly as
the instructions say to.

Called after the image build rather than during context creation, and
deliberately so: everything inside the build directory at build time is
uploaded to the Docker daemon as build context, and none of this belongs
in the image.
"""

from pathlib import Path

from app.core import monitoring

MONITORING_DIRNAME = "monitoring"


def write_monitoring_files(
    context_dir: Path, conf_values: dict[str, float | str], pg_major: str | int = "17"
) -> list[Path]:
    root = context_dir / MONITORING_DIRNAME
    provisioning = root / "grafana" / "provisioning"
    dashboards = root / "grafana" / "dashboards"

    files = {
        root / "prometheus.yml": monitoring.render_prometheus_config(),
        provisioning / "datasources" / "prometheus.yml":
            monitoring.render_grafana_datasource(),
        provisioning / "dashboards" / "pg4all.yml":
            monitoring.render_grafana_dashboard_provider(),
        dashboards / "pg4all.json": monitoring.render_dashboard_json(
            conf_values, pg_major
        ),
    }

    written = []
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    return written
