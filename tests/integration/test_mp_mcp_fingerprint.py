# FILE: tests/integration/test_mp_mcp_fingerprint.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-2 — V-MP-MCP-FINGERPRINT scenarios 1-7 +
#     VF-020 invariants (1, 2, 4, 5, 6) covering MP-MCP-FINGERPRINT
#     (mint_fingerprint_document MCP tool). Verifies the canonical
#     5-key dict shape, drift_status semantics across the three
#     baseline_hash branches (None / match / drift), structured tool
#     errors for path traversal + missing-styles-xml, log marker
#     payload, no-auth-call read invariant, and the no-legacy-import
#     grep gate.
#   SCOPE: Integration tests against the live MP-MCP-FINGERPRINT tool
#     + FastMCP server registration. Uses the shared sample_docs
#     fixture bank (valid_memo_docx_bytes, no_styles_xml_docx_bytes,
#     not_a_zip_bytes, path_traversal_sentinel, write_to_tmp) and the
#     shared FakeMCPContext.
#   DEPENDS: pytest, pytest-asyncio,
#     mint_python.mcp.fingerprint (UUT),
#     mint_python.mcp.auth (sentinel-patched in VF-020 inv-2 scenario),
#     mint_python.fingerprint (DriftStatus enum + FingerprintResult),
#     tests._helpers.sample_docs, tests._helpers.fake_mcp_context.
#   LINKS: docs/verification-plan.xml#V-MP-MCP-FINGERPRINT,
#     docs/verification-plan.xml#VF-020
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import pytest

from mint_python.fingerprint import DriftStatus, FingerprintResult
from mint_python.mcp import auth as auth_module
from mint_python.mcp.fingerprint import (
    CANONICAL_FP_KEYS,
    FingerprintDocumentError,
    InvalidDocument,
    MissingStylesXml,
    _canonicalize_result,
    mint_fingerprint_document,
)
from tests._helpers.fake_mcp_context import FakeMCPContext
from tests._helpers.sample_docs import (
    no_styles_xml_docx_bytes,
    not_a_zip_bytes,
    path_traversal_sentinel,
    valid_memo_docx_bytes,
    write_to_tmp,
)

# --------------------------------------------------------------------------- #
# Scenario-1 — valid memo docx (no baseline) → 5-key dict, drift_status=None
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_valid_doc_returns_canonical_dict(tmp_path: Path) -> None:
    """mint_fingerprint_document on a valid memo docx returns the 5-key
    canonical dict with hash populated and drift_status=None when no
    baseline is supplied."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    result = await mint_fingerprint_document(str(docx), ctx=ctx)

    # Canonical key set + ORDER (CANONICAL_FP_KEYS is a tuple, so order
    # matters for the contract).
    assert tuple(result.keys()) == CANONICAL_FP_KEYS
    assert frozenset(result.keys()) == frozenset(CANONICAL_FP_KEYS)
    # Hash is a 64-char SHA-256 hex digest.
    assert isinstance(result["hash"], str)
    assert len(result["hash"]) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", result["hash"])
    assert result["format"] == "docx"
    assert result["has_styles_xml"] is True
    assert isinstance(result["byte_count"], int)
    assert result["byte_count"] > 0
    # No baseline supplied → drift_status is None (NOT the string "unknown").
    assert result["drift_status"] is None


# --------------------------------------------------------------------------- #
# Scenario-2 — drift_status semantics across the three baseline branches
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_drift_status_match_drift_and_null(tmp_path: Path) -> None:
    """drift_status is "match" when baseline_hash equals current hash,
    "drift" when it differs, and null when baseline_hash is None.

    The deterministic part of this scenario is the bijective relationship
    between (current hash, baseline) and DriftStatus. We compute the
    current hash once via a no-baseline call, then re-invoke twice: once
    with the same hash (must yield "match"), once with a known-different
    hex digest (must yield "drift")."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    # First call — no baseline; capture the current hash.
    first = await mint_fingerprint_document(str(docx), ctx=ctx)
    assert first["drift_status"] is None
    current_hash = first["hash"]

    # baseline_hash == current_hash → "match"
    match = await mint_fingerprint_document(
        str(docx), baseline_hash=current_hash, ctx=ctx
    )
    assert match["drift_status"] == DriftStatus.MATCH.value == "match"
    assert match["hash"] == current_hash

    # baseline_hash != current_hash → "drift"
    bogus_baseline = "0" * 64
    assert bogus_baseline != current_hash  # paranoia: 0*64 isn't our hash
    drift = await mint_fingerprint_document(
        str(docx), baseline_hash=bogus_baseline, ctx=ctx
    )
    assert drift["drift_status"] == DriftStatus.DRIFT.value == "drift"
    assert drift["hash"] == current_hash  # hash is unaffected by baseline

    # Explicit None baseline → still null (mirrors first call).
    explicit_none = await mint_fingerprint_document(
        str(docx), baseline_hash=None, ctx=ctx
    )
    assert explicit_none["drift_status"] is None


# --------------------------------------------------------------------------- #
# Scenario-3 — path traversal → INVALID_DOCUMENT; no zipfile open
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_path_traversal_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`../../etc/passwd`-shaped paths are rejected by safe_doc BEFORE
    any zipfile open. Verified via a ZipFile.__init__ sentinel that
    records every call — the sentinel must NOT trip on the traversal
    path (VF-020 inv-4 PATH-TRAVERSAL-PRE-ZIP)."""
    zip_calls: list[tuple] = []
    real_init = zipfile.ZipFile.__init__

    def _sentinel_init(self, file, *args, **kwargs):  # type: ignore[no-untyped-def]
        zip_calls.append((file, args, kwargs))
        return real_init(self, file, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", _sentinel_init)

    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_fingerprint_document(path_traversal_sentinel(), ctx=ctx)

    assert zip_calls == [], (
        f"path traversal opened a zip before rejection: {zip_calls!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-4 — docx without styles.xml → MISSING_STYLES_XML structured error
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_missing_styles_xml_raises_structured_error(
    tmp_path: Path,
) -> None:
    """A docx whose word/styles.xml, word/numbering.xml AND fallback
    word/document.xml are all absent must trip the W1 port's
    MissingStyleXmlError — the wrap surfaces it as MISSING_STYLES_XML.

    The shared no_styles_xml_docx_bytes() helper only drops
    word/styles.xml; it leaves word/numbering.xml (the second primary
    member) and word/document.xml (the fallback) intact, so on its own
    it would NOT trip the missing-styles branch. We strip all three
    inline here rather than adding a new fixture flavor — the helper
    surface already targets the W1 port's narrower
    MissingStyleXmlError shape, and this wrap-level scenario wants the
    strictest 'no style XML at all' branch."""
    import io

    src = no_styles_xml_docx_bytes()
    strip = {
        "word/styles.xml",
        "word/numbering.xml",
        "word/document.xml",
    }
    buf = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(src), "r") as src_zf,
        zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst_zf,
    ):
        for info in src_zf.infolist():
            if info.filename in strip:
                continue
            dst_zf.writestr(info, src_zf.read(info.filename))

    docx = write_to_tmp(tmp_path, "no_styles.docx", buf.getvalue())
    ctx = FakeMCPContext()

    with pytest.raises(MissingStylesXml, match="MISSING_STYLES_XML"):
        await mint_fingerprint_document(str(docx), ctx=ctx)


# --------------------------------------------------------------------------- #
# Scenario-5 — log marker BLOCK_FP_DONE carries the documented payload
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_log_marker_on_success(
    tmp_path: Path, caplog_at_info: pytest.LogCaptureFixture
) -> None:
    """[MP-McpFingerprint][compute][BLOCK_FP_DONE] payload carries hash +
    format + drift_status."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    caplog_at_info.clear()
    with caplog_at_info.at_level(
        logging.INFO, logger="mint_python.mcp.fingerprint"
    ):
        await mint_fingerprint_document(str(docx), ctx=ctx)

    msgs = [
        r.getMessage()
        for r in caplog_at_info.records
        if "BLOCK_FP_DONE" in r.getMessage()
    ]
    assert msgs, "BLOCK_FP_DONE log marker missing on success"
    marker = msgs[0]
    assert "[MP-McpFingerprint][compute][BLOCK_FP_DONE]" in marker
    assert re.search(r"\bhash=[0-9a-f]{64}\b", marker)
    assert "format=docx" in marker
    # drift_status=None when no baseline (repr-string of None).
    assert "drift_status=None" in marker


# --------------------------------------------------------------------------- #
# Scenario-6 — VF-020 inv-2: require_template_writer sentinel never trips
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_vf_020_inv_2_no_auth_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fingerprint is a READ tool — it must never consult MP-AUTH-SHIM.
    Monkeypatch require_template_writer with a sentinel that raises;
    calling mint_fingerprint_document must NOT trip it (V-MP-AUTH-SHIM
    forbidden-1 + VF-020 inv-2 NO-AUTH-CALL)."""
    trips: list[str] = []

    def _sentinel(author: str) -> None:
        trips.append(author)
        raise AssertionError(
            f"VF-020 inv-2 violation: fingerprint consulted MP-AUTH-SHIM "
            f"for author={author!r}"
        )

    monkeypatch.setattr(auth_module, "require_template_writer", _sentinel)

    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    # Tool runs to completion without touching the sentinel.
    result = await mint_fingerprint_document(str(docx), ctx=ctx)
    assert isinstance(result["hash"], str)
    assert trips == []


# --------------------------------------------------------------------------- #
# Scenario-7 — VF-020 inv-1: NO-LEGACY-IMPORT grep gate
# --------------------------------------------------------------------------- #


def test_scenario_7_no_legacy_import() -> None:
    """grep -E "from mint\\.|import mint\\." in
    src/mint_python/mcp/fingerprint.py returns 0 hits — except the single
    agreed-upon exception line `from mint._security import safe_doc`
    (matches the VF-020 depends contract for the path-traversal guard)."""
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    target = repo_root / "src" / "mint_python" / "mcp" / "fingerprint.py"
    assert target.is_file(), f"UUT file missing: {target}"
    text = target.read_text(encoding="utf-8")

    offenders: list[str] = []
    pattern = re.compile(r"^\s*(from mint\.|import mint\.)")
    for line in text.splitlines():
        if pattern.match(line):
            # The agreed-upon exception: mint._security.safe_doc is the
            # sole bridge into src/mint/ allowed by the module contract.
            if "from mint._security" in line:
                continue
            offenders.append(line)
    assert not offenders, (
        f"VF-020 inv-1 NO-LEGACY-IMPORT violation in {target}: "
        f"{offenders!r}"
    )


# --------------------------------------------------------------------------- #
# Coverage tail — exercise the small branches the scenarios above don't
# reach: not-a-zip → InvalidDocument; missing file; canonicalizer in
# isolation; FingerprintError base-class catch (unsupported extension);
# error inheritance from FingerprintDocumentError + ToolError.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_missing_file_raises_invalid_document(tmp_path: Path) -> None:
    """A path that doesn't exist raises INVALID_DOCUMENT before any
    backend call — distinct from non-zip (backend) and from path
    traversal (safe_doc)."""
    missing = tmp_path / "does_not_exist.docx"
    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="not a regular file"):
        await mint_fingerprint_document(str(missing), ctx=ctx)


@pytest.mark.asyncio
async def test_not_a_zip_raises_invalid_document(tmp_path: Path) -> None:
    """A non-zip file at a .docx path raises INVALID_DOCUMENT (routed
    through the FingerprintError base-class catch — backend surfaces it
    as 'Not a valid ZIP')."""
    bogus = write_to_tmp(tmp_path, "fake.docx", not_a_zip_bytes())
    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_fingerprint_document(str(bogus), ctx=ctx)


@pytest.mark.asyncio
async def test_unsupported_extension_raises_invalid_document(
    tmp_path: Path,
) -> None:
    """A non-.docx/.pptx suffix trips the backend's FingerprintError
    ('Unsupported format'); the wrap routes it to INVALID_DOCUMENT."""
    target = write_to_tmp(tmp_path, "memo.txt", valid_memo_docx_bytes())
    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_fingerprint_document(str(target), ctx=ctx)


def test_canonicalize_result_no_baseline() -> None:
    """_canonicalize_result with baseline_hash=None produces the 5-key
    dict with drift_status=None — the no-baseline branch in isolation."""
    fr = FingerprintResult(
        hash="a" * 64,
        format="docx",
        has_styles_xml=True,
        byte_count=1234,
    )
    canonical = _canonicalize_result(fr, baseline_hash=None)
    assert tuple(canonical.keys()) == CANONICAL_FP_KEYS
    assert canonical == {
        "hash": "a" * 64,
        "format": "docx",
        "has_styles_xml": True,
        "byte_count": 1234,
        "drift_status": None,
    }


def test_canonicalize_result_with_match_baseline() -> None:
    """_canonicalize_result with baseline_hash == result.hash yields
    drift_status='match' — the matching-baseline branch in isolation."""
    fr = FingerprintResult(
        hash="b" * 64,
        format="pptx",
        has_styles_xml=True,
        byte_count=42,
    )
    canonical = _canonicalize_result(fr, baseline_hash="b" * 64)
    assert canonical["drift_status"] == "match"
    assert canonical["format"] == "pptx"


def test_canonicalize_result_with_drift_baseline() -> None:
    """_canonicalize_result with baseline_hash != result.hash yields
    drift_status='drift' — the drift branch in isolation."""
    fr = FingerprintResult(
        hash="c" * 64,
        format="docx",
        has_styles_xml=False,
        byte_count=7,
    )
    canonical = _canonicalize_result(fr, baseline_hash="d" * 64)
    assert canonical["drift_status"] == "drift"
    assert canonical["has_styles_xml"] is False


def test_error_class_hierarchy() -> None:
    """InvalidDocument + MissingStylesXml inherit from
    FingerprintDocumentError, which inherits from fastmcp ToolError —
    so FastMCP routes them as structured tool errors at the boundary."""
    from fastmcp.exceptions import ToolError

    assert issubclass(FingerprintDocumentError, ToolError)
    assert issubclass(InvalidDocument, FingerprintDocumentError)
    assert issubclass(MissingStylesXml, FingerprintDocumentError)
