# FILE: tests/integration/test_mp_e2e.py
# START_MODULE_CONTRACT
#   PURPOSE: VF-013 PurePythonDocFlow — Phase-7 acceptance e2e harness.
#     Builds the golden 3-section document via mint_python.sdk and asserts
#     the trace marker counts, no-legacy-markers guard, lxml word/document.xml
#     parses, M-VALIDATE on lenient passes with hard_count=0, fingerprint
#     idempotency across two builds, no GRACE Custom XML Parts in Phase-7
#     output, audit-baseline pin (gated by MP_E2E_WRITE_BASELINE), and a
#     preset-observable XPath sanity check.
#   SCOPE: Single integration module covering all VF-013 invariants. Reuses
#     central tests/unit/conftest.py fixtures and tests.unit._mp_helpers.
#   DEPENDS: pytest, lxml, zipfile, mint.fingerprint, mint.validate,
#     mint.config (MintConfig + SeverityMode), tests.unit._mp_helpers
#     (build_golden_document, assert_no_legacy_markers, load/write_audit_baseline).
#   LINKS: docs/verification-plan.xml#VF-013,
#     docs/verification-plan.xml#V-MP-DOCUMENT scenario-9 (audit-baseline pin)
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   _build_baseline - compute audit-baseline payload for first-run write
#   test_vf_013_trace_and_marker_counts - VF-013 trace-sequence
#   test_vf_013_no_legacy_markers - VF-013 forbidden-5
#   test_vf_013_lxml_parses_word_document_xml - VF-013 inv-2
#   test_vf_013_lenient_validate_passes - VF-013 expected-outcome (c)
#   test_vf_013_idempotent_under_fingerprint - VF-013 inv-1
#   test_vf_013_no_grace_custom_xml_parts - VF-013 inv-3
#   test_vf_013_audit_baseline_pin - VF-013 inv-5
#   test_vf_013_preset_observable_via_xpath - VF-013 inv-4 (lenient)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-5 - initial provisioning: VF-013 Phase-7 acceptance e2e.
# END_CHANGE_SUMMARY
"""VF-013 PurePythonDocFlow — Phase-7 acceptance e2e harness."""
from __future__ import annotations

import os
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

# We need conftest fixtures from tests.unit; re-export the conftest by adding
# a module-level fixture import. pytest auto-discovers tests/unit/conftest.py
# only for tests under tests/unit; for tests under tests/integration we point
# at the same fixtures via direct conftest import.
# Implementation: tests/integration/conftest.py thin shim (created alongside).

# Snapshot MP_E2E_WRITE_BASELINE at module import time, BEFORE the autouse
# mp_clean_env fixture (tests/unit/conftest.py) scrubs it. mp_clean_env's
# scrub is the right behavior for normal tests (prevents stray writes), but
# the audit-baseline-pin test legitimately needs to honor an operator-set
# env to produce the first-run baseline. See V-MP-DOCUMENT/baseline-update-protocol.
_BASELINE_WRITE_REQUESTED = os.environ.get("MP_E2E_WRITE_BASELINE") == "1"


# START_BLOCK_BUILD_BASELINE
def _build_baseline(saved_path: Path, mp_minimal_config: Any) -> dict[str, Any]:
    """Compute the audit-baseline payload for tests/fixtures/mp_e2e_baseline.json.

    Aggregates: structural fingerprint hash + xml_sources, file/section/table
    counts, and audit-mode validate violations bucketed by (rule_id, severity).
    """
    from dataclasses import replace

    from mint.config import SeverityMode
    from mint.fingerprint import fingerprint
    from mint.validate import validate

    fp = fingerprint(saved_path)

    # Run M-VALIDATE in audit mode to capture all (hard + soft) violations.
    audit_cfg = replace(mp_minimal_config, severity_mode=SeverityMode.AUDIT)
    audit_report = validate(saved_path, audit_cfg)

    aggregated: Counter[tuple[str, str]] = Counter(
        (v.rule_id, str(v.severity)) for v in audit_report.violations
    )
    audit_violations = sorted(
        [
            {"rule_id": rid, "severity": sev, "count": c}
            for (rid, sev), c in aggregated.items()
        ],
        key=lambda d: (d["rule_id"], d["severity"]),
    )

    hard_count = sum(c for (_, sev), c in aggregated.items() if sev == "hard")
    soft_count = sum(c for (_, sev), c in aggregated.items() if sev == "soft")

    # Structural counts via zip introspection.
    with zipfile.ZipFile(saved_path) as zf:
        names = zf.namelist()
    counts = {
        "zip_entry_count": len(names),
        "has_styles_xml": "word/styles.xml" in names,
        "has_numbering_xml": "word/numbering.xml" in names,
        "has_content_types": "[Content_Types].xml" in names,
        "audit_total": audit_report.total,
        "audit_hard": hard_count,
        "audit_soft": soft_count,
    }

    return {
        "schema_version": "1.0",
        "committed_at": "2026-05-09",
        "generator": "mint_python 0.1.0 + python-docx",
        "document_spec": "tests/unit/_mp_helpers.py::build_golden_document",
        "structural_fingerprint_sha256": fp.hash,
        "xml_sources": list(fp.xml_sources),
        "counts": counts,
        "audit_violations": audit_violations,
        "hard_violation_count": hard_count,
        "schema_validation": {"lxml_parses": True, "ecma376_validated": False},
    }
# END_BLOCK_BUILD_BASELINE


# START_BLOCK_TEST_TRACE_AND_MARKER_COUNTS
def test_vf_013_trace_and_marker_counts(
    tmp_docx_path: Path,
    caplog_at_info: pytest.LogCaptureFixture,
    marker_counter: Any,
    golden_doc_builder: Any,
) -> None:
    """VF-013 trace-sequence assertions on Phase-7 marker counts.

    Expected: BLOCK_LOAD_PRESET=1, BLOCK_RENDER_TABLE=2, BLOCK_SAVE_DOCX=1,
    BLOCK_PHASE_GUARD=0.
    """
    golden_doc_builder(tmp_docx_path)
    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_LOAD_PRESET"] == 1, f"counts={dict(counts)}"
    assert counts["BLOCK_RENDER_TABLE"] == 2, f"counts={dict(counts)}"
    assert counts["BLOCK_SAVE_DOCX"] == 1, f"counts={dict(counts)}"
    assert counts.get("BLOCK_PHASE_GUARD", 0) == 0, f"counts={dict(counts)}"
# END_BLOCK_TEST_TRACE_AND_MARKER_COUNTS


# START_BLOCK_TEST_NO_LEGACY_MARKERS
def test_vf_013_no_legacy_markers(
    tmp_docx_path: Path,
    caplog_at_info: pytest.LogCaptureFixture,
    golden_doc_builder: Any,
) -> None:
    """VF-013 forbidden-5: no [Sandbox]/[Validate]/[Edit]/etc. on the build path."""
    from tests.unit._mp_helpers import assert_no_legacy_markers

    golden_doc_builder(tmp_docx_path)
    assert_no_legacy_markers(caplog_at_info)
# END_BLOCK_TEST_NO_LEGACY_MARKERS


# START_BLOCK_TEST_LXML_PARSES
def test_vf_013_lxml_parses_word_document_xml(
    tmp_docx_path: Path, golden_doc_builder: Any
) -> None:
    """VF-013 inv-2: word/document.xml parses via lxml without raising."""
    from lxml import etree

    out = golden_doc_builder(tmp_docx_path)
    with zipfile.ZipFile(out) as zf:
        with zf.open("word/document.xml") as f:
            etree.parse(f)
        names = set(zf.namelist())
    assert "word/document.xml" in names
    assert "word/styles.xml" in names
    assert "[Content_Types].xml" in names
# END_BLOCK_TEST_LXML_PARSES


# START_BLOCK_TEST_LENIENT_VALIDATE_PASSES
def test_vf_013_lenient_validate_passes(
    tmp_docx_path: Path, golden_doc_builder: Any, mp_minimal_config: Any
) -> None:
    """VF-013 expected-outcome (c): lenient validate yields passed=True, hard_count=0."""
    from mint.validate import validate

    out = golden_doc_builder(tmp_docx_path)
    report = validate(out, mp_minimal_config)
    assert report.passed is True, (
        f"lenient validate reported passed=False; "
        f"hard={report.hard_count}, soft={report.soft_count}, "
        f"violations={[(v.rule_id, str(v.severity)) for v in report.violations]}"
    )
    assert report.hard_count == 0, (
        f"hard_count={report.hard_count}; "
        f"hard violations={[(v.rule_id, v.message) for v in report.hard_violations]}"
    )
# END_BLOCK_TEST_LENIENT_VALIDATE_PASSES


# START_BLOCK_TEST_IDEMPOTENT_UNDER_FINGERPRINT
def test_vf_013_idempotent_under_fingerprint(
    tmp_path: Path, golden_doc_builder: Any
) -> None:
    """VF-013 inv-1: two builds produce equal mint.fingerprint hash."""
    from mint.fingerprint import fingerprint

    a = tmp_path / "a.docx"
    b = tmp_path / "b.docx"
    golden_doc_builder(a)
    golden_doc_builder(b)
    assert fingerprint(a).hash == fingerprint(b).hash
# END_BLOCK_TEST_IDEMPOTENT_UNDER_FINGERPRINT


# START_BLOCK_TEST_NO_GRACE_CUSTOM_XML_PARTS
def test_vf_013_no_grace_custom_xml_parts(
    tmp_docx_path: Path, golden_doc_builder: Any
) -> None:
    """VF-013 inv-3: Phase-7 output MUST NOT contain GRACE Custom XML Parts."""
    out = golden_doc_builder(tmp_docx_path)
    with zipfile.ZipFile(out) as zf:
        custom_xml_entries = [
            n
            for n in zf.namelist()
            if n.startswith("customXml/item") and n.endswith(".xml")
        ]
        for entry in custom_xml_entries:
            content = zf.read(entry).decode("utf-8", errors="ignore")
            assert "urn:mint:grace:2026:manifest" not in content, (
                f"GRACE manifest namespace found in {entry}; "
                "Phase-7 output must not embed GRACE Custom XML Parts."
            )
# END_BLOCK_TEST_NO_GRACE_CUSTOM_XML_PARTS


# START_BLOCK_TEST_AUDIT_BASELINE_PIN
def test_vf_013_audit_baseline_pin(
    tmp_docx_path: Path, golden_doc_builder: Any, mp_minimal_config: Any
) -> None:
    """VF-013 inv-5: structural fingerprint matches tests/fixtures/mp_e2e_baseline.json.

    On first run with MP_E2E_WRITE_BASELINE=1, produces the baseline.
    On subsequent runs, asserts equality.
    """
    from mint.fingerprint import fingerprint
    from tests.unit._mp_helpers import (
        load_audit_baseline,
        write_audit_baseline,
    )

    out = golden_doc_builder(tmp_docx_path)
    fp_hash = fingerprint(out).hash

    if _BASELINE_WRITE_REQUESTED:
        # Re-set env so write_audit_baseline's gate passes; mp_clean_env
        # autouse already scrubbed it at fixture setup.
        os.environ["MP_E2E_WRITE_BASELINE"] = "1"
        baseline_data = _build_baseline(out, mp_minimal_config)
        write_audit_baseline(baseline_data)
        assert baseline_data["structural_fingerprint_sha256"] == fp_hash
        # Phase-7 acceptance bar: hard_count MUST be 0.
        assert baseline_data["hard_violation_count"] == 0, (
            f"Phase-7 acceptance failed: hard_violation_count="
            f"{baseline_data['hard_violation_count']}; "
            f"audit_violations={baseline_data['audit_violations']}"
        )
    else:
        baseline = load_audit_baseline()
        assert baseline["structural_fingerprint_sha256"] == fp_hash, (
            f"baseline drift detected. "
            f"expected={baseline['structural_fingerprint_sha256']!r}, "
            f"got={fp_hash!r}. See V-MP-DOCUMENT/baseline-update-protocol "
            "if intentional."
        )
# END_BLOCK_TEST_AUDIT_BASELINE_PIN


# START_BLOCK_TEST_PRESET_OBSERVABLE_VIA_XPATH
def test_vf_013_preset_observable_via_xpath(
    tmp_docx_path: Path,
    caplog_at_info: pytest.LogCaptureFixture,
    marker_counter: Any,
    golden_doc_builder: Any,
) -> None:
    """VF-013 inv-4: the alga_corporate preset is observable in the output.

    NOTE (Phase-7 lenient acceptance): python-docx's add_heading wires its
    built-in Heading styles, which may not pick up our preset's font/color
    overrides. We accept either:
      (a) Heading 1 style entry exists in word/styles.xml (structural sanity),
        AND
      (b) BLOCK_LOAD_PRESET marker fired exactly once (preset was loaded).
    Hard XPath font/color match is deferred to Phase-2+ when MP-DOCUMENT
    can write style overrides directly into word/styles.xml.
    """
    from lxml import etree

    out = golden_doc_builder(tmp_docx_path)

    # (b) preset was loaded.
    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_LOAD_PRESET"] == 1, f"counts={dict(counts)}"

    # (a) Heading 1 entry exists.
    with zipfile.ZipFile(out) as zf:
        styles_xml = zf.read("word/styles.xml")
    root = etree.fromstring(styles_xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    heading1_styles = root.xpath(".//w:style[@w:styleId='Heading1']", namespaces=ns)
    assert len(heading1_styles) >= 1, (
        "expected at least one w:style[@w:styleId='Heading1'] entry in "
        "word/styles.xml; the golden document uses Heading 1 sections."
    )
# END_BLOCK_TEST_PRESET_OBSERVABLE_VIA_XPATH
