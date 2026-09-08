from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.builder.dockerfile_gen import create_build_context
from app.builder.docker_build import build_image
from app.core import hardware, pg_versions, workloads
from app.core.conf_generator import generate_conf, render_conf

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="pg4all console")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "pg_versions": pg_versions.list_versions(),
            "workloads": workloads.WORKLOADS,
        },
    )


@app.post("/generate", response_class=HTMLResponse)
def generate(
    request: Request,
    pg_version: str = Form(...),
    workload_key: str = Form(...),
    hardware_tier: str | None = Form(None),
):
    workload = workloads.get(workload_key)
    tier = (
        hardware.get(hardware_tier)
        if hardware_tier
        else hardware.recommend_for_workload(workload_key)
    )
    settings = generate_conf(workload, tier)
    conf_text = render_conf(settings)

    return templates.TemplateResponse(
        request,
        "workload.html",
        {
            "pg_version": pg_version,
            "workload": workload,
            "tier": tier,
            "tiers": hardware.TIERS,
            "settings": settings,
            "conf_text": conf_text,
        },
    )


@app.post("/build", response_class=HTMLResponse)
def build(
    request: Request,
    pg_version: str = Form(...),
    workload_key: str = Form(...),
    hardware_tier: str = Form(...),
):
    workload = workloads.get(workload_key)
    tier = hardware.get(hardware_tier)
    settings = generate_conf(workload, tier)
    conf_text = render_conf(settings)

    context_dir = create_build_context(pg_version, conf_text)
    tag = f"pg4all/postgres:{pg_version}-{workload.key}-{tier.key}"
    result = build_image(context_dir, tag)

    return templates.TemplateResponse(
        request,
        "result.html",
        {"result": result},
    )
