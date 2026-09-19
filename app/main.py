import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.builder import (
    auth_store, build_store, compose_gen, credential_store, monitoring_gen, port_store,
)
from app.builder.host_ports import published_host_ports
from app.builder.dockerfile_gen import BUILD_OUTPUT_DIR, create_build_context
from app.builder.docker_build import build_image, remove_build_image, remove_image, tag_image
from app.builder.smoke_test import run_smoke_test
from app.core import (
    auth, credentials, extensions, hardware, image_tags, monitoring, parameters, pg_versions,
    ports, services, validation, workloads,
)
from app.core.conf_generator import render_conf

BASE_DIR = Path(__file__).resolve().parent

# Reachable without a session. Everything else is protected by default —
# see require_login. /static is here because the login page needs its
# stylesheet, and it serves no build data.
PUBLIC_PATHS = frozenset({"/login"})
PUBLIC_PREFIXES = ("/static/",)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    interrupted = build_store.mark_interrupted_builds(BUILD_OUTPUT_DIR)
    if interrupted:
        print(
            f"pg4all: marked {interrupted} build(s) as interrupted — the console "
            "restarted while they were running.",
            flush=True,
        )

    password = auth_store.ensure_password()
    if password:
        print(
            "\n"
            "  ┌─ pg4all console password ─────────────────────────────────\n"
            "  │\n"
            f"  │   {password}\n"
            "  │\n"
            "  │  Generated on first run and shown once — only its hash is\n"
            "  │  stored. Save it now.\n"
            "  │  To rotate: delete secrets/console-password.json, restart.\n"
            "  └───────────────────────────────────────────────────────────\n",
            flush=True,
        )
    yield


app = FastAPI(title="pg4all console", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Gate every route that isn't explicitly public.

    Deliberately middleware rather than a per-route dependency: a route
    added later is protected because it exists, not because someone
    remembered to decorate it. The failure mode of forgetting here is
    handing out Postgres superuser passwords to anyone who can reach the
    port, so this fails closed.
    """
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)

    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if not auth.verify_session(token, auth_store.load_session_key()):
        # 303 so a rejected POST becomes a GET of the login page rather
        # than the browser trying to re-POST the form to it.
        return RedirectResponse("/login", status_code=303)

    return await call_next(request)


def _set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE_NAME,
        token,
        max_age=auth.SESSION_MAX_AGE_SECONDS,
        httponly=True,       # not readable from JavaScript
        samesite="lax",      # blocks cross-site POSTs to /build
        # Not Secure: the console is served over plain HTTP on loopback and
        # reached through an SSH tunnel, which is what provides transport
        # confidentiality. Setting Secure here would stop the cookie being
        # sent at all and lock the operator out.
        secure=False,
        path="/",
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    form = await request.form()
    record = auth_store.load_password_record()

    if record is None or not auth.verify_password(form.get("password") or "", record):
        # One message for both "no password configured" and "wrong
        # password" — which of the two it is isn't the guesser's business.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Incorrect password."},
            status_code=401,
        )

    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, auth.issue_session(auth_store.load_session_key()))
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
    return response


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
        "has_grafana": "grafana:" in compose_text,
        "pgadmin_email": services.PGADMIN_EMAIL,
        "grafana_user": services.GRAFANA_ADMIN_USER,
        "host_ports": port_store.read_ports(build_dir),
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


def _submitted_ports(form) -> dict[str, int]:
    return ports.resolve(
        {spec.key: form.get(f"port_{spec.key}") for spec in ports.PORT_SPECS}
    )


def _all_findings(values, hw_tier, service_keys, host_ports) -> list:
    """Everything wrong with a submission, tuning and ports together, so
    the tuner panel and the /build gate always see the same list."""
    return validation.validate(values, hw_tier, service_keys) + ports.validate(
        host_ports, service_keys, published_host_ports()
    )


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
            "port_specs": ports.PORT_SPECS,
            "host_ports": ports.defaults(),
            "findings": _all_findings(
                {
                    row.spec.key: row.recommended_value
                    for group in groups
                    for row in group.rows
                },
                hw_tier,
                frozenset(default_service_keys),
                ports.defaults(),
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
    findings = _all_findings(
        _submitted_values(form, wl, hw_tier),
        hw_tier,
        frozenset(form.getlist("services")),
        _submitted_ports(form),
    )
    return templates.TemplateResponse(
        request, "_findings.html", {"findings": findings}
    )


@app.post("/build", response_class=HTMLResponse)
async def build(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()

    version, wl, hw_tier = _resolve_selection(
        form.get("pg_version"), form.get("workload"), form.get("tier")
    )
    values = _submitted_values(form, wl, hw_tier)
    host_ports = _submitted_ports(form)
    findings = _all_findings(
        values, hw_tier, frozenset(form.getlist("services")), host_ports
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

    # What the conf actually asks for, which is what the smoke test has to
    # hold the server to. format_conf_value rounds on the way out — a
    # work_mem slider at 10.24 MB is written as "10MB" — so comparing the
    # running server against the unrounded slider value reports a mismatch
    # for a value the conf never requested.
    conf_values = {
        spec.key: parameters.parse_value(spec, settings[spec.key])
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
    # The build owns this tag permanently; the short series tag is moved
    # onto it after a successful build. Everything generated for this build
    # — compose file, run instructions, smoke test — names the build tag, so
    # a later build of the same combination cannot change what this one runs.
    tag = image_tags.build_tag(version.major, wl.key, hw_tier.key, build_id)
    series = image_tags.series_tag(version.major, wl.key, hw_tier.key)
    port_store.write_ports(context_dir, host_ports)

    # The credential is generated and stored before the build starts, not
    # after it finishes, so the build page can hand it over immediately
    # rather than making the operator wait out a multi-minute build to see
    # it. build_ok is corrected once the build actually lands.
    password = credentials.generate_password()
    record = credentials.CredentialRecord(
        build_id=build_id,
        username=credentials.ADMIN_USERNAME,
        password=password,
        image_tag=tag,
        pg_major=version.major,
        created_at=datetime.now(UTC).isoformat(),
        build_ok=False,
    )
    credential_store.save_credential(record)

    build_store.start(
        context_dir,
        build_id=build_id,
        image_tag=tag,
        pg_major=version.major,
        pg_full_version=version.full_version,
        findings=[_finding_to_dict(f) for f in findings],
    )

    # Runs after this response is sent. The function is sync, so FastAPI
    # puts it on a worker thread and the event loop stays free.
    background_tasks.add_task(
        _run_build,
        context_dir=context_dir,
        tag=tag,
        pg_major=version.major,
        series_tag=series,
        conf_values=conf_values,
        selected_services=selected_services,
        username=record.username,
        password=password,
        host_ports=host_ports,
        record=record,
    )

    return RedirectResponse(f"/builds/{build_id}", status_code=303)


def _run_build(
    context_dir: Path,
    tag: str,
    pg_major: str,
    series_tag: str,
    conf_values: dict,
    selected_services: list,
    username: str,
    password: str,
    host_ports: dict[str, int],
    record: credentials.CredentialRecord,
) -> None:
    """The whole build, off the request. Progress goes to build_store as it
    happens; nothing here talks back to the browser."""
    try:
        result = build_image(
            context_dir,
            tag,
            on_log=lambda line: build_store.append_log(context_dir, line),
        )

        # CredentialRecord is frozen, so this is a new record written over
        # the placeholder saved before the build started.
        credential_store.save_credential(replace(record, build_ok=result.ok))

        if not result.ok:
            build_store.finish(context_dir, build_store.FAILED, build_ok=False)
            return

        # Only a build that actually succeeded gets to own the short name.
        moved, message = tag_image(tag, series_tag)
        build_store.append_log(context_dir, f"[pg4all] {message}")
        if not moved:
            build_store.append_log(
                context_dir,
                f"[pg4all] {series_tag} still points wherever it did before; "
                f"{tag} is this build's image either way.",
            )

        compose_gen.write_compose(
            context_dir, tag, username, password, selected_services, host_ports
        )

        # Written here rather than into the build context: everything in the
        # build directory at build time is uploaded to the daemon, and none
        # of this belongs in the image.
        if monitoring.is_selected(s.key for s in selected_services):
            monitoring_gen.write_monitoring_files(context_dir, conf_values, pg_major)

        build_store.set_stage(context_dir, build_store.STAGE_SMOKE_TESTING)
        build_store.append_log(context_dir, ["", "--- smoke test ---"])
        smoke = run_smoke_test(tag, conf_values)
        build_store.append_log(context_dir, smoke.log)

        # The image built, so the build succeeded. A failed smoke test is
        # reported beside it, not folded into it — the image is on the
        # daemon either way.
        build_store.finish(
            context_dir,
            build_store.SUCCEEDED,
            build_ok=True,
            smoke=_smoke_to_dict(smoke),
        )
    except Exception as exc:  # noqa: BLE001 - nothing above us to catch this
        # Without this a crash leaves the record stuck on "running" and the
        # page polling forever, with the reason only in the server log.
        build_store.append_log(context_dir, f"[pg4all] Build aborted: {exc!r}")
        build_store.finish(context_dir, build_store.FAILED, build_ok=False)


def _finding_to_dict(finding) -> dict:
    return {
        "level": finding.level,
        "summary": finding.summary,
        "detail": finding.detail,
        "parameter_keys": list(finding.parameter_keys),
    }


def _smoke_to_dict(smoke) -> dict:
    return {
        "status": smoke.status,
        "summary": smoke.summary,
        "comparisons": [
            {
                "key": c.key,
                "requested_display": c.requested_display,
                "applied_display": c.applied_display,
                "matches": c.matches,
            }
            for c in smoke.comparisons
        ],
    }


def _build_page_context(build_id: str) -> dict | None:
    build_dir = BUILD_OUTPUT_DIR / build_id
    build = build_store.load(build_dir)
    if build is None:
        return None
    return {
        "build": build,
        "log": build_store.read_log(build_dir),
        "credential": credential_store.load_credential(build_id),
        "findings": build.findings,
        "smoke": build.smoke,
        **_build_dir_flags(build_dir),
    }


@app.get("/builds/{build_id}", response_class=HTMLResponse)
def show_build(request: Request, build_id: str):
    context = _build_page_context(build_id)
    if context is None:
        return templates.TemplateResponse(
            request, "credential_not_found.html", {"build_id": build_id}, status_code=404
        )
    return templates.TemplateResponse(request, "build.html", context)


@app.get("/builds/{build_id}/progress", response_class=HTMLResponse)
def build_progress(request: Request, build_id: str):
    """The live part of the build page, re-rendered for polling.

    Same template fragment the full page uses, so the running view and the
    finished view can't drift apart."""
    context = _build_page_context(build_id)
    if context is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "_build_progress.html", context)


def _builds_sharing_tag(build_id: str, image_tag: str) -> list[str]:
    """Other builds whose image is this same tag.

    Tags are `pg4all/postgres:<major>-<workload>-<tier>`, so two builds of
    the same combination are the same tag — the later one overwrote the
    earlier. Removing "this build's image" would therefore take an image
    another record still points at, so the option isn't offered when that
    is true.
    """
    return [
        other.build_id
        for other in credential_store.list_credentials()
        if other.build_id != build_id and other.image_tag == image_tag
    ]


def _series_tag_for(image_tag: str | None) -> str | None:
    """The short tag a build tag belongs under, or None if there isn't one."""
    if not image_tag or not image_tags.is_build_tag(image_tag):
        return None
    return image_tag.rsplit("-", 1)[0]


def _delete_context(build_id: str) -> dict | None:
    """What a deletion would cover, for the confirmation page and the act."""
    if not build_store.is_valid_build_id(build_id):
        return None

    build_dir = BUILD_OUTPUT_DIR / build_id
    credential = credential_store.load_credential(build_id)
    build = build_store.load(build_dir)
    if credential is None and build is None and not build_dir.is_dir():
        return None

    image_tag = (
        credential.image_tag if credential else (build.image_tag if build else None)
    )
    return {
        "build_id": build_id,
        "credential": credential,
        "build": build,
        "image_tag": image_tag,
        "series_tag": _series_tag_for(image_tag),
        "is_build_tag": image_tags.is_build_tag(image_tag or ""),
        "has_directory": build_dir.is_dir(),
        "shared_with": _builds_sharing_tag(build_id, image_tag) if image_tag else [],
        "is_running": bool(build and build.is_running),
    }


@app.post("/builds/{build_id}/delete", response_class=HTMLResponse)
async def delete_build(request: Request, build_id: str):
    context = _delete_context(build_id)
    if context is None:
        return templates.TemplateResponse(
            request, "credential_not_found.html", {"build_id": build_id},
            status_code=404,
        )

    form = await request.form()
    if form.get("confirm") != "1":
        return templates.TemplateResponse(request, "confirm_delete.html", context)

    # A running build would carry on writing into the directory after it
    # was removed, leaving a half-rebuilt one behind.
    if context["is_running"]:
        return templates.TemplateResponse(
            request, "confirm_delete.html",
            {**context, "error": "This build is still running. Wait for it to finish."},
            status_code=409,
        )

    outcomes: list[tuple[bool, str]] = []

    removed_dir = build_store.delete_build(BUILD_OUTPUT_DIR, build_id)
    outcomes.append((
        removed_dir,
        f"Removed build_output/{build_id}/ and everything in it."
        if removed_dir else "No build directory to remove.",
    ))

    removed_credential = credential_store.delete_credential(build_id)
    outcomes.append((
        removed_credential,
        "Deleted the stored superuser credential."
        if removed_credential else "No stored credential to delete.",
    ))

    if form.get("remove_image") == "1" and context["image_tag"]:
        if context["shared_with"]:
            outcomes.append((
                False,
                f"Kept image {context['image_tag']}: "
                f"{len(context['shared_with'])} other build(s) still use that tag.",
            ))
        elif image_tags.is_build_tag(context["image_tag"]):
            outcomes.extend(
                await run_in_threadpool(
                    remove_build_image, context["image_tag"], context["series_tag"]
                )
            )
        else:
            # A record from before builds had their own tag. Its tag is the
            # shared series name, so removing it would take whatever image
            # happens to hold it now — which is not this build's.
            outcomes.append((
                False,
                f"Kept image {context['image_tag']}: this build predates "
                "per-build image tags, so that tag no longer identifies its image.",
            ))

    return templates.TemplateResponse(
        request, "build_deleted.html", {**context, "outcomes": outcomes}
    )


@app.get("/builds", response_class=HTMLResponse)
def list_builds(request: Request):
    records = credential_store.list_credentials()
    states = {}
    for credential in records:
        build = build_store.load(BUILD_OUTPUT_DIR / credential.build_id)
        states[credential.build_id] = build.state if build else None
    return templates.TemplateResponse(
        request, "builds.html", {"records": records, "states": states}
    )


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
