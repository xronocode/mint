# FILE: tests/integration/test_mp_mcp_edit.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-3c — V-MP-MCP-EDIT scenarios 1-9 covering
#     MP-MCP-EDIT (mint_edit_document tool). Text-replace success path,
#     EDIT_PLAN_INVALID short-circuit + no .bak, wrap-layer raw-OOXML
#     rejection (UC-008 / VF-021 inv-3), path traversal, BACKUP-PRE-MUTATION
#     byte equality, VF-021 inv-5 NO-AUTH-CALL sentinel, log marker shape,
#     CANONICAL-EDIT-RESULT-KEYS oracle, and atomic-or-restorable on
#     mid-pipeline op failure. Also exercises every internal helper branch
#     so the new module hits 100% line coverage.
#   SCOPE: Integration tests against the MP-EDIT pure-python core wired
#     through the MP-MCP-EDIT wrap. Uses caplog_at_info from the shared
#     conftest and tests/_helpers/{fake_mcp_context,sample_docs}.
#   DEPENDS: pytest, pytest-asyncio, mint_python.mcp.edit (unit under
#     test), mint_python.edit (the pure-python core), mint_python.mcp.auth
#     (sentinel patch target for VF-021 inv-5).
#   LINKS: docs/verification-plan.xml#V-MP-MCP-EDIT,
#     docs/verification-plan.xml#VF-021
# END_MODULE_CONTRACT
from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path
from typing import Any

import pytest

from mint_python.edit import EditError, EditResult, OpOutcome
from mint_python.mcp import auth as auth_module
from mint_python.mcp.edit import (
    CANONICAL_EDIT_RESULT_KEYS,
    EditAnchorAmbiguous,
    EditAnchorNotFound,
    EditBackupFailed,
    EditOpUnsupported,
    EditPlanInvalid,
    EditTrackedChangeInvalid,
    EditUnknown,
    EditValidationFailed,
    InvalidDocument,
    _canonicalize_edit_result,
    _canonicalize_op_outcome,
    _reject_raw_ooxml_in_plan,
    _remap_edit_error,
    mint_edit_document,
)
from tests._helpers.fake_mcp_context import FakeMCPContext
from tests._helpers.sample_docs import (
    not_a_zip_bytes,
    path_traversal_sentinel,
    valid_memo_docx_bytes,
    write_to_tmp,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #


def _multi_para_docx(tmp_path: Path, *paragraphs: str) -> Path:
    """Build a tiny well-formed DOCX with N paragraphs of plain text.

    Mirrors the helper in tests/unit/test_mp_edit.py so the integration tests
    exercise the same fixture shape MP-EDIT was W3b-validated against. We
    start from minimal_valid.docx and overwrite document.xml + styles.xml +
    [Content_Types].xml to give the edit pipeline a clean tree.
    """
    fixtures = Path(__file__).resolve().parent.parent / "fixtures"
    src = fixtures / "minimal_valid.docx"
    out = tmp_path / "multi.docx"

    with zipfile.ZipFile(src) as z:
        entries = {n: z.read(n) for n in z.namelist()}

    body_parts = [f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs]
    body = "".join(body_parts)
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{r_ns}" '
        f'xmlns:w14="{W14_NS}">'
        "<w:body>"
        f"{body}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
        "</w:body></w:document>"
    ).encode()
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:styles xmlns:w="{W_NS}">'
        '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
        "</w:styles>"
    ).encode()
    ct = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b'<Types xmlns="http://schemas.openxmlformats.org/'
        b'package/2006/content-types">'
        b'<Default Extension="rels" '
        b'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'<Override PartName="/word/document.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.'
        b'wordprocessingml.document.main+xml"/>'
        b'<Override PartName="/word/styles.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.'
        b'wordprocessingml.styles+xml"/>'
        b"</Types>"
    )
    entries["word/document.xml"] = document_xml
    entries["word/styles.xml"] = styles_xml
    entries["[Content_Types].xml"] = ct
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, d in entries.items():
            zo.writestr(n, d)
    return out


def _plan_dict_text_replace(
    *, op_id: str = "r1", anchor_value: int = 0
) -> dict[str, Any]:
    """Build a single-op replace_text EditPlan dict.

    Anchored by paragraph_index so the test fixture's first paragraph
    "Hello world." becomes "Hi world." on success.
    """
    return {
        "format": "docx",
        "ops": [
            {
                "type": "replace_text",
                "op_id": op_id,
                "anchor": {
                    "type": "paragraph_index",
                    "value": anchor_value,
                    "part": "document",
                },
                "old_text": "Hello",
                "new_text": "Hi",
            }
        ],
        "metadata": {"model": "test"},
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Scenario-1 — text-replace via MCP: 10 canonical keys, success envelope.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_text_replace_via_mcp(tmp_path: Path) -> None:
    """A well-formed replace_text plan succeeds; canonical dict has the
    10 documented keys; output_path exists, success=True, ops_succeeded=1.
    """
    docx = _multi_para_docx(tmp_path, "Hello world.", "Second paragraph.")
    ctx = FakeMCPContext(answers={})

    result = await mint_edit_document(
        str(docx), _plan_dict_text_replace(), severity_mode="lenient", ctx=ctx
    )

    assert set(result.keys()) == set(CANONICAL_EDIT_RESULT_KEYS)
    assert result["success"] is True
    assert result["ops_total"] == 1
    assert result["ops_succeeded"] == 1
    assert result["ops_failed"] == 0
    assert result["output_path"] is not None
    assert Path(result["output_path"]).exists()
    assert result["backup_path"] is not None
    assert Path(result["backup_path"]).exists()
    assert isinstance(result["diff"], list) and len(result["diff"]) == 1
    assert result["diff"][0]["op_id"] == "r1"
    assert "Hi" in result["diff"][0]["after_snippet"]
    assert result["error"] is None
    assert isinstance(result["duration_ms"], int)
    # validation_report comes from MP-MCP-VALIDATE's canonicalizer when
    # the post-edit validation runs to completion.
    assert isinstance(result["validation_report"], dict)
    assert result["validation_report"]["severity_mode"] == "lenient"


# --------------------------------------------------------------------------- #
# Scenario-2 — EDIT_PLAN_INVALID short-circuits BEFORE any backup.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_plan_invalid_no_io(tmp_path: Path) -> None:
    """A plan dict with an op missing the `op_id` field raises
    EditPlanInvalid; no .bak is written beside the source."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    bad_plan: dict[str, Any] = {
        "format": "docx",
        "ops": [
            {
                "type": "replace_text",
                # op_id intentionally absent
                "anchor": {"type": "paragraph_index", "value": 0},
                "old_text": "x",
                "new_text": "y",
            }
        ],
    }
    ctx = FakeMCPContext(answers={})
    with pytest.raises(EditPlanInvalid, match="EDIT_PLAN_INVALID"):
        await mint_edit_document(str(docx), bad_plan, ctx=ctx)
    assert not Path(str(docx) + ".bak").exists()


# --------------------------------------------------------------------------- #
# Scenario-3 — wrap-layer raw-OOXML rejection (UC-008 / VF-021 inv-3).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_raw_ooxml_rejected(tmp_path: Path) -> None:
    """An anchor.value carrying `<w:r>` is rejected at the wrap layer
    BEFORE edit_plan_from_dict touches the dict. The W3b port deferred
    this rejection to the MCP boundary on purpose (UC-008 acceptance)."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    plan: dict[str, Any] = {
        "format": "docx",
        "ops": [
            {
                "type": "replace_text",
                "op_id": "r1",
                "anchor": {
                    "type": "text",
                    "value": "<w:r>raw markup</w:r>",
                },
                "old_text": "x",
                "new_text": "y",
            }
        ],
    }
    ctx = FakeMCPContext(answers={})
    with pytest.raises(EditPlanInvalid, match="raw OOXML markup"):
        await mint_edit_document(str(docx), plan, ctx=ctx)
    assert not Path(str(docx) + ".bak").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("needle", ["<w:r", "<w:p", "<w:t", "</w:r>", "<W:T"])
async def test_scenario_3_b_raw_ooxml_needles_matrix(
    tmp_path: Path, needle: str
) -> None:
    """Every documented needle (`<w:r`, `<w:p`, `<w:t`, `</w:`) is rejected,
    including the case-insensitive variant — the wrap lowercases the
    anchor.value before comparing."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    plan: dict[str, Any] = {
        "format": "docx",
        "ops": [
            {
                "type": "replace_text",
                "op_id": "r1",
                "anchor": {"type": "text", "value": f"prefix {needle} suffix"},
                "old_text": "x",
                "new_text": "y",
            }
        ],
    }
    ctx = FakeMCPContext(answers={})
    with pytest.raises(EditPlanInvalid):
        await mint_edit_document(str(docx), plan, ctx=ctx)


def test_reject_raw_ooxml_skips_non_string_values() -> None:
    """The walker is defensive against shapes edit_plan_from_dict will
    later reject — ops not a list, op not a dict, anchor not a dict,
    anchor.value not a string. None of these branches raise; they pass
    through silently to the downstream validator."""
    # ops not a list
    _reject_raw_ooxml_in_plan({"ops": "not-a-list"})
    # op not a dict
    _reject_raw_ooxml_in_plan({"ops": ["string-op"]})
    # anchor not a dict
    _reject_raw_ooxml_in_plan({"ops": [{"anchor": "not-a-dict"}]})
    # anchor.value not a string (e.g. paragraph_index uses int)
    _reject_raw_ooxml_in_plan(
        {"ops": [{"anchor": {"type": "paragraph_index", "value": 0}}]}
    )
    # no ops key at all
    _reject_raw_ooxml_in_plan({"format": "docx"})


def test_reject_raw_ooxml_uses_unknown_op_id_when_missing() -> None:
    """When the offending op has no op_id key the message names it as
    `<unknown>` rather than crashing."""
    with pytest.raises(EditPlanInvalid, match="<unknown>"):
        _reject_raw_ooxml_in_plan(
            {"ops": [{"anchor": {"type": "text", "value": "<w:p>"}}]}
        )


# --------------------------------------------------------------------------- #
# Scenario-4 — path traversal rejected BEFORE any zipfile open.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`../../etc/passwd` is rejected by safe_doc with no zipfile open."""
    zip_calls: list[object] = []
    real_init = zipfile.ZipFile.__init__

    def _sentinel_init(self, file, *args, **kwargs):  # type: ignore[no-untyped-def]
        zip_calls.append(file)
        return real_init(self, file, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", _sentinel_init)

    is_zip_calls: list[object] = []
    real_is_zip = zipfile.is_zipfile

    def _sentinel_is_zip(filename):  # type: ignore[no-untyped-def]
        is_zip_calls.append(filename)
        return real_is_zip(filename)

    monkeypatch.setattr(zipfile, "is_zipfile", _sentinel_is_zip)

    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_edit_document(
            path_traversal_sentinel(), _plan_dict_text_replace(), ctx=ctx
        )
    assert zip_calls == []
    assert is_zip_calls == []


@pytest.mark.asyncio
async def test_scenario_4_b_not_a_zip(tmp_path: Path) -> None:
    """A plain-text file at a .docx path raises INVALID_DOCUMENT."""
    bogus = write_to_tmp(tmp_path, "fake.docx", not_a_zip_bytes())
    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="not a valid zip"):
        await mint_edit_document(str(bogus), _plan_dict_text_replace(), ctx=ctx)


@pytest.mark.asyncio
async def test_scenario_4_c_missing_file(tmp_path: Path) -> None:
    """A nonexistent path raises INVALID_DOCUMENT (not a regular file)."""
    missing = tmp_path / "does_not_exist.docx"
    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="not a regular file"):
        await mint_edit_document(
            str(missing), _plan_dict_text_replace(), ctx=ctx
        )


@pytest.mark.asyncio
async def test_scenario_4_d_unknown_severity_mode(tmp_path: Path) -> None:
    """Out-of-domain severity_mode raises INVALID_DOCUMENT BEFORE any IO."""
    docx = write_to_tmp(tmp_path, "doc.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext(answers={})
    with pytest.raises(InvalidDocument, match="unknown severity_mode"):
        await mint_edit_document(
            str(docx),
            _plan_dict_text_replace(),
            severity_mode="bogus",  # type: ignore[arg-type]
            ctx=ctx,
        )


# --------------------------------------------------------------------------- #
# Scenario-5 — VF-021 inv-2 BACKUP-PRE-MUTATION: sha256(.bak) == pre-call sha256.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_bak_byte_equality(tmp_path: Path) -> None:
    """The .bak file's sha256 equals the source's sha256 captured BEFORE
    the call — proves the backup is a faithful pre-mutation copy."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    pre_hash = _sha256(docx)
    ctx = FakeMCPContext(answers={})

    result = await mint_edit_document(
        str(docx), _plan_dict_text_replace(), ctx=ctx
    )

    bak = Path(result["backup_path"])
    assert bak.exists()
    assert _sha256(bak) == pre_hash, (
        "VF-021 inv-2 violation: .bak bytes diverge from pre-call source"
    )


# --------------------------------------------------------------------------- #
# Scenario-6 — VF-021 inv-5 NO-AUTH-CALL: require_template_writer never trips.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_no_auth_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch require_template_writer with a sentinel that raises
    AssertionError. mint_edit_document must complete cleanly — edit
    operates on caller-owned docs and V-MP-AUTH-SHIM forbidden-1
    extension forbids MP-AUTH-SHIM dispatch."""

    def _sentinel(_author: str) -> None:
        raise AssertionError(
            "VF-021 inv-5 violation: edit path called require_template_writer "
            "(edit is caller-owned, not template/preset)"
        )

    monkeypatch.setattr(auth_module, "require_template_writer", _sentinel)

    docx = _multi_para_docx(tmp_path, "Hello world.")
    ctx = FakeMCPContext(answers={})
    result = await mint_edit_document(
        str(docx), _plan_dict_text_replace(), ctx=ctx
    )
    assert result["success"] is True


# --------------------------------------------------------------------------- #
# Scenario-7 — outer log marker BLOCK_EDIT_DONE + inner MP-EDIT markers.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_7_outer_and_inner_markers(
    tmp_path: Path, caplog_at_info: pytest.LogCaptureFixture
) -> None:
    """Success path emits `[MP-McpEdit][edit][BLOCK_EDIT_DONE]` with the
    documented payload (ops_total / ops_succeeded / ops_failed /
    duration_ms). Inner BLOCK_EDIT_BACKUP and BLOCK_EDIT_APPLY_OP markers
    fire from MP-EDIT."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    caplog_at_info.clear()
    ctx = FakeMCPContext(answers={})

    with caplog_at_info.at_level(logging.INFO):
        await mint_edit_document(
            str(docx), _plan_dict_text_replace(), ctx=ctx
        )

    msgs = [r.getMessage() for r in caplog_at_info.records]
    outer = [m for m in msgs if "[MP-McpEdit][edit][BLOCK_EDIT_DONE]" in m]
    assert outer, "BLOCK_EDIT_DONE outer marker missing on success"
    msg = outer[0]
    assert "ops_total=" in msg
    assert "ops_succeeded=" in msg
    assert "ops_failed=" in msg
    assert "duration_ms=" in msg

    # Inner MP-EDIT markers — these prove the wrap delegates to the W3b core.
    assert any("BLOCK_EDIT_BACKUP" in m for m in msgs), "inner backup marker missing"


# --------------------------------------------------------------------------- #
# Scenario-8 — VF-021 inv-6 CANONICAL-EDIT-RESULT-KEYS oracle.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_8_canonical_keys(tmp_path: Path) -> None:
    """The returned dict.keys() match CANONICAL_EDIT_RESULT_KEYS exactly —
    no extra keys, no missing keys, in the documented order."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    ctx = FakeMCPContext(answers={})

    result = await mint_edit_document(
        str(docx), _plan_dict_text_replace(), ctx=ctx
    )

    assert tuple(result.keys()) == CANONICAL_EDIT_RESULT_KEYS


# --------------------------------------------------------------------------- #
# Scenario-9 — mid-pipeline op failure → structured error with op_id;
# .bak is byte-identical to pre-call source (VF-021 inv-4).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_9_atomic_or_restorable(tmp_path: Path) -> None:
    """An anchor that doesn't resolve mid-pipeline (paragraph_index=99
    on a 1-paragraph doc) returns EditResult.success=False; the wrap
    surfaces this as EditAnchorNotFound carrying the failing op_id. The
    .bak exists and is byte-identical to the pre-call source — the
    'restorable' half of atomic-or-restorable."""
    docx = _multi_para_docx(tmp_path, "Only paragraph.")
    pre_hash = _sha256(docx)

    plan = _plan_dict_text_replace(op_id="oops", anchor_value=99)
    ctx = FakeMCPContext(answers={})

    with pytest.raises(EditAnchorNotFound, match="op_id='oops'"):
        await mint_edit_document(str(docx), plan, ctx=ctx)

    bak = Path(str(docx) + ".bak")
    assert bak.exists()
    assert _sha256(bak) == pre_hash


# --------------------------------------------------------------------------- #
# Internal-helper coverage — exercise _remap_edit_error / _canonicalize_*
# branches that the integration scenarios don't reach.
# --------------------------------------------------------------------------- #


def test_remap_edit_error_covers_every_known_code() -> None:
    """Every code in _EDIT_ERROR_MAP routes to the documented subclass."""
    cases: list[tuple[str, type]] = [
        ("EDIT_PLAN_INVALID", EditPlanInvalid),
        ("EDIT_OP_UNSUPPORTED", EditOpUnsupported),
        ("EDIT_ANCHOR_NOT_FOUND", EditAnchorNotFound),
        ("EDIT_ANCHOR_AMBIGUOUS", EditAnchorAmbiguous),
        ("EDIT_VALIDATION_FAILED", EditValidationFailed),
        ("EDIT_TRACKED_CHANGE_INVALID", EditTrackedChangeInvalid),
        ("BACKUP_FAILED", EditBackupFailed),
    ]
    for code, cls in cases:
        err = EditError("boom", code=code)
        mapped = _remap_edit_error(err, document_path="/tmp/x.docx")
        assert isinstance(mapped, cls), code


def test_remap_edit_error_unknown_code_falls_through_to_edit_unknown() -> None:
    """An unrecognized code routes to EditUnknown with EDIT_UNKNOWN prefix."""
    err = EditError("future-refactor", code="EDIT_NEW_CODE_FROM_FUTURE")
    mapped = _remap_edit_error(err, document_path="/tmp/x.docx")
    assert isinstance(mapped, EditUnknown)
    assert "EDIT_UNKNOWN" in str(mapped)


def test_remap_edit_error_backup_failed_is_renamed_to_edit_backup_failed() -> None:
    """The MP-EDIT BACKUP_FAILED code is remapped to EDIT_BACKUP_FAILED on
    the MCP surface so the vocabulary stays EDIT_-prefixed."""
    err = EditError("disk full", code="BACKUP_FAILED")
    mapped = _remap_edit_error(err, document_path="/tmp/x.docx")
    assert isinstance(mapped, EditBackupFailed)
    assert "EDIT_BACKUP_FAILED" in str(mapped)
    assert "BACKUP_FAILED" not in str(mapped).replace("EDIT_BACKUP_FAILED", "")


def test_remap_edit_error_carries_op_id_when_provided() -> None:
    """Mid-pipeline op_id is appended to the surfaced message."""
    err = EditError("anchor not in tree", code="EDIT_ANCHOR_NOT_FOUND")
    mapped = _remap_edit_error(
        err, document_path="/tmp/x.docx", op_id="op-42"
    )
    assert "op_id='op-42'" in str(mapped)


def test_canonicalize_op_outcome_round_trip() -> None:
    """OpOutcome dataclass → 6-key sub-dict with primitive values."""
    outcome = OpOutcome(
        op_id="op1",
        success=True,
        error_code=None,
        affected_part="document",
        before_snippet="before",
        after_snippet="after",
    )
    d = _canonicalize_op_outcome(outcome)
    assert d == {
        "op_id": "op1",
        "success": True,
        "error_code": None,
        "affected_part": "document",
        "before_snippet": "before",
        "after_snippet": "after",
    }


def test_canonicalize_edit_result_handles_none_validation_report() -> None:
    """When EditResult.validation_report is None (mid-pipeline failure)
    the canonical dict surfaces None on validation_report and output_path."""
    result = EditResult(
        output_path=None,
        backup_path=Path("/tmp/x.docx.bak"),
        success=False,
        ops_total=1,
        ops_succeeded=0,
        ops_failed=1,
        validation_report=None,
        diff=[
            OpOutcome(
                op_id="op1",
                success=False,
                error_code="EDIT_ANCHOR_NOT_FOUND",
                affected_part="document",
                before_snippet="",
                after_snippet="",
            )
        ],
        duration_ms=42,
        error="anchor not found",
    )
    canon = _canonicalize_edit_result(result, "lenient")
    assert canon["output_path"] is None
    assert canon["backup_path"] == "/tmp/x.docx.bak"
    assert canon["validation_report"] is None
    assert canon["error"] == "anchor not found"
    assert canon["duration_ms"] == 42
    assert tuple(canon.keys()) == CANONICAL_EDIT_RESULT_KEYS


@pytest.mark.asyncio
async def test_edit_plan_from_dict_format_pptx_raises_op_unsupported(
    tmp_path: Path,
) -> None:
    """plan.format='pptx' is caught by validate_plan (which edit() runs
    first) with code=EDIT_OP_UNSUPPORTED. The wrap remaps to
    EditOpUnsupported. Note: edit_plan_from_dict accepts pptx, so this
    branch goes through the second EditError catch around _backend_edit."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    plan: dict[str, Any] = {
        "format": "pptx",
        "ops": [
            {
                "type": "replace_text",
                "op_id": "r1",
                "anchor": {"type": "paragraph_index", "value": 0},
                "old_text": "x",
                "new_text": "y",
            }
        ],
    }
    ctx = FakeMCPContext(answers={})
    with pytest.raises(EditOpUnsupported, match="EDIT_OP_UNSUPPORTED"):
        await mint_edit_document(str(docx), plan, ctx=ctx)


@pytest.mark.asyncio
async def test_edit_plan_from_dict_unknown_format_raises_plan_invalid(
    tmp_path: Path,
) -> None:
    """plan.format='xlsx' is rejected by edit_plan_from_dict directly
    with EDIT_PLAN_INVALID — covers the pre-edit() catch branch."""
    docx = _multi_para_docx(tmp_path, "Hello world.")
    plan: dict[str, Any] = {
        "format": "xlsx",
        "ops": [
            {
                "type": "replace_text",
                "op_id": "r1",
                "anchor": {"type": "paragraph_index", "value": 0},
                "old_text": "x",
                "new_text": "y",
            }
        ],
    }
    ctx = FakeMCPContext(answers={})
    with pytest.raises(EditPlanInvalid, match="EDIT_PLAN_INVALID"):
        await mint_edit_document(str(docx), plan, ctx=ctx)


def test_canonical_edit_result_keys_pinned() -> None:
    """The 10 canonical keys + their order are pinned to the EditResult
    dataclass shape. If MP-EDIT adds a field this test fails loudly so
    the wrap + verification plan both get updated."""
    assert CANONICAL_EDIT_RESULT_KEYS == (
        "output_path",
        "backup_path",
        "success",
        "ops_total",
        "ops_succeeded",
        "ops_failed",
        "validation_report",
        "diff",
        "duration_ms",
        "error",
    )
