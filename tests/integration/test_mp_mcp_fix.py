# FILE: tests/integration/test_mp_mcp_fix.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-1 — V-MP-MCP-FIX scenarios 1-8 covering
#     MP-MCP-FIX (mint_fix_document tool). Safe-fix path, destructive
#     short-circuit, path traversal, BACKUP-BEFORE-WRITE byte equality,
#     unwritable backup target, no-convergence cascade, log marker shape,
#     and the VF-020 inv-2 NO-AUTH-CALL sentinel.
#   SCOPE: Integration tests against the MP-FIX pure-python core wired
#     through the MP-MCP-FIX wrap. Uses caplog_at_info from the shared
#     conftest and the sample_docs / write_to_tmp helpers from
#     tests/_helpers/.
#   DEPENDS: pytest, pytest-asyncio, mint_python.mcp.fix (unit under
#     test), mint_python.fix (the pure-python core),
#     mint_python.mcp.auth (sentinel patch target for VF-020 inv-2),
#     tests._helpers.fake_mcp_context, tests._helpers.sample_docs.
#   LINKS: docs/verification-plan.xml#V-MP-MCP-FIX,
#     docs/verification-plan.xml#VF-020
# END_MODULE_CONTRACT
from __future__ import annotations

import hashlib
import logging
import os
import stat
import zipfile
from pathlib import Path

import pytest

from mint_python import fix as fix_module
from mint_python.mcp import auth as auth_module
from mint_python.mcp import fix as mcp_fix_module
from mint_python.mcp.fix import (
    CANONICAL_FIX_KEYS,
    BackupFailed,
    CascadeDetected,
    DestructiveRejected,
    InvalidDocument,
    mint_fix_document,
)
from mint_python.rules import FixCategory, Severity, Violation
from mint_python.validate import ValidationReport
from tests._helpers.fake_mcp_context import FakeMCPContext
from tests._helpers.sample_docs import (
    not_a_zip_bytes,
    path_traversal_sentinel,
    valid_memo_docx_bytes,
    write_to_tmp,
)

_PKG_NS = "http://schemas.openxmlformats.org/package/2006"
_OFC_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_OFC_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document.main+xml"
)
_REL_PKG_TYPE = "application/vnd.openxmlformats-package.relationships+xml"

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="{_PKG_NS}/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Default Extension="rels" ContentType="{_REL_PKG_TYPE}"/>'
    '<Override PartName="/word/document.xml" '
    f'ContentType="{_OFC_TYPE}"/>'
    "</Types>"
)
_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{_PKG_NS}/relationships">'
    f'<Relationship Id="rId1" Type="{_OFC_NS}/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _build_raw_newline_docx(path: Path) -> None:
    """Build a fixable D-H09 docx (mirrors tests/unit/test_mp_fix.py)."""
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<w:document"
        ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        "<w:body><w:p><w:r><w:t>hello\nworld</w:t></w:r></w:p></w:body>\n"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("_rels/.rels", _RELS_XML)
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Scenario-1 — safe-fix path: D-H09 newline → fix applies, .bak exists, canonical dict.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_safe_fix_d_h09_applies_and_writes_backup(
    tmp_path: Path,
) -> None:
    """A docx with a D-H09 newline violation is repaired in place. The
    returned dict carries exactly the 6 canonical keys, applied_fixes
    contains 'D-H09', backup_path points at an existing `.bak` beside the
    original, and diff_summary is 'file modified'."""
    docx = tmp_path / "raw_newline.docx"
    _build_raw_newline_docx(docx)

    ctx = FakeMCPContext(answers={})
    result = await mint_fix_document(str(docx), ctx=ctx)

    assert set(result.keys()) == set(CANONICAL_FIX_KEYS)
    assert "D-H09" in result["applied_fixes"]
    assert result["backup_path"] is not None
    assert Path(result["backup_path"]).exists()
    assert Path(result["backup_path"]).name == "raw_newline.docx.bak"
    assert result["diff_summary"] == "file modified"
    assert result["severity_mode"] == "lenient"
    assert isinstance(result["remaining_violations"], list)
    assert isinstance(result["iterations"], int) and result["iterations"] >= 1


# --------------------------------------------------------------------------- #
# Scenario-2 — destructive short-circuit: structured error, source unchanged.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_destructive_rejected_leaves_source_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A violation classified DESTRUCTIVE short-circuits with
    DESTRUCTIVE_REJECTED. The source bytes are byte-identical before/after
    the call, and no `.bak` file is written."""
    docx = write_to_tmp(tmp_path, "doc.docx", valid_memo_docx_bytes())
    pre_hash = _sha256(docx)

    destructive = Violation(
        rule_id="D-H03",
        severity=Severity.HARD,
        fix_category=FixCategory.DESTRUCTIVE,
        message="percentage width table",
        hint="convert to fixed widths",
    )

    def fake_run_checks(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return ValidationReport(
            violations=[destructive],
            total=1,
            hard_count=1,
            soft_count=0,
            mode="lenient",
            passed=False,
            document_format="docx",
        )

    monkeypatch.setattr(mcp_fix_module, "run_checks", fake_run_checks)

    ctx = FakeMCPContext(answers={})
    with pytest.raises(DestructiveRejected, match="DESTRUCTIVE_REJECTED"):
        await mint_fix_document(str(docx), ctx=ctx)

    assert _sha256(docx) == pre_hash
    assert not Path(str(docx) + ".bak").exists()


# --------------------------------------------------------------------------- #
# Scenario-3 — path traversal rejected BEFORE any zipfile open.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_path_traversal_rejected_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`../../etc/passwd` is rejected by safe_doc; no zipfile.ZipFile open
    happens. Sentinel records every ZipFile() call inside the patched
    window — the list must stay empty."""
    zip_calls: list[object] = []
    real_init = zipfile.ZipFile.__init__

    def _sentinel_init(self, file, *args, **kwargs):  # type: ignore[no-untyped-def]
        zip_calls.append(file)
        return real_init(self, file, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", _sentinel_init)

    # is_zipfile also opens internally; guard the call sequence by patching
    # it to a sentinel too. The contract: NEITHER open is reached when
    # safe_doc rejects.
    is_zip_calls: list[object] = []
    real_is_zip = zipfile.is_zipfile

    def _sentinel_is_zip(filename):  # type: ignore[no-untyped-def]
        is_zip_calls.append(filename)
        return real_is_zip(filename)

    monkeypatch.setattr(zipfile, "is_zipfile", _sentinel_is_zip)

    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_fix_document(path_traversal_sentinel(), ctx=ctx)

    assert zip_calls == [], (
        f"path traversal opened a zip before rejection: {zip_calls!r}"
    )
    assert is_zip_calls == [], (
        f"path traversal called is_zipfile before rejection: {is_zip_calls!r}"
    )


@pytest.mark.asyncio
async def test_scenario_3_b_not_a_zip_raises_invalid_document(
    tmp_path: Path,
) -> None:
    """A plain-text .docx surface raises INVALID_DOCUMENT, not whatever
    BadZipFile traceback the validator would otherwise leak."""
    bogus = write_to_tmp(tmp_path, "fake.docx", not_a_zip_bytes())
    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_fix_document(str(bogus), ctx=ctx)


@pytest.mark.asyncio
async def test_scenario_3_c_missing_file_raises_invalid_document(
    tmp_path: Path,
) -> None:
    """A path that doesn't exist raises INVALID_DOCUMENT (not regular file)."""
    missing = tmp_path / "does_not_exist.docx"
    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_fix_document(str(missing), ctx=ctx)


@pytest.mark.asyncio
async def test_scenario_3_d_unknown_severity_mode_raises_invalid_document(
    tmp_path: Path,
) -> None:
    """An out-of-domain severity_mode raises INVALID_DOCUMENT BEFORE any
    filesystem touch — keeps the guard symmetric for caller mistakes."""
    docx = write_to_tmp(tmp_path, "doc.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        # Cast to satisfy mypy in tests — runtime check is the point.
        await mint_fix_document(str(docx), severity_mode="bogus", ctx=ctx)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Scenario-4 — VF-020 inv-3 BACKUP-BEFORE-WRITE: sha256(.bak) == sha256(source_pre).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_vf_020_inv_3_backup_before_write_byte_equal(
    tmp_path: Path,
) -> None:
    """The `.bak` file's sha256 equals the source's sha256 captured BEFORE
    the call — proves the backup was a faithful pre-mutation copy."""
    docx = tmp_path / "raw_newline.docx"
    _build_raw_newline_docx(docx)
    pre_hash = _sha256(docx)

    ctx = FakeMCPContext(answers={})
    result = await mint_fix_document(str(docx), ctx=ctx)

    assert result["backup_path"] is not None
    bak = Path(result["backup_path"])
    assert bak.exists()
    assert _sha256(bak) == pre_hash, (
        "VF-020 inv-3 violation: .bak bytes diverge from pre-call source"
    )


# --------------------------------------------------------------------------- #
# Scenario-5 — BACKUP_FAILED: unwritable backup destination, source unchanged.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.geteuid() == 0,  # type: ignore[attr-defined]
    reason="root bypasses directory permission bits — cannot exercise BACKUP_FAILED",
)
async def test_scenario_5_backup_failed_when_dir_unwritable(
    tmp_path: Path,
) -> None:
    """If the parent directory is read-only, .bak can't be written and we
    raise BACKUP_FAILED. The source bytes are byte-identical after the
    failed call (apply_fixes fails before any document rewrite)."""
    parent = tmp_path / "readonly"
    parent.mkdir()
    docx = parent / "raw_newline.docx"
    _build_raw_newline_docx(docx)
    pre_hash = _sha256(docx)

    os.chmod(parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        ctx = FakeMCPContext(answers={})
        with pytest.raises(BackupFailed, match="BACKUP_FAILED"):
            await mint_fix_document(str(docx), ctx=ctx)
        assert _sha256(docx) == pre_hash
        assert not Path(str(docx) + ".bak").exists()
    finally:
        os.chmod(parent, stat.S_IRWXU)


# --------------------------------------------------------------------------- #
# Scenario-6 — CASCADE_DETECTED after max_iterations passes.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_cascade_detected_after_max_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate non-convergence: _apply_simple_fix always reports success,
    run_checks always returns the same fixable violation list, hash stays
    constant across iterations → apply_fixes raises CascadeDetectedError
    which the wrap translates to CASCADE_DETECTED."""
    docx = tmp_path / "oscillating.docx"
    _build_raw_newline_docx(docx)

    cascade_violation = Violation(
        rule_id="D-H09",
        severity=Severity.HARD,
        fix_category=FixCategory.SAFE,
        message="newline in w:t",
        hint="replace newline with space",
    )

    def fake_pre_validation(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return ValidationReport(
            violations=[cascade_violation],
            total=1,
            hard_count=1,
            soft_count=0,
            mode="lenient",
            passed=False,
            document_format="docx",
        )

    def fake_run_checks_in_fix(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return ValidationReport(
            violations=[cascade_violation],
            total=1,
            hard_count=1,
            soft_count=0,
            mode="lenient",
            passed=False,
            document_format="docx",
        )

    def always_fix(_doc_path, _violation):  # type: ignore[no-untyped-def]
        return True

    def constant_hash(_path):  # type: ignore[no-untyped-def]
        return "abc123"

    monkeypatch.setattr(mcp_fix_module, "run_checks", fake_pre_validation)
    monkeypatch.setattr(fix_module, "run_checks", fake_run_checks_in_fix)
    monkeypatch.setattr(fix_module, "_apply_simple_fix", always_fix)
    monkeypatch.setattr(fix_module, "_compute_file_hash", constant_hash)

    ctx = FakeMCPContext(answers={})
    with pytest.raises(CascadeDetected, match="CASCADE_DETECTED"):
        await mint_fix_document(str(docx), max_iterations=3, ctx=ctx)


# --------------------------------------------------------------------------- #
# Scenario-7 — Log marker BLOCK_FIX_DONE on success.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_7_log_marker_block_fix_done_on_success(
    tmp_path: Path, caplog_at_info: pytest.LogCaptureFixture
) -> None:
    """Success path emits `[MP-McpFix][apply][BLOCK_FIX_DONE]` with the
    documented payload (iterations / applied_count / remaining_violations
    / backup_path)."""
    docx = tmp_path / "raw_newline.docx"
    _build_raw_newline_docx(docx)

    caplog_at_info.clear()
    ctx = FakeMCPContext(answers={})
    with caplog_at_info.at_level(logging.INFO, logger="mint_python.mcp.fix"):
        await mint_fix_document(str(docx), ctx=ctx)

    done_msgs = [
        r.getMessage()
        for r in caplog_at_info.records
        if "BLOCK_FIX_DONE" in r.getMessage()
    ]
    assert done_msgs, "BLOCK_FIX_DONE log marker missing on success"
    msg = done_msgs[0]
    assert "[MP-McpFix][apply][BLOCK_FIX_DONE]" in msg
    assert "iterations=" in msg
    assert "applied_count=" in msg
    assert "remaining_violations=" in msg
    assert "backup_path=" in msg


@pytest.mark.asyncio
async def test_scenario_7_b_log_markers_on_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog_at_info: pytest.LogCaptureFixture,
) -> None:
    """Destructive + cascade error paths emit their respective BLOCK_*
    markers before the structured error is raised."""
    # Destructive path
    docx = write_to_tmp(tmp_path, "destr.docx", valid_memo_docx_bytes())
    destructive = Violation(
        rule_id="D-H03",
        severity=Severity.HARD,
        fix_category=FixCategory.DESTRUCTIVE,
        message="destructive",
        hint="manual",
    )

    def fake_destr(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return ValidationReport(
            violations=[destructive],
            total=1,
            hard_count=1,
            soft_count=0,
            mode="lenient",
            passed=False,
            document_format="docx",
        )

    monkeypatch.setattr(mcp_fix_module, "run_checks", fake_destr)

    caplog_at_info.clear()
    ctx = FakeMCPContext(answers={})
    with (
        caplog_at_info.at_level(logging.INFO, logger="mint_python.mcp.fix"),
        pytest.raises(DestructiveRejected),
    ):
        await mint_fix_document(str(docx), ctx=ctx)
    assert any(
        "BLOCK_DESTRUCTIVE_REJECTED" in r.getMessage()
        for r in caplog_at_info.records
    )


# --------------------------------------------------------------------------- #
# Scenario-8 — VF-020 inv-2 NO-AUTH-CALL: require_template_writer never trips.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_8_vf_020_inv_2_no_auth_call_on_fix_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch require_template_writer at every importable site with a
    sentinel that raises AssertionError. mint_fix_document must complete
    cleanly — the auto-fix surface operates on caller-owned docs and is
    forbidden by V-MP-AUTH-SHIM forbidden-1 extension from consulting
    MP-AUTH-SHIM."""

    def _sentinel(_author: str) -> None:
        raise AssertionError(
            "VF-020 inv-2 violation: fix path called "
            "require_template_writer (fix is caller-owned, not template/preset)"
        )

    monkeypatch.setattr(auth_module, "require_template_writer", _sentinel)

    docx = tmp_path / "raw_newline.docx"
    _build_raw_newline_docx(docx)

    ctx = FakeMCPContext(answers={})
    result = await mint_fix_document(str(docx), ctx=ctx)
    assert "D-H09" in result["applied_fixes"]


# --------------------------------------------------------------------------- #
# Internal helper coverage — exercises _canonicalize_report branches that the
# success scenarios don't reach (None backup_path, remaining_violations entry
# projection shape).
# --------------------------------------------------------------------------- #


def test_canonicalize_report_handles_none_backup_path() -> None:
    """When FixReport.backup_path is None (theoretical — apply_fixes always
    sets it on the non-destructive path), _canonicalize_report surfaces
    None on the canonical dict instead of stringifying."""
    from mint_python.mcp.fix import _canonicalize_report

    leftover = Violation(
        rule_id="D-H01",
        severity=Severity.SOFT,
        fix_category=FixCategory.VISUAL,
        message="leftover",
        hint="hint",
    )
    report = fix_module.FixReport(
        fixed_path=Path("/tmp/x.docx"),
        backup_path=None,
        iterations=0,
        applied_fixes=[],
        remaining_violations=[leftover],
        diff="no changes",
    )
    canon = _canonicalize_report(report, severity_mode="audit")
    assert canon["backup_path"] is None
    assert canon["remaining_violations"] == [
        {
            "rule_id": "D-H01",
            "severity": "soft",
            "fix_category": "visual",
            "message": "leftover",
        }
    ]
    assert canon["severity_mode"] == "audit"
    assert canon["diff_summary"] == "no changes"


# --------------------------------------------------------------------------- #
# Coverage — validation pre-pass failure is translated to INVALID_DOCUMENT.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_validation_prepass_failure_translates_to_invalid_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If run_checks itself raises (e.g. internal parse failure on a
    structurally-valid-but-semantically-broken zip), the wrap translates
    the failure to INVALID_DOCUMENT rather than leaking the traceback."""
    docx = write_to_tmp(tmp_path, "doc.docx", valid_memo_docx_bytes())

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated parser failure")

    monkeypatch.setattr(mcp_fix_module, "run_checks", boom)

    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_fix_document(str(docx), ctx=ctx)
