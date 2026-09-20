"""License classification and the THIRD_PARTY_NOTICES text.

Pure logic only — no file I/O, no Docker, no scanning. Turning the built
image into a component list is app/builder/license_scan.py; this module
only knows how to classify an SPDX id and assemble the notices document
from component lists it is handed. Same core/builder split as
conf_generator.py against dockerfile_gen.py.

Why this exists: pg4all's deliverable is not one program, it is an
*aggregation* — the PostgreSQL image it builds (a Debian base plus
PostgreSQL, extensions, and any apt-installed services) alongside the
companion images the generated compose file references (Grafana,
Prometheus, the exporter, …). Distributing that bundle carries every
component's own obligations: retain each license and its notices. There is
no single "superset" license to compute — copyleft components keep their
own terms and cannot be relicensed — so the artifact this produces is an
inventory (a NOTICES manifest), not a generated license.

Scope of v1 (notices only): the manifest identifies each component and its
license. The verbatim per-package license texts already travel *inside*
the image — Debian retains them at /usr/share/doc/<pkg>/copyright — so the
manifest points there rather than duplicating tens of thousands of lines
of license text into a file. Whether that is complete enough for a given
distribution is a legal call; this is an inventory, not legal advice.
"""

from dataclasses import dataclass

UNKNOWN_LICENSE = "UNKNOWN"

# License families, coarsest-obligation-first. The point of grouping is to
# make the strength of each obligation legible in the manifest — a reader
# scanning the list should see immediately that Grafana's AGPL is a
# different kind of obligation from a BSD notice. Classification is by SPDX
# id prefix because that is what scanners emit; it is deliberately
# conservative (an unrecognized id is UNKNOWN, never silently "permissive").
PERMISSIVE = "permissive"
WEAK_COPYLEFT = "weak-copyleft"
STRONG_COPYLEFT = "strong-copyleft"
NETWORK_COPYLEFT = "network-copyleft"
UNCLASSIFIED = "unclassified"

FAMILY_LABELS = {
    PERMISSIVE: "Permissive (notice retention)",
    WEAK_COPYLEFT: "Weak copyleft (LGPL/MPL-style)",
    STRONG_COPYLEFT: "Strong copyleft (GPL-style)",
    NETWORK_COPYLEFT: "Network copyleft (AGPL-style)",
    UNCLASSIFIED: "Unclassified — verify manually",
}

# Matched against the upper-cased SPDX id. AGPL is checked before GPL so
# "AGPL-3.0" never falls through to the GPL bucket.
_PERMISSIVE_PREFIXES = (
    "MIT", "BSD", "ISC", "APACHE", "POSTGRESQL", "ZLIB", "PYTHON",
    "PSF", "NCSA", "X11", "0BSD", "UNLICENSE", "WTFPL", "BOOST", "BSL-1.0",
)


def classify(spdx: str) -> str:
    """The obligation family of an SPDX id (or a raw license string).

    Only the first recognizable token is considered, so a compound
    expression like "GPL-2.0-or-later AND MIT" classifies by its strongest
    obligation would be ideal — but that needs an expression parser we
    don't have in v1, so a compound string is classified by whichever
    family its text mentions most strongly (AGPL > GPL > LGPL/MPL >
    permissive). This is a summary aid, not a compliance determination.
    """
    if not spdx:
        return UNCLASSIFIED
    text = spdx.upper()

    if "AGPL" in text:
        return NETWORK_COPYLEFT
    if "LGPL" in text or text.startswith("MPL") or "MPL-" in text:
        return WEAK_COPYLEFT
    if "GPL" in text:
        return STRONG_COPYLEFT
    if any(token in text for token in _PERMISSIVE_PREFIXES):
        return PERMISSIVE
    return UNCLASSIFIED


@dataclass(frozen=True)
class Component:
    """One distributed component and the license it travels under.

    `origin` distinguishes the two kinds of thing in the deliverable, which
    carry different manifest treatment: "image" components are packages
    pg4all's own image bakes in (enumerated by scanning it), while
    "referenced" components are whole companion images the compose file
    pulls, identified by their primary project license.
    """

    name: str
    version: str
    license: str
    origin: str  # "image" | "referenced"


# The declaration for pg4all's *own* generated output — the Dockerfile and
# postgresql.conf it authors, as distinct from the third-party software
# they install. These are trivial configuration scaffolding, so the default
# is a permissive, no-warranty grant. This is the one legal choice in the
# module a maintainer should confirm or replace for their distribution;
# it is kept here, in one place, rather than decided silently in the text.
OUTPUT_LICENSE_DECLARATION = (
    "The Dockerfile and postgresql.conf that pg4all generates are "
    "configuration scaffolding authored by pg4all. They are provided "
    "as-is, without warranty, and may be used, modified, and distributed "
    "without restriction. This grant covers only those generated files — "
    "the third-party software they install and run is licensed by its "
    "respective copyright holders, as inventoried below."
)


def _component_lines(components: list[Component]) -> list[str]:
    lines = []
    for c in sorted(components, key=lambda c: (c.name.lower(), c.version)):
        version = f" {c.version}" if c.version else ""
        lines.append(f"- `{c.name}`{version} — {c.license}")
    return lines


def _families_present(components: list[Component]) -> list[str]:
    seen = {classify(c.license) for c in components}
    # Report in fixed obligation order, so a copyleft family never hides
    # below the permissive one.
    order = [NETWORK_COPYLEFT, STRONG_COPYLEFT, WEAK_COPYLEFT, PERMISSIVE, UNCLASSIFIED]
    return [FAMILY_LABELS[f] for f in order if f in seen]


def assemble_notices(
    image_tag: str,
    pg_major: str,
    scanned: list[Component],
    scan_note: str | None,
    referenced: list[Component],
) -> str:
    """The full THIRD_PARTY_NOTICES.md text.

    `scanned` is what scanning the built image found (may be empty).
    `scan_note`, when set, explains why the image scan is incomplete or
    unavailable — it is written into the manifest verbatim rather than
    swallowed, so a build whose scan failed produces a document that says
    so instead of one that silently omits the image's components.
    `referenced` are the companion images the compose file pulls.
    """
    all_components = scanned + referenced
    out: list[str] = []

    out.append("# Third-party licenses")
    out.append("")
    out.append(
        "This is an inventory of the open-source components distributed in "
        "this pg4all deliverable and the licenses they travel under. The "
        "deliverable is an aggregation of independently licensed programs; "
        "each keeps its own license. This file is a manifest to help you "
        "meet those licenses' notice obligations — it is not legal advice "
        "and not a warranty of compliance."
    )
    out.append("")

    out.append("## pg4all's generated files")
    out.append("")
    out.append(OUTPUT_LICENSE_DECLARATION)
    out.append("")

    out.append(f"## PostgreSQL image (`{image_tag}`)")
    out.append("")
    out.append(
        f"A Debian-based image running PostgreSQL {pg_major} (PostgreSQL "
        "License), with the packages below installed on top."
    )
    out.append("")
    if scan_note:
        out.append(f"> **Note:** {scan_note}")
        out.append("")
    if scanned:
        out.extend(_component_lines(scanned))
        out.append("")
    elif not scan_note:
        out.append("_No additional packages were reported._")
        out.append("")
    out.append(
        "The full, verbatim copyright and license text for each package "
        "above is retained inside the image itself, at "
        "`/usr/share/doc/<package>/copyright` (the Debian convention). "
        "Those files travel with the image, so the obligation to distribute "
        "each license text is met by the image; this manifest identifies "
        "what is present."
    )
    out.append("")

    if referenced:
        out.append("## Referenced companion images")
        out.append("")
        out.append(
            "The generated compose file pulls these images as separate "
            "containers. Each is listed under its primary project license; "
            "each image also bundles its own base-layer components under "
            "their own licenses, retained inside that image."
        )
        out.append("")
        out.extend(_component_lines(referenced))
        out.append("")

    families = _families_present(all_components)
    if families:
        out.append("## License families present")
        out.append("")
        out.append(
            "Grouped by the kind of obligation each imposes, strongest "
            "first. This is a reading aid, not a compliance determination."
        )
        out.append("")
        for label in families:
            out.append(f"- {label}")
        out.append("")

    return "\n".join(out).rstrip("\n") + "\n"
