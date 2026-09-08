from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.builder.dockerfile_gen import create_build_context
from app.builder.docker_build import build_image
from app.core import hardware, parameters, pg_versions, workloads
from app.core.conf_generator import render_conf

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="pg4all console")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _resolve_selection(pg_version: str | None, workload_key: str | None, tier_key: str | None):
    try:
        version = pg_versions.get(pg_version) if pg_version else pg_versions.list_versions()[0]
    except ValueError:
        version = pg_versions.list_versions()[0]

    try:
        workload = workloads.get(workload_key) if workload_key else workloads.get("mixed")
    except ValueError:
        workload = workloads.get("mixed")

    try:
        tier = hardware.get(tier_key) if tier_key else hardware.recommend_for_workload(workload.key)
    except ValueError:
        tier = hardware.recommend_for_workload(workload.key)

    return version, workload, tier


def _tuner_stats(groups: list[parameters.CategoryGroup]) -> dict:
    all_rows = [row for group in groups for row in group.rows]
    tuned_rows = [row for row in all_rows if row.recommended_value != row.default_value]

    avg_impact = (
        round(sum(row.spec.impact for row in tuned_rows) / len(tuned_rows))
        if tuned_rows
        else 0
    )
    restart_required = any(row.spec.restart_required for row in tuned_rows)

    return {
        "tuned_count": len(tuned_rows),
        "total_count": len(all_rows),
        "avg_impact": avg_impact,
        "restart_required": restart_required,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request, pg_version: str | None = None, workload: str | None = None, tier: str | None = None):
    version, wl, hw_tier = _resolve_selection(pg_version, workload, tier)
    groups = parameters.build_tuner_groups(wl, hw_tier)

    return templates.TemplateResponse(
        request,
        "tuner.html",
        {
            "pg_versions": pg_versions.list_versions(),
            "workloads": workloads.WORKLOADS,
            "tiers": hardware.TIERS,
            "selected_version": version,
            "selected_workload": wl,
            "selected_tier": hw_tier,
            "groups": groups,
            "stats": _tuner_stats(groups),
        },
    )


@app.post("/build", response_class=HTMLResponse)
async def build(request: Request):
    form = await request.form()

    version, wl, hw_tier = _resolve_selection(
        form.get("pg_version"), form.get("workload"), form.get("tier")
    )
    groups = parameters.build_tuner_groups(wl, hw_tier)
    recommended_by_key = {
        row.spec.key: row.recommended_value for group in groups for row in group.rows
    }

    settings = {}
    for spec in parameters.PARAMETER_SPECS:
        raw = form.get(f"p_{spec.key}")
        if spec.kind == "enum":
            value = raw if raw in (spec.choices or ()) else recommended_by_key[spec.key]
        else:
            try:
                value = float(raw) if raw is not None else recommended_by_key[spec.key]
            except ValueError:
                value = recommended_by_key[spec.key]
        settings[spec.key] = parameters.format_conf_value(spec, value)

    conf_text = render_conf(settings)
    context_dir = create_build_context(version.major, conf_text)
    tag = f"pg4all/postgres:{version.major}-{wl.key}-{hw_tier.key}"
    result = build_image(context_dir, tag)

    return templates.TemplateResponse(
        request,
        "result.html",
        {"result": result, "pg_version": version},
    )
