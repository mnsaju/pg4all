"""Tests for app/builder/build_store.py — the on-disk record of a build."""

from pathlib import Path

from app.builder import build_store


def start(tmp_path: Path, build_id: str = "abc") -> Path:
    build_dir = tmp_path / build_id
    build_store.start(
        build_dir,
        build_id=build_id,
        image_tag="pg4all/postgres:17-oltp-medium",
        pg_major="17",
        pg_full_version="17.11",
        findings=[],
    )
    return build_dir


def test_a_started_build_is_running(tmp_path):
    build_dir = start(tmp_path)
    record = build_store.load(build_dir)
    assert record.state == build_store.RUNNING
    assert record.is_running and not record.is_terminal
    assert record.stage == build_store.STAGE_BUILDING


def test_log_lines_accumulate_in_order(tmp_path):
    build_dir = start(tmp_path)
    build_store.append_log(build_dir, "Step 1/3")
    build_store.append_log(build_dir, ["Step 2/3", "Step 3/3"])
    assert build_store.read_log(build_dir).splitlines() == [
        "Step 1/3", "Step 2/3", "Step 3/3"
    ]


def test_finishing_records_the_outcome(tmp_path):
    build_dir = start(tmp_path)
    build_store.finish(
        build_dir, build_store.SUCCEEDED, build_ok=True, smoke={"status": "passed"}
    )

    record = build_store.load(build_dir)
    assert record.state == build_store.SUCCEEDED
    assert record.is_terminal
    assert record.build_ok is True
    assert record.smoke == {"status": "passed"}
    assert record.finished_at is not None


def test_status_is_readable_at_every_point_during_a_write(tmp_path):
    """Written whole then moved into place, so a page polling mid-write
    never parses half a document."""
    build_dir = start(tmp_path)
    for i in range(30):
        build_store.set_stage(build_dir, f"stage {i}")
        assert build_store.load(build_dir) is not None
    assert not list(build_dir.glob("*.tmp"))


def test_loading_a_directory_with_no_build_returns_none(tmp_path):
    assert build_store.load(tmp_path / "nothing-here") is None


def test_a_corrupt_status_file_reads_as_absent(tmp_path):
    build_dir = start(tmp_path)
    build_store.status_path(build_dir).write_text("{ not json")
    assert build_store.load(build_dir) is None


def test_a_build_left_running_by_a_restart_is_marked_interrupted(tmp_path):
    """Nothing will ever finish that record, so the page would poll
    forever. Saying interrupted is at least true."""
    running = start(tmp_path, "still-running")
    done = start(tmp_path, "already-done")
    build_store.finish(done, build_store.SUCCEEDED, build_ok=True)

    assert build_store.mark_interrupted_builds(tmp_path) == 1

    assert build_store.load(running).state == build_store.INTERRUPTED
    assert build_store.load(running).is_terminal
    assert "console restarted" in build_store.read_log(running)
    # A finished build is left exactly as it was.
    assert build_store.load(done).state == build_store.SUCCEEDED


def test_marking_interrupted_builds_is_safe_when_nothing_has_been_built(tmp_path):
    assert build_store.mark_interrupted_builds(tmp_path / "missing") == 0
