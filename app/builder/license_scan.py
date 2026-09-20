"""Scan a built image for its components and write THIRD_PARTY_NOTICES.md.

The I/O and Docker half of the licensing feature; the classification and
document assembly is app/core/licensing.py. Same split as smoke_test.py
against applied_settings.py.

The built Postgres image is scanned in place with syft, run as a throwaway
container against the host daemon over the socket the console already
mounts — the same Docker-outside-of-Docker pattern the rest of the builder
uses, so no tool is added to the console image itself. syft is chosen over
hand-reading dpkg because it enumerates non-dpkg components too (PostgreSQL
itself, anything pip- or source-installed) and emits SPDX ids directly;
the cost is a real dependency on the syft image, pulled on first use.

The companion images the compose file references are *not* scanned: they
are whole third-party artifacts pg4all only pins, not assembles, and
scanning them would mean pulling every one over the network at build time.
They are identified by the primary project license recorded next to their
pin in app/core/services.py instead, and each ships its own notices inside
itself.

This never raises into the build: a scan that cannot run still produces a
notices file, one that says the image scan was unavailable rather than one
that silently omits the image's components. Compliance information failing
open — a manifest that looks complete but isn't — is the failure worth
avoiding here.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import DockerException

from app.core import licensing
from app.core.licensing import Component
from app.core.services import ServiceSpec

NOTICES_FILENAME = "THIRD_PARTY_NOTICES.md"

# Pinned like every other image pg4all runs (see services.py). Bump it to
# move syft versions; a tag that can't be pulled degrades to a written
# notices file that says the scan was unavailable, not a failed build.
SYFT_IMAGE = "anchore/syft:v1.18.1"

DOCKER_SOCKET = "/var/run/docker.sock"


@dataclass
class LicenseResult:
    notices_path: Path
    summary: str


def _license_of(artifact: dict) -> str:
    """The SPDX id(s) syft reports for one artifact, as a display string.

    syft's license shape has drifted across versions — an entry may be a
    bare string, or an object with `spdxExpression` or `value`. All three
    are handled, unique values joined, and an artifact with none reported
    becomes UNKNOWN rather than being dropped, so "we don't know" is
    visible in the manifest instead of absent from it.
    """
    found: list[str] = []
    for entry in artifact.get("licenses", []) or []:
        if isinstance(entry, str):
            value = entry
        else:
            value = entry.get("spdxExpression") or entry.get("value") or ""
        value = value.strip()
        if value and value not in found:
            found.append(value)
    return ", ".join(found) if found else licensing.UNKNOWN_LICENSE


def _parse_syft(output: bytes) -> list[Component]:
    data = json.loads(output.decode("utf-8", errors="replace"))
    components = []
    for artifact in data.get("artifacts", []):
        name = artifact.get("name")
        if not name:
            continue
        components.append(
            Component(
                name=name,
                version=artifact.get("version", "") or "",
                license=_license_of(artifact),
                origin="image",
            )
        )
    return components


def scan_image(image_tag: str) -> tuple[list[Component], str | None]:
    """Run syft over `image_tag`, returning (components, note).

    `note` is None on a clean scan and a human-readable explanation when
    the scan could not run or returned nothing usable — never an exception.
    """
    try:
        client = docker.from_env()
    except DockerException as exc:
        return [], f"the image was not scanned — the Docker daemon was unreachable ({exc})."

    try:
        # `docker:` forces the daemon source, so syft reads the image the
        # build just produced from the local store rather than trying to
        # pull it from a registry. -q keeps syft's own progress off stdout.
        output = client.containers.run(
            SYFT_IMAGE,
            command=[f"docker:{image_tag}", "-o", "syft-json", "-q"],
            volumes={DOCKER_SOCKET: {"bind": DOCKER_SOCKET, "mode": "ro"}},
            remove=True,
            stdout=True,
            stderr=False,
        )
    except DockerException as exc:
        return [], (
            f"the image was not scanned — could not run the license scanner "
            f"({SYFT_IMAGE}): {exc}."
        )

    try:
        components = _parse_syft(output)
    except (json.JSONDecodeError, ValueError) as exc:
        return [], f"the image scan produced output that could not be read ({exc})."

    if not components:
        return [], "the image scan reported no components."
    return components, None


def referenced_components(selected_services: list[ServiceSpec]) -> list[Component]:
    """The companion images the compose file pulls, as Components.

    Only sidecar images are whole referenced artifacts; an apt service is
    inside the Postgres image and is covered by scanning it.
    """
    return [
        Component(
            name=spec.image,
            version="",
            license=spec.license or licensing.UNKNOWN_LICENSE,
            origin="referenced",
        )
        for spec in selected_services
        if spec.mode == "sidecar" and spec.image
    ]


def generate_notices(
    build_dir: Path,
    image_tag: str,
    pg_major: str,
    selected_services: list[ServiceSpec],
) -> LicenseResult:
    """Scan the built image, then write THIRD_PARTY_NOTICES.md into the
    build directory. Always writes a file; the summary says what it holds."""
    scanned, note = scan_image(image_tag)
    referenced = referenced_components(selected_services)

    text = licensing.assemble_notices(
        image_tag=image_tag,
        pg_major=pg_major,
        scanned=scanned,
        scan_note=note,
        referenced=referenced,
    )

    path = build_dir / NOTICES_FILENAME
    path.write_text(text)

    if note:
        summary = (
            f"Wrote {NOTICES_FILENAME} — image scan incomplete "
            f"({len(referenced)} referenced image(s) listed): {note}"
        )
    else:
        summary = (
            f"Wrote {NOTICES_FILENAME} — {len(scanned)} package(s) in the image, "
            f"{len(referenced)} referenced image(s)."
        )
    return LicenseResult(notices_path=path, summary=summary)
