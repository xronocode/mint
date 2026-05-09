# FILE: tests/integration/test_mp_chart_e2e.py
# START_MODULE_CONTRACT
#   PURPOSE: VF-014 PurePythonChartFlow — Phase-8 acceptance e2e harness.
#     Builds the chart-bearing golden document via mint_python.sdk and asserts
#     trace marker counts (BLOCK_BUILD_CHART x2, BLOCK_RENDER_CHART x2,
#     BLOCK_PHASE_GUARD=0, BLOCK_LOAD_PRESET=1, BLOCK_SAVE_DOCX=1), no legacy
#     markers, lxml word/document.xml parse, M-VALIDATE lenient hard_count=0,
#     EMU width precision, fingerprint idempotency across two builds, audit
#     baseline pin gated by MP_CHART_E2E_WRITE_BASELINE, and VF-013
#     non-regression (Phase-7 chart-free golden doc fingerprint UNCHANGED
#     after Phase-8 in-place edits).
#   SCOPE: Single integration module covering all VF-014 invariants. Reuses
#     central tests/unit/conftest.py fixtures (re-exported via
#     tests/integration/conftest.py) and tests.unit._mp_helpers helpers.
#   DEPENDS: pytest, lxml, zipfile, mint.fingerprint, mint.validate,
#     mint.config (MintConfig + SeverityMode), tests.unit._mp_helpers
#     (build_chart_golden_document, build_golden_document,
#     assert_chart_inline_shape_emu, assert_no_legacy_markers,
#     load_audit_baseline, load_chart_audit_baseline,
#     write_chart_audit_baseline).
#   LINKS: docs/verification-plan.xml#VF-014,
#     docs/verification-plan.xml#V-MP-DOCUMENT scenario-9 (audit-baseline pin)
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_vf_014_trace_marker_counts - VF-014 trace-sequence
#   test_vf_014_no_legacy_markers - VF-014 forbidden-3
#   test_vf_014_lxml_parses_word_document_xml - VF-014 lxml parse
#   test_vf_014_lenient_validate_passes - VF-014 expected-outcome (c)
#   test_vf_014_inline_shape_emu_widths - VF-014 inv-3 (EMU PRECISION)
#   test_vf_014_idempotent_under_fingerprint - VF-014 inv-2
#   test_vf_014_baseline_pin - VF-014 inv-6 (audit-baseline pin)
#   test_vf_014_inv1_vf013_non_regression - VF-014 inv-1 (VF-013 unchanged)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-8-2 (VF-014): initial provisioning — Phase-8 chart e2e
#     harness covering 8 invariants including VF-013 non-regression guard.
# END_CHANGE_SUMMARY
"""VF-014 PurePythonChartFlow — Phase-8 acceptance e2e."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

import pytest

# Snapshot MP_CHART_E2E_WRITE_BASELINE at module import time, BEFORE the
# autouse mp_clean_env fixture (tests/unit/conftest.py) scrubs it. mp_clean_env
# scrubs MP_E2E_WRITE_BASELINE; the chart variant is not scrubbed there but we
# follow the same module-level snapshot pattern as VF-013 for consistency.
_CHART_BASELINE_WRITE_REQUESTED = (
    os.environ.get("MP_CHART_E2E_WRITE_BASELINE") == "1"
)


# START_BLOCK_TEST_TRACE_MARKER_COUNTS
def test_vf_014_trace_marker_counts(
    tmp_docx_path: Path,
    caplog_at_info: pytest.LogCaptureFixture,
    marker_counter: Any,
) -> None:
    """VF-014 trace-sequence: BLOCK_BUILD_CHART=2, BLOCK_RENDER_CHART=2, BLOCK_PHASE_GUARD=0."""
    from tests.unit._mp_helpers import build_chart_golden_document

    build_chart_golden_document(tmp_docx_path)
    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_BUILD_CHART"] == 2, f"counts={dict(counts)}"
    assert counts["BLOCK_RENDER_CHART"] == 2, f"counts={dict(counts)}"
    assert counts.get("BLOCK_PHASE_GUARD", 0) == 0, f"counts={dict(counts)}"
    assert counts["BLOCK_LOAD_PRESET"] == 1, f"counts={dict(counts)}"
    assert counts["BLOCK_SAVE_DOCX"] == 1, f"counts={dict(counts)}"
# END_BLOCK_TEST_TRACE_MARKER_COUNTS


# START_BLOCK_TEST_NO_LEGACY_MARKERS
def test_vf_014_no_legacy_markers(
    tmp_docx_path: Path,
    caplog_at_info: pytest.LogCaptureFixture,
) -> None:
    """VF-014 forbidden-3: no [Sandbox]/[Validate]/[Edit]/etc. on the build path."""
    from tests.unit._mp_helpers import (
        assert_no_legacy_markers,
        build_chart_golden_document,
    )

    build_chart_golden_document(tmp_docx_path)
    assert_no_legacy_markers(caplog_at_info)
# END_BLOCK_TEST_NO_LEGACY_MARKERS


# START_BLOCK_TEST_LXML_PARSES
def test_vf_014_lxml_parses_word_document_xml(tmp_docx_path: Path) -> None:
    """VF-014: word/document.xml parses via lxml without raising."""
    from lxml import etree

    from tests.unit._mp_helpers import build_chart_golden_document

    out = build_chart_golden_document(tmp_docx_path)
    with zipfile.ZipFile(out) as zf:
        with zf.open("word/document.xml") as f:
            etree.parse(f)
        names = set(zf.namelist())
    assert "word/document.xml" in names
    assert "word/styles.xml" in names
    assert "[Content_Types].xml" in names
# END_BLOCK_TEST_LXML_PARSES


# START_BLOCK_TEST_LENIENT_VALIDATE_PASSES
def test_vf_014_lenient_validate_passes(
    tmp_docx_path: Path, mp_minimal_config: Any
) -> None:
    """VF-014 expected-outcome (c): M-VALIDATE lenient passes hard_count=0."""
    from mint.validate import validate
    from tests.unit._mp_helpers import build_chart_golden_document

    out = build_chart_golden_document(tmp_docx_path)
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


# START_BLOCK_TEST_INLINE_SHAPE_EMU_WIDTHS
def test_vf_014_inline_shape_emu_widths(tmp_docx_path: Path) -> None:
    """VF-014 inv-3 (EMU PRECISION): both charts at 5.0 inches → emu == 4_572_000."""
    from docx import Document

    from tests.unit._mp_helpers import (
        assert_chart_inline_shape_emu,
        build_chart_golden_document,
    )

    out = build_chart_golden_document(tmp_docx_path)
    doc = Document(str(out))
    assert_chart_inline_shape_emu(doc, [5.0, 5.0])
# END_BLOCK_TEST_INLINE_SHAPE_EMU_WIDTHS


# START_BLOCK_TEST_IDEMPOTENT_UNDER_FINGERPRINT
def test_vf_014_idempotent_under_fingerprint(tmp_path: Path) -> None:
    """VF-014 inv-2: two builds → equal mint.fingerprint hash.

    Fingerprint hashes word/styles.xml + word/numbering.xml only (or falls
    back to word/document.xml when neither is present). The chart PNG bytes
    live in word/media/* and are NOT part of the fingerprint, so structural
    fingerprint equality across builds is independent of matplotlib output
    determinism.
    """
    from mint.fingerprint import fingerprint
    from tests.unit._mp_helpers import build_chart_golden_document

    a = tmp_path / "a.docx"
    b = tmp_path / "b.docx"
    build_chart_golden_document(a)
    build_chart_golden_document(b)
    assert fingerprint(a).hash == fingerprint(b).hash
# END_BLOCK_TEST_IDEMPOTENT_UNDER_FINGERPRINT


# START_BLOCK_TEST_BASELINE_PIN
def test_vf_014_baseline_pin(
    tmp_docx_path: Path, chart_baseline_path: Path
) -> None:
    """VF-014 inv-6: structural fingerprint matches mp_chart_e2e_baseline.json.

    On first run with MP_CHART_E2E_WRITE_BASELINE=1, writes the baseline.
    On subsequent runs, asserts equality.
    """
    from mint.fingerprint import fingerprint
    from tests.unit._mp_helpers import (
        build_chart_golden_document,
        load_chart_audit_baseline,
        write_chart_audit_baseline,
    )

    out = build_chart_golden_document(tmp_docx_path)
    fp_hash = fingerprint(out).hash

    if _CHART_BASELINE_WRITE_REQUESTED:
        # Re-set env so write_chart_audit_baseline's gate passes; the
        # mp_clean_env autouse may scrub Phase-7's MP_E2E_WRITE_BASELINE
        # but does not touch MP_CHART_E2E_WRITE_BASELINE — set it here for
        # belt-and-suspenders.
        os.environ["MP_CHART_E2E_WRITE_BASELINE"] = "1"
        baseline_data = {
            "schema_version": "1.0",
            "committed_at": "2026-05-09",
            "generator": "mint_python 0.2.0 + matplotlib 3.10.x",
            "document_spec": "tests/unit/_mp_helpers.py::build_chart_golden_document",
            "structural_fingerprint_sha256": fp_hash,
            "chart_count": 2,
            "schema_validation": {"lxml_parses": True, "ecma376_validated": False},
        }
        write_chart_audit_baseline(baseline_data)
        assert baseline_data["structural_fingerprint_sha256"] == fp_hash
    else:
        baseline = load_chart_audit_baseline()
        assert baseline["structural_fingerprint_sha256"] == fp_hash, (
            f"chart baseline drift detected. "
            f"expected={baseline['structural_fingerprint_sha256']!r}, "
            f"got={fp_hash!r}. See V-MP-DOCUMENT/baseline-update-protocol "
            "if intentional."
        )
# END_BLOCK_TEST_BASELINE_PIN


# START_BLOCK_TEST_INV1_VF013_NON_REGRESSION
def test_vf_014_inv1_vf013_non_regression(tmp_path: Path) -> None:
    """VF-014 inv-1: Phase-7 VF-013 golden doc fingerprint UNCHANGED after Phase-8.

    Critical guard — Wave-8-2 in-place edit on MP-SECTION (add_chart unstub)
    must NOT have shifted the chart-free golden doc's structural fingerprint.
    If this fails, diff section.py against pre-Wave-8-2 commit and revert any
    unrelated changes.
    """
    from mint.fingerprint import fingerprint
    from tests.unit._mp_helpers import build_golden_document, load_audit_baseline

    out = tmp_path / "vf013.docx"
    build_golden_document(out)
    fp_hash = fingerprint(out).hash

    baseline = load_audit_baseline()  # mp_e2e_baseline.json from Phase-7
    assert baseline["structural_fingerprint_sha256"] == fp_hash, (
        "VF-013 regression detected after Phase-8 in-place edits. "
        f"expected={baseline['structural_fingerprint_sha256']!r}, "
        f"got={fp_hash!r}. "
        "Diff section.py against pre-Wave-8-2 commit; revert any unrelated changes."
    )
# END_BLOCK_TEST_INV1_VF013_NON_REGRESSION
