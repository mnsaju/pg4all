"""Tests for app/core/monitoring.py.

The interesting property is that the dashboard carries *this build's*
tuning values rather than generic ones — that's the whole reason pg4all
generates a dashboard instead of shipping a community one.
"""

import json

from app.core import hardware, monitoring, parameters, workloads


def conf_values(workload: str = "oltp", tier: str = "medium") -> dict[str, float | str]:
    """The values the conf asks for, exactly as _run_build computes them."""
    groups = parameters.build_tuner_groups(
        workloads.get(workload), hardware.get(tier)
    )
    recommended = {
        row.spec.key: row.recommended_value for group in groups for row in group.rows
    }
    return {
        spec.key: parameters.parse_value(
            spec, parameters.format_conf_value(spec, recommended[spec.key])
        )
        for spec in parameters.PARAMETER_SPECS
    }


def panels_by_title(values) -> dict:
    return {p["title"]: p for p in monitoring.build_dashboard(values)["panels"]}


def panels_by_title_for(values, pg_major) -> dict:
    return {
        p["title"]: p
        for p in monitoring.build_dashboard(values, pg_major)["panels"]
    }


def test_prometheus_scrapes_the_exporter_by_service_name():
    """Service name on the compose network, not a host port — moving the
    published port must not break the scrape."""
    config = monitoring.render_prometheus_config()
    assert "postgres_exporter:9187" in config
    assert "job_name: postgres" in config


def test_the_datasource_points_at_prometheus_on_the_compose_network():
    datasource = monitoring.render_grafana_datasource()
    assert "http://prometheus:9090" in datasource
    assert monitoring.DATASOURCE_UID in datasource


def test_the_dashboard_provider_points_where_the_compose_file_mounts_dashboards():
    provider = monitoring.render_grafana_dashboard_provider()
    assert monitoring.GRAFANA_DASHBOARD_DIR in provider


def test_the_dashboard_has_the_nine_agreed_panels():
    titles = set(panels_by_title(conf_values()))
    assert titles == {
        "Up",
        "Longest running transaction",
        "Database size",
        "Connections by state",
        "Cache hit ratio",
        "Transactions per second",
        "Checkpoints: timed vs requested",
        "Deadlocks",
        "Locks by mode",
    }


def test_the_connections_panel_draws_the_line_at_this_builds_max_connections():
    """The point of generating the dashboard: a stock one plots connections
    against an arbitrary axis, this one against the ceiling pg4all set."""
    values = conf_values("oltp", "medium")
    assert values["max_connections"] == 300

    steps = panels_by_title(values)["Connections by state"]["fieldConfig"]["defaults"][
        "thresholds"
    ]["steps"]
    assert [s["value"] for s in steps] == [None, 240, 300]  # 80% warn, then the ceiling


def test_the_threshold_moves_when_the_tuning_does():
    low = panels_by_title({**conf_values(), "max_connections": 100})
    high = panels_by_title({**conf_values(), "max_connections": 400})

    def ceiling(panels):
        return panels["Connections by state"]["fieldConfig"]["defaults"]["thresholds"][
            "steps"
        ][-1]["value"]

    assert ceiling(low) == 100
    assert ceiling(high) == 400


def test_panels_name_the_tuned_values_they_relate_to():
    values = {
        **conf_values(),
        "shared_buffers": 4096,
        "max_wal_size": 4096,
        "checkpoint_timeout": 15,
    }
    panels = panels_by_title(values)

    assert "4.0 GB" in panels["Cache hit ratio"]["description"]
    checkpoint_description = panels["Checkpoints: timed vs requested"]["description"]
    assert "4.0 GB" in checkpoint_description       # max_wal_size
    assert "15 min" in checkpoint_description       # checkpoint_timeout


def test_the_cache_hit_ratio_cannot_divide_by_zero():
    """An idle database has no reads at all, and a bare ratio would render
    the panel as NaN rather than as 'nothing happening'."""
    expr = panels_by_title(conf_values())["Cache hit ratio"]["targets"][0]["expr"]
    assert "clamp_min" in expr


def test_every_panel_is_wired_to_the_provisioned_datasource():
    for panel in monitoring.build_dashboard(conf_values())["panels"]:
        assert panel["datasource"]["uid"] == monitoring.DATASOURCE_UID
        for target in panel["targets"]:
            assert target["expr"]


def test_panels_do_not_overlap_on_the_grid():
    """Grafana will render overlapping panels, just badly."""
    occupied = set()
    for panel in monitoring.build_dashboard(conf_values())["panels"]:
        g = panel["gridPos"]
        cells = {
            (x, y)
            for x in range(g["x"], g["x"] + g["w"])
            for y in range(g["y"], g["y"] + g["h"])
        }
        assert not (cells & occupied), panel["title"]
        assert g["x"] + g["w"] <= 24, panel["title"]
        occupied |= cells


def test_the_rendered_dashboard_is_valid_json():
    parsed = json.loads(monitoring.render_dashboard_json(conf_values()))
    assert parsed["uid"] == monitoring.DASHBOARD_UID
    assert len(parsed["panels"]) == 9


def test_is_selected_only_fires_for_monitoring_services():
    assert monitoring.is_selected({"grafana"})
    assert monitoring.is_selected({"prometheus"})
    assert not monitoring.is_selected({"pgbouncer", "pgadmin"})
    assert not monitoring.is_selected(set())


def test_checkpoint_metrics_follow_the_view_postgres_actually_has():
    """PostgreSQL 17 moved the checkpoint counters out of pg_stat_bgwriter
    into pg_stat_checkpointer. Asking the wrong major for the wrong pair
    renders a perfectly good-looking panel with nothing in it — which is
    exactly what happened before this was version-aware."""
    assert monitoring.checkpoint_metrics("16") == (
        "pg_stat_bgwriter_checkpoints_timed_total",
        "pg_stat_bgwriter_checkpoints_req_total",
    )
    for major in ("17", "18", 18):
        assert monitoring.checkpoint_metrics(major) == (
            "pg_stat_checkpointer_num_timed_total",
            "pg_stat_checkpointer_num_requested_total",
        ), major


def test_the_checkpoint_panel_queries_the_right_metrics_per_major():
    values = conf_values()
    for major, expected in (("16", "pg_stat_bgwriter"), ("17", "pg_stat_checkpointer")):
        panel = panels_by_title_for(values, major)["Checkpoints: timed vs requested"]
        exprs = " ".join(t["expr"] for t in panel["targets"])
        assert expected in exprs, major


def test_an_unparseable_major_falls_back_to_the_current_view():
    """Better to match the majors pg4all actually ships than to guess old."""
    assert monitoring.checkpoint_metrics("nonsense") == (
        "pg_stat_checkpointer_num_timed_total",
        "pg_stat_checkpointer_num_requested_total",
    )
