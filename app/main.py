import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.builder import compose_gen, credential_store
from app.builder.dockerfile_gen import BUILD_OUTPUT_DIR, create_build_context
from app.builder.docker_build import build_image
from app.core import (
    credentials, extensions, hardware, parameters, pg_versions, services, validation, workloads,
)
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


def _build_dir_flags(build_dir: Path) -> dict:
    """Which companion-service instructions to show for a build, derived
    from what's actually on disk rather than tracked separately — works
    identically right after a build and when revisiting it later."""
    compose_path = build_dir / "docker-compose.yml"
    compose_text = compose_path.read_text() if compose_path.exists() else ""
    return {
        "has_compose": bool(compose_text),
        "has_pgbackrest": (build_dir / "pgbackrest.conf").exists(),
        "has_pgadmin": "pgadmin:" in compose_text,
        "pgadmin_email": services.PGADMIN_EMAIL,
    }


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


def _submitted_values(form, wl, hw_tier) -> dict[str, float | str]:
    """Every tunable parameter's submitted value, in its native unit.

    Anything missing or unparseable falls back to the recommendation for
    the workload/tier pair, so a partial or hand-crafted POST can never
    produce a half-populated conf. Shared by `/validate` and `/build` so
    both judge exactly the same numbers.
    """
    groups = parameters.build_tuner_groups(wl, hw_tier)
    recommended_by_key = {
        row.spec.key: row.recommended_value for group in groups for row in group.rows
    }

    values: dict[str, float | str] = {}
    for spec in parameters.PARAMETER_SPECS:
        raw = form.get(f"p_{spec.key}")
        if spec.kind == "enum":
            values[spec.key] = (
                raw if raw in (spec.choices or ()) else recommended_by_key[spec.key]
            )
        else:
            try:
                values[spec.key] = (
                    float(raw) if raw is not None else recommended_by_key[spec.key]
                )
            except ValueError:
                values[spec.key] = recommended_by_key[spec.key]
    return values


@app.get("/", response_class=HTMLResponse)
def index(request: Request, pg_version: str | None = None, workload: str | None = None, tier: str | None = None):
    version, wl, hw_tier = _resolve_selection(pg_version, workload, tier)
    groups = parameters.build_tuner_groups(wl, hw_tier)
    default_extension_keys = {
        e.key for e in extensions.list_extensions() if e.default_selected
    }
    default_service_keys = {
        s.key for s in services.list_services() if s.default_selected
    }

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
            "extensions": extensions.list_extensions(),
            "selected_extension_keys": default_extension_keys,
            "services": services.list_services(),
            "selected_service_keys": default_service_keys,
            "findings": validation.validate(
                {
                    row.spec.key: row.recommended_value
                    for group in groups
                    for row in group.rows
                },
                hw_tier,
                frozenset(default_service_keys),
            ),
        },
    )


@app.post("/validate", response_class=HTMLResponse)
async def validate_settings(request: Request):
    """Findings for the parameter set currently in the form.

    The tuner page POSTs here as the operator drags sliders and renders
    the returned fragment, so the thresholds stay in `app/core/validation.py`
    instead of being reimplemented in JavaScript.
    """
    form = await request.form()
    _version, wl, hw_tier = _resolve_selection(
        form.get("pg_version"), form.get("workload"), form.get("tier")
    )
    findings = validation.validate(
        _submitted_values(form, wl, hw_tier),
        hw_tier,
        frozenset(form.getlist("services")),
    )
    return templates.TemplateResponse(
        request, "_findings.html", {"findings": findings}
    )


@app.post("/build", response_class=HTMLResponse)
async def build(request: Request):
    form = await request.form()

    version, wl, hw_tier = _resolve_selection(
        form.get("pg_version"), form.get("workload"), form.get("tier")
    )
    values = _submitted_values(form, wl, hw_tier)
    findings = validation.validate(
        values, hw_tier, frozenset(form.getlist("services"))
    )

    # An error-level finding describes a configuration that will start and
    # then fail later under load, so it stops the build here rather than
    # spending minutes producing an image that can't hold up. The operator
    # can still override — this is advisory arithmetic, and they may know
    # something about the deployment that the tier presets don't capture.
    if validation.has_errors(findings) and form.get("acknowledge") != "1":
        return templates.TemplateResponse(
            request,
            "confirm_build.html",
            {
                "findings": findings,
                "tier": hw_tier,
                "resubmit_fields": [
                    (name, value)
                    for name, value in form.multi_items()
                    if name != "acknowledge"
                ],
            },
            status_code=422,
        )

    settings = {
        spec.key: parameters.format_conf_value(spec, values[spec.key])
        for spec in parameters.PARAMETER_SPECS
    }

    selected_extensions = extensions.resolve(form.getlist("extensions"))
    preload = extensions.preload_libraries(selected_extensions)
    if preload:
        settings["shared_preload_libraries"] = f"'{','.join(preload)}'"
    settings.update(extensions.extra_conf_settings(selected_extensions))

    selected_services = services.resolve(form.getlist("services"))
    service_apt_packages = services.apt_packages(selected_services)
    pgbackrest_conf = (
        services.render_pgbackrest_conf(version.major)
        if any(s.key == "pgbackrest" for s in selected_services)
        else None
    )

    conf_text = render_conf(settings)
    build_id = uuid.uuid4().hex
    context_dir = create_build_context(
        version.major,
        conf_text,
        selected_extensions,
        build_id=build_id,
        extra_apt_packages=service_apt_packages,
        pgbackrest_conf=pgbackrest_conf,
    )
    tag = f"pg4all/postgres:{version.major}-{wl.key}-{hw_tier.key}"
    result = build_image(context_dir, tag)

    password = credentials.generate_password()
    record = credentials.CredentialRecord(
        build_id=build_id,
        username=credentials.ADMIN_USERNAME,
        password=password,
        image_tag=tag,
        pg_major=version.major,
        created_at=datetime.now(UTC).isoformat(),
        build_ok=result.ok,
    )
    credential_store.save_credential(record)

    if result.ok:
        compose_gen.write_compose(context_dir, tag, record.username, password, selected_services)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "result": result,
            "pg_version": version,
            "credential": record,
            "findings": findings,
            **_build_dir_flags(context_dir),
        },
    )


@app.get("/builds", response_class=HTMLResponse)
def list_builds(request: Request):
    records = credential_store.list_credentials()
    return templates.TemplateResponse(request, "builds.html", {"records": records})


@app.get("/credentials/{build_id}", response_class=HTMLResponse)
def show_credential(request: Request, build_id: str):
    record = credential_store.load_credential(build_id)
    if record is None:
        return templates.TemplateResponse(
            request, "credential_not_found.html", {"build_id": build_id}, status_code=404
        )

    build_dir = BUILD_OUTPUT_DIR / build_id
    return templates.TemplateResponse(
        request,
        "credential.html",
        {"credential": record, **_build_dir_flags(build_dir)},
    )
