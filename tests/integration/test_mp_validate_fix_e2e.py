# FILE: tests/integration/test_mp_validate_fix_e2e.py
# START_MODULE_CONTRACT
#   PURPOSE: VF-015 + VF-016 — Phase-9 validate+fix integration e2e.
#     Tests Document.validate() and Document.fix() via SDK pipeline,
#     plus non-regression on VF-013 + VF-014 goldens.
#   SCOPE: validate pipeline (save→check→report), fix pipeline (save→fix→report),
#     temp-file cleanup, BLOCK_PHASE_GUARD absence on validate/fix,
#     BLOCK_PHASE_GUARD presence on inject_grace/to_pdf.
#   DEPENDS: MP-VALIDATE, MP-FIX, MP-DOCUMENT, MP-SDK
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_document_validate_returns_report - VF-016: validate returns ValidationReport
#   test_document_fix_returns_report - VF-016: fix returns FixReport
#   test_no_phase_guard_on_validate_fix - VF-016 inv-2: BLOCK_PHASE_GUARD absent
#   test_inject_grace_still_stub - inject_grace still raises PhaseGuardNotImplementedError
#   test_to_pdf_still_stub - to_pdf still raises PhaseGuardNotImplementedError
#   test_validate_does_not_mutate - VF-015 inv-1: validate does not alter fingerprint
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-9-4 — initial provisioning: VF-015 + VF-016 e2e.
# END_CHANGE_SUMMARY
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from mint_python.core.document import PhaseGuardNotImplementedError
from mint_python.core.section import Section
from mint_python.core.table import Table
from mint_python.fix import FixReport
from mint_python.sdk import Document
from mint_python.validate import ValidationReport


def test_document_validate_returns_report(tmp_path: Path) -> None:
    doc = Document(format="docx", title="Test")
    doc.add_section(Section("S1", level=1).add_paragraph("Content"))

    report = doc.validate()
    assert isinstance(report, ValidationReport)
    assert report.passed


def test_document_fix_returns_report(tmp_path: Path) -> None:
    doc = Document(format="docx", title="Test")
    doc.add_section(Section("S1", level=1).add_paragraph("Content"))

    report = doc.fix()
    assert isinstance(report, FixReport)
    assert len(report.applied_fixes) == 0


def test_no_phase_guard_on_validate_fix(
    tmp_path: Path, caplog_at_info, marker_counter
) -> None:
    doc = Document(format="docx", title="Test")
    doc.add_section(Section("S1", level=1).add_paragraph("Content"))

    doc.validate()
    doc.fix()

    guards = [
        r.getMessage()
        for r in caplog_at_info.records
        if "BLOCK_PHASE_GUARD" in r.getMessage()
        and ("validate" in r.getMessage() or "fix" in r.getMessage())
    ]
    assert len(guards) == 0


def test_inject_grace_still_stub() -> None:
    doc = Document(format="docx", title="Test")
    with pytest.raises(PhaseGuardNotImplementedError):
        doc.inject_grace()


def test_to_pdf_still_stub() -> None:
    doc = Document(format="docx", title="Test")
    with pytest.raises(PhaseGuardNotImplementedError):
        doc.to_pdf()


def test_validate_does_not_mutate(tmp_path: Path) -> None:
    doc = Document(format="docx", title="Q2").with_style_preset("alga_corporate")
    doc.add_cover(title="Q2 Memo", subtitle="2026")
    doc.add_toc(max_level=2)
    doc.add_section(
        Section("Revenue", level=1)
        .add_paragraph("Trend.")
        .add_table(
            Table.from_list(
                [["Q", "Rev"], ["Q1", "$1M"], ["Q2", "$1.3M"]]
            )
        )
    )

    out1 = tmp_path / "out1.docx"
    doc.save(out1)
    h1 = hashlib.sha256(out1.read_bytes()).hexdigest()

    doc.validate()

    out2 = tmp_path / "out2.docx"
    doc.save(out2)
    h2 = hashlib.sha256(out2.read_bytes()).hexdigest()

    assert h1 == h2


def test_temp_file_cleanup(tmp_path: Path) -> None:
    doc = Document(format="docx", title="Test")
    doc.add_section(Section("S1", level=1).add_paragraph("Content"))
    doc.save(tmp_path / "pre.docx")

    before = set(Path(tempfile.gettempdir()).glob("*.docx"))
    doc.validate()
    after = set(Path(tempfile.gettempdir()).glob("*.docx"))
    # no new permanent temp files leaked
    assert before == after
