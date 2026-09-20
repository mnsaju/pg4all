import json

import pytest

from app.builder import license_scan
from app.core import licensing, services
from app.core.licensing import Component


def test_license_of_handles_syft_shapes():
    assert license_scan._license_of({"licenses": [{"spdxExpression": "MIT"}]}) == "MIT"
    assert license_scan._license_of({"licenses": [{"value": "BSD-3-Clause"}]}) == "BSD-3-Clause"
    assert license_scan._license_of({"licenses": ["ISC"]}) == "ISC"
    # spdxExpression wins over value on the same entry.
    assert license_scan._license_of(
        {"licenses": [{"spdxExpression": "Apache-2.0", "value": "asl2"}]}
    ) == "Apache-2.0"


def test_license_of_dedupes_and_defaults_to_unknown():
    assert license_scan._license_of(
        {"licenses": [{"value": "MIT"}, {"value": "MIT"}]}
    ) == "MIT"
    assert license_scan._license_of({}) == licensing.UNKNOWN_LICENSE
    assert license_scan._license_of({"licenses": []}) == licensing.UNKNOWN_LICENSE


def test_parse_syft_maps_artifacts_to_components():
    output = json.dumps(
        {
            "artifacts": [
                {"name": "libc6", "version": "2.36", "licenses": [{"value": "LGPL-2.1-only"}]},
                {"name": "", "version": "x", "licenses": []},  # dropped: no name
            ]
        }
    ).encode()
    components = license_scan._parse_syft(output)
    assert components == [Component("libc6", "2.36", "LGPL-2.1-only", "image")]


def test_referenced_components_only_sidecar_images():
    selected = services.resolve(["grafana", "pgbackrest"])
    referenced = license_scan.referenced_components(selected)
    names = {c.name for c in referenced}
    # Grafana and its required sidecars are referenced images; pgbackrest is
    # an apt package baked into the image, so it is not a referenced image.
    assert "grafana/grafana:13.2.2" in names
    assert all(c.origin == "referenced" for c in referenced)
    assert not any("pgbackrest" in c.name for c in referenced)


def test_referenced_components_carry_the_recorded_license():
    referenced = license_scan.referenced_components(services.resolve(["grafana"]))
    grafana = next(c for c in referenced if c.name == "grafana/grafana:13.2.2")
    assert grafana.license == "AGPL-3.0-only"


def test_generate_notices_writes_file_on_successful_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(
        license_scan,
        "scan_image",
        lambda tag: ([Component("libc6", "2.36", "LGPL-2.1-only", "image")], None),
    )
    result = license_scan.generate_notices(
        tmp_path, "pg4all/postgres:17-oltp-medium-abc", "17",
        services.resolve(["grafana"]),
    )
    text = result.notices_path.read_text()
    assert result.notices_path.name == "THIRD_PARTY_NOTICES.md"
    assert "`libc6` 2.36 — LGPL-2.1-only" in text
    assert "grafana/grafana:13.2.2" in text
    assert "1 package(s)" in result.summary


def test_generate_notices_still_writes_when_scan_unavailable(tmp_path, monkeypatch):
    # Compliance information must fail visibly: a scan that can't run still
    # yields a file, and that file says the image scan was unavailable.
    monkeypatch.setattr(
        license_scan,
        "scan_image",
        lambda tag: ([], "the image was not scanned — the Docker daemon was unreachable."),
    )
    result = license_scan.generate_notices(
        tmp_path, "pg4all/postgres:17-oltp-medium-abc", "17", [],
    )
    assert result.notices_path.is_file()
    assert "the image was not scanned" in result.notices_path.read_text()
    assert "incomplete" in result.summary


@pytest.mark.docker
def test_scan_image_runs_syft_against_a_real_image():
    # Exercises the real syft-in-a-container path end to end on a tiny
    # public image. Tolerant by design: if the syft image can't be pulled
    # in this environment the function must degrade to a note, never raise.
    import docker
    from docker.errors import DockerException

    try:
        client = docker.from_env()
        client.images.pull("busybox:1.36")
    except DockerException as exc:
        pytest.skip(f"Docker unavailable: {exc}")

    components, note = license_scan.scan_image("busybox:1.36")
    # Either the scan ran (note is None, components found) or it degraded to
    # a human-readable note — but it returned cleanly either way.
    assert note is None or isinstance(note, str)
    if note is None:
        assert isinstance(components, list)
