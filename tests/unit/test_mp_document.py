# FILE: tests/unit/test_mp_document.py
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-DOCUMENT scenarios 1-9 — Document fluent surface, save()
#     emit + idempotency, format/preset error paths, and the remaining Phase-N
#     stub (to_pdf; BLOCK_PHASE_GUARD trace + NotImplementedError content).
#   SCOPE: Reuses central conftest fixtures (mp_clean_env autouse,
#     tmp_docx_path, caplog_at_info, marker_counter); imports extract_marker
#     from tests.unit._mp_helpers; uses mint.fingerprint.fingerprint for
#     scenario-8 idempotency assertion. Scenario-9 (audit-baseline pin) is
#     skipped pre-Wave-7-5 with an explicit reason.
#   DEPENDS: pytest, mint_python.core.document, mint_python.core.section,
#     mint.fingerprint, lxml, python-docx.
#   LINKS: docs/verification-plan.xml#V-MP-DOCUMENT,
#     docs/development-plan.xml#MP-DOCUMENT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_scenario_1_with_style_preset_returns_self
#   test_scenario_2_fluent_builders_return_self
#   test_scenario_3_save_produces_parseable_docx
#   test_scenario_4_unsupported_format_raises
#   test_scenario_5_unknown_preset_raises_document_preset_not_found
#   test_scenario_6_stubs_emit_phase_guard_then_raise
#   test_scenario_7_save_emits_block_save_docx_with_payload
#   test_scenario_8_save_is_fingerprint_idempotent
#   test_scenario_9_audit_baseline_pin (skipped pre-Wave-7-5)
#   test_phase_guard_is_subclass_of_not_implemented_error
#   test_save_returns_path
#   test_with_style_from_path_loads_preset
#   test_inject_grace_returns_grace_manifest
#   test_inject_grace_via_document_pipeline
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-11-1 — retire inject_grace from stub parametrize;
#     add test that inject_grace returns GRACEManifest via MP-GRACE delegation
#   PRIOR: Wave-9-4 — retire Phase-7 BLOCK_PHASE_GUARD assertions for
#     validate/fix; add tests that validate returns ValidationReport and
#     fix returns FixReport.
#   PRIOR: Wave-7-4 (MP-DOCUMENT): initial test suite.
# END_CHANGE_SUMMARY
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from mint.fingerprint import fingerprint
from mint_python.core.document import (
    DOCUMENT_FIXED_TIMESTAMP,
    Document,
    DocumentFormatUnsupportedError,
    DocumentPresetNotFoundError,
    PhaseGuardNotImplementedError,
)
from mint_python.core.section import Section
from mint_python.grace import GRACEManifest

# Audit baseline path — scenario-9 is skipped until Wave-7-5 produces the
# baseline file. Mirrors _mp_helpers._BASELINE_PATH so the skip condition
# is self-consistent.
_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "mp_e2e_baseline.json"
)


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-1: with_style_preset returns self (fluent surface)
# ---------------------------------------------------------------------------


def test_scenario_1_with_style_preset_returns_self() -> None:
    doc = Document(format="docx", title="X")
    result = doc.with_style_preset("alga_corporate")
    assert result is doc


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-2: every fluent builder returns self
# ---------------------------------------------------------------------------


def test_scenario_2_fluent_builders_return_self() -> None:
    doc = Document(format="docx", title="X").with_style_preset("alga_corporate")

    section = Section("Body", level=1)

    assert doc.add_cover(title="X") is doc
    assert doc.add_section(section) is doc
    assert doc.add_toc(max_level=2) is doc
    assert doc.set_header("hdr") is doc
    assert doc.set_footer("ftr") is doc


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-3: save() produces a file lxml can parse
# ---------------------------------------------------------------------------


def test_scenario_3_save_produces_parseable_docx(tmp_docx_path: Path) -> None:
    (
        Document(format="docx", title="X")
        .with_style_preset("alga_corporate")
        .add_cover(title="Cover Title", subtitle="Cover Subtitle")
        .add_toc(max_level=3)
        .add_section(Section("Body", level=1).add_paragraph("hello"))
        .set_header("hdr")
        .set_footer("ftr")
        .save(tmp_docx_path)
    )

    assert tmp_docx_path.exists()

    with zipfile.ZipFile(tmp_docx_path) as zf:
        document_xml = zf.read("word/document.xml")

    # lxml MUST be able to parse the emitted document.xml without error.
    root = etree.fromstring(document_xml)
    assert root is not None


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-4: format != 'docx' raises with valid formats listed
# ---------------------------------------------------------------------------


def test_scenario_4_unsupported_format_raises() -> None:
    with pytest.raises(DocumentFormatUnsupportedError) as exc_info:
        Document(format="pdf", title="X")  # type: ignore[arg-type]
    msg = str(exc_info.value)
    # Message MUST name the offending value AND the valid format set.
    assert "pdf" in msg
    assert "docx" in msg


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-5: unknown preset raises DocumentPresetNotFoundError
# ---------------------------------------------------------------------------


def test_scenario_5_unknown_preset_raises_document_preset_not_found() -> None:
    doc = Document(format="docx", title="X")
    with pytest.raises(DocumentPresetNotFoundError) as exc_info:
        doc.with_style_preset("does_not_exist")
    # The wrapped STYLE_PRESET_NOT_FOUND message names the missing preset.
    assert "does_not_exist" in str(exc_info.value)


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-6: each stub raises NotImplementedError; emits
# BLOCK_PHASE_GUARD before raising; message names target phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method_name,target_phase",
    [
        ("to_pdf", "Phase 5"),
    ],
)
def test_scenario_6_stubs_emit_phase_guard_then_raise(
    caplog_at_info, marker_counter, method_name: str, target_phase: str
) -> None:
    doc = Document(format="docx", title="X")
    method = getattr(doc, method_name)

    with pytest.raises(NotImplementedError) as exc_info:
        method()

    msg = str(exc_info.value)
    assert target_phase in msg

    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_PHASE_GUARD"] == 1

    guard_records = [
        r for r in caplog_at_info.records if "BLOCK_PHASE_GUARD" in r.getMessage()
    ]
    assert len(guard_records) == 1
    payload = guard_records[0].getMessage()
    assert "[MP-Document]" in payload
    assert "[stub]" in payload
    assert f"method={method_name}" in payload
    assert f"target_phase={target_phase}" in payload


def test_phase_guard_is_subclass_of_not_implemented_error() -> None:
    # The custom error MUST remain a NotImplementedError subclass so callers
    # using the standard exception type keep working.
    assert issubclass(PhaseGuardNotImplementedError, NotImplementedError)


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-7: save() emits exactly one BLOCK_SAVE_DOCX with
# section_count, table_count, output_path payload
# ---------------------------------------------------------------------------


def test_scenario_7_save_emits_block_save_docx_with_payload(
    tmp_docx_path: Path, caplog_at_info, marker_counter
) -> None:
    (
        Document(format="docx", title="X")
        .with_style_preset("alga_corporate")
        .add_cover(title="Cover")
        .add_section(Section("S1", level=1).add_paragraph("a"))
        .add_section(Section("S2", level=1).add_paragraph("b"))
        .save(tmp_docx_path)
    )

    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_SAVE_DOCX"] == 1

    save_records = [
        r for r in caplog_at_info.records if "BLOCK_SAVE_DOCX" in r.getMessage()
    ]
    assert len(save_records) == 1
    payload = save_records[0].getMessage()
    assert "[MP-Document]" in payload
    assert "[save]" in payload
    # 2 sections + 1 cover = 3
    assert "section_count=3" in payload
    assert "table_count=0" in payload
    assert f"output_path={tmp_docx_path}" in payload


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-8: idempotent save — two saves produce equal
# mint.fingerprint.fingerprint hashes
# ---------------------------------------------------------------------------


def test_scenario_8_save_is_fingerprint_idempotent(tmp_path: Path) -> None:
    """Build identical Document, save twice, assert fingerprint hashes match.

    python-docx populates core.xml dcterms:created/modified with
    datetime.now() during save; without the timestamp pin in
    Document._pin_core_xml_timestamps, the resulting fingerprints would
    diverge. This test is the regression guard for that pin.
    """

    def _build() -> Document:
        return (
            Document(format="docx", title="Idempotency Probe")
            .with_style_preset("alga_corporate")
            .add_cover(title="Cover", subtitle="Sub")
            .add_toc(max_level=3)
            .add_section(Section("Body", level=1).add_paragraph("hello"))
            .set_header("hdr")
            .set_footer("ftr")
        )

    out_a = tmp_path / "a.docx"
    out_b = tmp_path / "b.docx"

    _build().save(out_a)
    _build().save(out_b)

    hash_a = fingerprint(out_a).hash
    hash_b = fingerprint(out_b).hash

    assert hash_a == hash_b, (
        f"fingerprint hashes diverge across saves\n"
        f"  hash_a: {hash_a}\n"
        f"  hash_b: {hash_b}\n"
        f"  pinned timestamp: {DOCUMENT_FIXED_TIMESTAMP}"
    )


# ---------------------------------------------------------------------------
# V-MP-DOCUMENT scenario-9: audit-baseline pin (deferred to Wave-7-5)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _BASELINE_PATH.exists(),
    reason="baseline not yet created — Wave-7-5 produces it",
)
def test_scenario_9_audit_baseline_pin(tmp_docx_path: Path) -> None:
    """Audit-baseline pin: the Wave-7-5 e2e harness will exercise this fully.

    The placeholder body below documents the intent — once Wave-7-5 lands,
    this test compares the BLOCK_SAVE_DOCX payload + fingerprint hash
    against tests/fixtures/mp_e2e_baseline.json. Pre-Wave-7-5 the test is
    skipped via the marker above, so the suite stays green while preserving
    the V-MP-DOCUMENT scenario-9 coverage slot.
    """
    from tests.unit._mp_helpers import load_audit_baseline

    baseline = load_audit_baseline()
    # The actual assertion shape is owned by Wave-7-5 (VF-013 e2e); we only
    # surface the load here so the skip condition above remains the single
    # gating signal.
    assert isinstance(baseline, dict)


# ---------------------------------------------------------------------------
# Auxiliary surface assertions (not numbered scenarios but verify contract)
# ---------------------------------------------------------------------------


def test_save_returns_path(tmp_docx_path: Path) -> None:
    result = (
        Document(format="docx", title="X")
        .with_style_preset("alga_corporate")
        .save(tmp_docx_path)
    )
    assert result == tmp_docx_path
    assert isinstance(result, Path)


def test_with_style_from_path_loads_preset(tmp_path: Path) -> None:
    # Use a built-in preset's underlying JSON path to exercise the path-load
    # branch without needing a synthetic JSON fixture.
    from mint_python.core.style import BUILTIN_PRESETS

    preset_path = BUILTIN_PRESETS["alga_corporate"]
    doc = Document(format="docx", title="X")
    result = doc.with_style_from(preset_path)
    assert result is doc
    assert doc._preset is not None
    # _preset_name reflects the path stem per the documented fallback.
    assert doc._preset_name == preset_path.stem


# ---------------------------------------------------------------------------
# Wave-9-4: validate() and fix() are unstubbed — they return real reports
# ---------------------------------------------------------------------------


def test_validate_returns_validation_report(tmp_docx_path: Path) -> None:
    from mint_python.validate import ValidationReport

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )
    report = doc.validate()
    assert isinstance(report, ValidationReport)
    assert hasattr(report, "passed")
    # lenient default: golden doc shape passes
    assert report.passed is True


def test_fix_returns_fix_report(tmp_docx_path: Path) -> None:
    from mint_python.fix import FixReport

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )
    report = doc.fix()
    assert isinstance(report, FixReport)
    assert hasattr(report, "applied_fixes")
    assert len(report.applied_fixes) == 0


# ---------------------------------------------------------------------------
# Wave-11-1: inject_grace is unstubbed — returns GRACEManifest via MP-GRACE
# ---------------------------------------------------------------------------


def test_inject_grace_returns_grace_manifest() -> None:
    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )
    manifest = doc.inject_grace()
    assert isinstance(manifest, GRACEManifest)
    assert manifest.fingerprint != ""
    assert len(manifest.instructions) == 10
    assert manifest.xml_part_name.startswith("grace/")


def test_inject_grace_via_document_pipeline(tmp_path: Path) -> None:
    doc = (
        Document(format="docx", title="Pipeline Test")
        .with_style_preset("alga_corporate")
        .add_cover(title="Cover", subtitle="Sub")
        .add_section(Section("Body", level=1).add_paragraph("hello"))
    )
    manifest = doc.inject_grace()

    assert isinstance(manifest, GRACEManifest)
    assert manifest.document_structure["format"] == "docx"
    assert "parts" in manifest.document_structure

    # Verify the output .docx exists and has the grace part.
    output_path = tmp_path / "pipeline_grace.docx"
    doc.inject_grace(output_path=output_path)
    assert output_path.exists()

    import zipfile as zf_mod
    with zf_mod.ZipFile(output_path, "r") as zf:
        names = zf.namelist()
        assert any(n.startswith("grace/") and n.endswith(".xml") for n in names)
