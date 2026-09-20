from app.core import licensing
from app.core.licensing import Component


def test_classify_families():
    assert licensing.classify("MIT") == licensing.PERMISSIVE
    assert licensing.classify("BSD-3-Clause") == licensing.PERMISSIVE
    assert licensing.classify("Apache-2.0") == licensing.PERMISSIVE
    assert licensing.classify("PostgreSQL") == licensing.PERMISSIVE
    assert licensing.classify("ISC") == licensing.PERMISSIVE
    assert licensing.classify("LGPL-2.1-only") == licensing.WEAK_COPYLEFT
    assert licensing.classify("MPL-2.0") == licensing.WEAK_COPYLEFT
    assert licensing.classify("GPL-2.0-only") == licensing.STRONG_COPYLEFT
    assert licensing.classify("AGPL-3.0-only") == licensing.NETWORK_COPYLEFT


def test_classify_agpl_not_mistaken_for_gpl():
    # "AGPL" contains "GPL"; the network-copyleft bucket must win so
    # Grafana's obligation is never understated as ordinary GPL.
    assert licensing.classify("AGPL-3.0-or-later") == licensing.NETWORK_COPYLEFT


def test_classify_unknown_is_never_permissive():
    assert licensing.classify("") == licensing.UNCLASSIFIED
    assert licensing.classify("Proprietary") == licensing.UNCLASSIFIED
    assert licensing.classify(licensing.UNKNOWN_LICENSE) == licensing.UNCLASSIFIED


def _notices(scanned=None, note=None, referenced=None):
    return licensing.assemble_notices(
        image_tag="pg4all/postgres:17-oltp-medium-abc",
        pg_major="17",
        scanned=scanned or [],
        scan_note=note,
        referenced=referenced or [],
    )


def test_notices_include_output_declaration_and_disclaimer():
    text = _notices()
    assert licensing.OUTPUT_LICENSE_DECLARATION in text
    assert "not legal advice" in text
    assert "pg4all/postgres:17-oltp-medium-abc" in text


def test_notices_list_scanned_and_referenced_components():
    scanned = [Component("libc6", "2.36", "LGPL-2.1-only", "image")]
    referenced = [Component("grafana/grafana:13.2.2", "", "AGPL-3.0-only", "referenced")]
    text = _notices(scanned=scanned, referenced=referenced)
    assert "`libc6` 2.36 — LGPL-2.1-only" in text
    assert "`grafana/grafana:13.2.2` — AGPL-3.0-only" in text
    # The in-image location of the verbatim texts is pointed at, not copied.
    assert "/usr/share/doc/<package>/copyright" in text


def test_notices_report_scan_failure_verbatim_not_silently():
    text = _notices(note="the image was not scanned — the Docker daemon was unreachable.")
    assert "the image was not scanned" in text
    # And no component list is fabricated in its place.
    assert "No additional packages" not in text


def test_notices_family_summary_orders_copyleft_first():
    text = _notices(
        scanned=[Component("libc6", "2.36", "LGPL-2.1-only", "image")],
        referenced=[Component("grafana/grafana:13.2.2", "", "AGPL-3.0-only", "referenced")],
    )
    network = text.index(licensing.FAMILY_LABELS[licensing.NETWORK_COPYLEFT])
    weak = text.index(licensing.FAMILY_LABELS[licensing.WEAK_COPYLEFT])
    assert network < weak


def test_notices_are_deterministic():
    scanned = [
        Component("zlib1g", "1.2", "Zlib", "image"),
        Component("libc6", "2.36", "LGPL-2.1-only", "image"),
    ]
    assert _notices(scanned=scanned) == _notices(scanned=list(reversed(scanned)))
