"""Live state for a build that's still running, and the record of one
that finished.

Builds used to happen inside the POST that requested them: the browser
held an open request through `docker build` and the smoke test and showed
nothing until both finished. Warm layers make that a few seconds; a cold
build, or one with apt-sourced extensions and pgBackRest, is minutes of
blank page.

So a build now runs in the background and its progress lives here. State
goes on disk, in the same `build_output/<build_id>/` directory the
Dockerfile and conf already go into — which is bind-mounted to the host,
so a build is inspectable with `cat` while it runs, and survives the
console restarting. Two files:

- `status.json` — everything the build page renders except the log.
- `build.log`   — append-only, written as the daemon emits it.

The alternative was keeping jobs in memory. That's less code right up
until the console restarts mid-build, at which point the job vanishes
while the daemon carries on building, and nothing can tell you what
happened. On disk, an interrupted build is still visible — and
`mark_interrupted_builds` turns those stale "running" records into
something honest at startup.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
INTERRUPTED = "interrupted"

TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, INTERRUPTED})

STAGE_BUILDING = "Building image"
STAGE_SMOKE_TESTING = "Smoke-testing image"
STAGE_DONE = "Done"

STATUS_FILENAME = "status.json"
LOG_FILENAME = "build.log"


@dataclass
class BuildRecord:
    build_id: str
    state: str
    stage: str
    image_tag: str
    pg_major: str
    pg_full_version: str
    started_at: str
    finished_at: str | None = None
    build_ok: bool | None = None
    # Rendered forms only: the build page needs to show these long after
    # the objects that produced them are gone.
    smoke: dict | None = None
    findings: list[dict] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.state == RUNNING

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def _now() -> str:
    return datetime.now(UTC).isoformat()


def status_path(build_dir: Path) -> Path:
    return build_dir / STATUS_FILENAME


def log_path(build_dir: Path) -> Path:
    return build_dir / LOG_FILENAME


def save(build_dir: Path, record: BuildRecord) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    # Written whole, then moved into place, so a page polling mid-write
    # never reads half a document.
    temporary = status_path(build_dir).with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(record), indent=2) + "\n")
    temporary.replace(status_path(build_dir))


def load(build_dir: Path) -> BuildRecord | None:
    path = status_path(build_dir)
    if not path.exists():
        return None
    try:
        return BuildRecord(**json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def start(
    build_dir: Path,
    build_id: str,
    image_tag: str,
    pg_major: str,
    pg_full_version: str,
    findings: list[dict],
) -> BuildRecord:
    record = BuildRecord(
        build_id=build_id,
        state=RUNNING,
        stage=STAGE_BUILDING,
        image_tag=image_tag,
        pg_major=pg_major,
        pg_full_version=pg_full_version,
        started_at=_now(),
        findings=findings,
    )
    save(build_dir, record)
    log_path(build_dir).write_text("")
    return record


def set_stage(build_dir: Path, stage: str) -> None:
    record = load(build_dir)
    if record is None:
        return
    record.stage = stage
    save(build_dir, record)


def finish(
    build_dir: Path,
    state: str,
    build_ok: bool,
    smoke: dict | None = None,
) -> None:
    record = load(build_dir)
    if record is None:
        return
    record.state = state
    record.stage = STAGE_DONE
    record.build_ok = build_ok
    record.smoke = smoke
    record.finished_at = _now()
    save(build_dir, record)


def append_log(build_dir: Path, lines: list[str] | str) -> None:
    if isinstance(lines, str):
        lines = [lines]
    text = "".join(line.rstrip("\n") + "\n" for line in lines if line is not None)
    if not text:
        return
    with log_path(build_dir).open("a") as handle:
        handle.write(text)


def read_log(build_dir: Path) -> str:
    path = log_path(build_dir)
    if not path.exists():
        return ""
    try:
        return path.read_text()
    except OSError:  # pragma: no cover - transient read race
        return ""


def mark_interrupted_builds(build_output_dir: Path) -> int:
    """Turn stale "running" records into "interrupted" at startup.

    Any build still marked running when the console boots was owned by a
    process that no longer exists, so it will never finish or update
    itself. Leaving it as running means a page that spins forever; saying
    interrupted is at least true. Note the image may well have finished
    building on the daemon regardless — the console just stopped watching.
    """
    if not build_output_dir.exists():
        return 0

    marked = 0
    for build_dir in build_output_dir.iterdir():
        if not build_dir.is_dir():
            continue
        record = load(build_dir)
        if record is None or not record.is_running:
            continue
        record.state = INTERRUPTED
        record.stage = STAGE_DONE
        record.finished_at = _now()
        save(build_dir, record)
        append_log(
            build_dir,
            "[pg4all] The console restarted while this build was running, so it "
            "stopped following it. The image may still have finished building on "
            "the Docker daemon — check `docker images`.",
        )
        marked += 1
    return marked
