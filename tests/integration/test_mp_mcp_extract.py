# FILE: tests/integration/test_mp_mcp_extract.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-2 — V-MP-MCP-EXTRACT scenarios 1-7 + VF-020
#     invariants (1, 2, 4, 5, 6) covering MP-MCP-EXTRACT
#     (mint_extract_content MCP tool). Verifies the canonical nested dict
#     shape, structured tool errors (INVALID_DOCUMENT / UNSUPPORTED_FORMAT
#     / EXTRACTION_FAILED), log marker payload, no-auth-call read
#     invariant, no-legacy-import grep gate, and canonical key stability
#     oracle.
#   SCOPE: Integration tests against the live MP-MCP-EXTRACT tool +
#     FastMCP server registration. Uses the shared sample_docs fixture
#     bank (valid_memo_docx_bytes, no_styles_xml_docx_bytes,
#     not_a_zip_bytes, path_traversal_sentinel, write_to_tmp) and the
#     shared FakeMCPContext so the suite stays disjoint from the W1 +
#     other W2 worker test files.
#   DEPENDS: pytest, pytest-asyncio, mint_python.mcp.extract (UUT),
#     mint_python.mcp.auth (sentinel-patched in scenario-6),
#     tests._helpers.sample_docs, tests._helpers.fake_mcp_context.
#   LINKS: docs/verification-plan.xml#V-MP-MCP-EXTRACT,
#     docs/verification-plan.xml#VF-020
# END_MODULE_CONTRACT
from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import pytest

from mint_python.mcp import auth as auth_module
from mint_python.mcp.extract import (
    CANONICAL_EXTRACT_KEYS,
    ExtractionFailed,
    InvalidDocument,
    UnsupportedFormat,
    _reshape_tokens,
    mint_extract_content,
)
from tests._helpers.fake_mcp_context import FakeMCPContext
from tests._helpers.sample_docs import (
    not_a_zip_bytes,
    path_traversal_sentinel,
    valid_memo_docx_bytes,
    write_to_tmp,
)

THEME_KEYS = {"colors", "typography", "xml_sources"}
LAYOUT_KEYS = {"type", "count"}

_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


# --------------------------------------------------------------------------- #
# Local helper — fabricate a docx zip with theme.xml stripped. Scoped to this
# test file (the shared sample_docs.py is controller-owned per Wave-16-2 task
# brief).
# --------------------------------------------------------------------------- #


def _docx_without_theme_bytes() -> bytes:
    """Construct a docx zip whose word/theme/theme1.xml entry is absent.

    Drives V-MP-MCP-EXTRACT scenario-3 (intact zip + missing theme — wrap
    must return success with empty theme, not raise; option-(a) parity
    with W1 MP-EXTRACT).
    """
    src = valid_memo_docx_bytes()
    buf = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(src), "r") as src_zf,
        zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst_zf,
    ):
        for info in src_zf.infolist():
            if info.filename == "word/theme/theme1.xml":
                continue
            dst_zf.writestr(info, src_zf.read(info.filename))
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Scenario-1 — valid memo docx → canonical 4-key dict, ISO8601 timestamp
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_valid_docx_returns_canonical_shape(tmp_path: Path) -> None:
    """mint_extract_content on a valid memo docx returns the nested
    canonical dict: {format='docx', theme={colors, typography,
    xml_sources}, layouts: list, extracted_at: ISO8601}."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    result = await mint_extract_content(str(docx), ctx=ctx)

    # Canonical 4-key set.
    assert set(result.keys()) == set(CANONICAL_EXTRACT_KEYS)
    assert result["format"] == "docx"

    # Theme sub-dict shape.
    theme = result["theme"]
    assert isinstance(theme, dict)
    assert set(theme.keys()) == THEME_KEYS
    # python-docx's default theme1.xml ships a populated clrScheme +
    # fontScheme — both should be non-empty on the memo fixture.
    assert theme["colors"], "theme colors must be populated for memo fixture"
    assert theme["typography"], "theme typography must be populated for memo fixture"
    assert isinstance(theme["xml_sources"], list)

    # Layouts: list (possibly empty); each entry is {type, count}.
    assert isinstance(result["layouts"], list)
    for layout in result["layouts"]:
        assert set(layout.keys()) == LAYOUT_KEYS

    # extracted_at: ISO8601 with timezone (datetime.isoformat() in UTC).
    assert _ISO8601_RE.match(result["extracted_at"]), (
        f"extracted_at not ISO8601 with tz suffix: {result['extracted_at']!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-2 — .txt path → UNSUPPORTED_FORMAT
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_unsupported_format(tmp_path: Path) -> None:
    """A path with .txt extension is rejected with UNSUPPORTED_FORMAT at
    the wrap boundary (BEFORE extract_style would wrap it in
    ExtractionFailedError)."""
    txt = tmp_path / "doc.txt"
    txt.write_text("not a document")
    ctx = FakeMCPContext()

    with pytest.raises(UnsupportedFormat, match="UNSUPPORTED_FORMAT"):
        await mint_extract_content(str(txt), ctx=ctx)


# --------------------------------------------------------------------------- #
# Scenario-3 — intact zip + missing theme.xml → success with empty theme
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_missing_theme_returns_empty_theme(tmp_path: Path) -> None:
    """A docx with an intact zip but no word/theme/theme1.xml returns
    success with empty theme.colors + empty theme.typography (option (a)
    parity with the W1 MP-EXTRACT port — see CHANGE_SUMMARY).

    Layouts may still be populated (word/document.xml is still present and
    parseable). xml_sources omits theme1.xml since the entry is absent.
    """
    docx = write_to_tmp(tmp_path, "no_theme.docx", _docx_without_theme_bytes())
    ctx = FakeMCPContext()

    result = await mint_extract_content(str(docx), ctx=ctx)

    assert set(result.keys()) == set(CANONICAL_EXTRACT_KEYS)
    assert result["format"] == "docx"
    assert result["theme"]["colors"] == {}
    assert result["theme"]["typography"] == {}
    assert "word/theme/theme1.xml" not in result["theme"]["xml_sources"]


# --------------------------------------------------------------------------- #
# Scenario-4 — path traversal → INVALID_DOCUMENT; no zipfile open
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_path_traversal_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`../../etc/passwd`-shaped paths are rejected by safe_doc BEFORE any
    zipfile open. Verified via a ZipFile.__init__ sentinel that records
    every call — the sentinel must NOT trip on the traversal path."""
    zip_calls: list[tuple] = []
    real_init = zipfile.ZipFile.__init__

    def _sentinel_init(self, file, *args, **kwargs):  # type: ignore[no-untyped-def]
        zip_calls.append((file, args, kwargs))
        return real_init(self, file, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", _sentinel_init)

    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_extract_content(path_traversal_sentinel(), ctx=ctx)

    assert zip_calls == [], (
        f"path traversal opened a zip before rejection: {zip_calls!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-5 — log marker BLOCK_EXTRACT_DONE carries the documented payload
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_log_marker_on_success(
    tmp_path: Path, caplog_at_info: pytest.LogCaptureFixture
) -> None:
    """[MP-McpExtract][run][BLOCK_EXTRACT_DONE] payload carries
    format + theme_keys_count + layouts_count."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    caplog_at_info.clear()
    with caplog_at_info.at_level(logging.INFO, logger="mint_python.mcp.extract"):
        await mint_extract_content(str(docx), ctx=ctx)

    msgs = [
        r.getMessage()
        for r in caplog_at_info.records
        if "BLOCK_EXTRACT_DONE" in r.getMessage()
        and "[MP-McpExtract]" in r.getMessage()
    ]
    assert msgs, "[MP-McpExtract][run][BLOCK_EXTRACT_DONE] log marker missing"
    marker = msgs[0]
    assert "[MP-McpExtract][run][BLOCK_EXTRACT_DONE]" in marker
    assert "format=docx" in marker
    assert re.search(r"\btheme_keys_count=\d+", marker)
    assert re.search(r"\blayouts_count=\d+", marker)


# --------------------------------------------------------------------------- #
# Scenario-6 — VF-020 inv-2: require_template_writer sentinel never trips
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_vf_020_inv_2_no_auth_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extract is a READ tool — it must never consult MP-AUTH-SHIM.
    Monkeypatch require_template_writer with a sentinel that raises;
    calling mint_extract_content must NOT trip it (V-MP-AUTH-SHIM
    forbidden-1 + VF-020 inv-2)."""
    trips: list[str] = []

    def _sentinel(author: str) -> None:
        trips.append(author)
        raise AssertionError(
            f"VF-020 inv-2 violation: extract consulted MP-AUTH-SHIM "
            f"for author={author!r}"
        )

    monkeypatch.setattr(auth_module, "require_template_writer", _sentinel)

    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    # Tool runs to completion without touching the sentinel.
    result = await mint_extract_content(str(docx), ctx=ctx)
    assert result["format"] == "docx"
    assert trips == []


# --------------------------------------------------------------------------- #
# Scenario-7 — VF-020 inv-1: NO-LEGACY-IMPORT grep gate
# --------------------------------------------------------------------------- #


def test_scenario_7_no_legacy_import() -> None:
    """grep -E "from mint\\.|import mint\\." in
    src/mint_python/mcp/extract.py returns 0 hits — except the single
    agreed-upon exception line `from mint._security import safe_doc`
    (matches the V-MP-MANIFEST-READ depends contract for the
    path-traversal guard)."""
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    target = repo_root / "src" / "mint_python" / "mcp" / "extract.py"
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
# Coverage tail — exercise branches the scenarios above don't reach:
# canonical key oracle on live result, not-a-zip → EXTRACTION_FAILED,
# missing file → INVALID_DOCUMENT, _reshape_tokens in isolation.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_canonical_keys_oracle(tmp_path: Path) -> None:
    """VF-020 inv-5 CANONICAL-DICT-STABLE-KEYS — the returned dict's key
    set equals frozenset(CANONICAL_EXTRACT_KEYS) exactly. Asserted on a
    fresh end-to-end call so wrap-level regressions are caught."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    result = await mint_extract_content(str(docx), ctx=ctx)

    assert frozenset(result.keys()) == CANONICAL_EXTRACT_KEYS
    assert set(result["theme"].keys()) == THEME_KEYS


@pytest.mark.asyncio
async def test_not_a_zip_raises_extraction_failed(tmp_path: Path) -> None:
    """A file with a .docx extension but non-zip contents trips the
    backend's BadZipFile → ExtractionFailedError → wrap surfaces as
    EXTRACTION_FAILED (structured, no traceback bleed)."""
    bogus = write_to_tmp(tmp_path, "fake.docx", not_a_zip_bytes())
    ctx = FakeMCPContext()

    with pytest.raises(ExtractionFailed, match="EXTRACTION_FAILED"):
        await mint_extract_content(str(bogus), ctx=ctx)


@pytest.mark.asyncio
async def test_missing_file_raises_invalid_document(tmp_path: Path) -> None:
    """A path that doesn't exist (but has a supported suffix) raises
    INVALID_DOCUMENT at the wrap's is_file check — distinct from the
    backend's ExtractionFailedError for missing-file (which would surface
    as EXTRACTION_FAILED). The wrap pre-empts that to keep the error map
    clean."""
    missing = tmp_path / "does_not_exist.docx"
    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="not a regular file"):
        await mint_extract_content(str(missing), ctx=ctx)


def test_reshape_tokens_flat_to_nested() -> None:
    """_reshape_tokens projects the flat extract_style output into the
    canonical nested dict. Covers the reshape in isolation so future
    contract changes to extract_style's output keys are caught even
    before the full integration call would observe them."""
    flat = {
        "colors": {"accent": ["#FF0000"], "dark1": "#000000"},
        "typography": {"headingFont": "Calibri"},
        "format": "docx",
        "xml_sources": ["word/theme/theme1.xml", "word/styles.xml"],
        "detected_layouts": [{"type": "paragraph", "count": 3}],
    }
    canonical = _reshape_tokens(flat)

    assert frozenset(canonical.keys()) == CANONICAL_EXTRACT_KEYS
    assert canonical["format"] == "docx"
    assert canonical["theme"]["colors"] == {"accent": ["#FF0000"], "dark1": "#000000"}
    assert canonical["theme"]["typography"] == {"headingFont": "Calibri"}
    assert canonical["theme"]["xml_sources"] == [
        "word/theme/theme1.xml",
        "word/styles.xml",
    ]
    assert canonical["layouts"] == [{"type": "paragraph", "count": 3}]
    assert _ISO8601_RE.match(canonical["extracted_at"])


def test_reshape_tokens_missing_detected_layouts() -> None:
    """When extract_style didn't add `detected_layouts` (no layouts
    detected), the wrap defaults `layouts` to an empty list — never
    KeyError, never None."""
    flat = {
        "colors": {},
        "typography": {},
        "format": "pptx",
        "xml_sources": [],
    }
    canonical = _reshape_tokens(flat)

    assert canonical["layouts"] == []
    assert canonical["theme"]["colors"] == {}
    assert canonical["theme"]["typography"] == {}
    assert canonical["theme"]["xml_sources"] == []
    assert canonical["format"] == "pptx"
