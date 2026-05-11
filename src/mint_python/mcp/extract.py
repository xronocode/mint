# FILE: src/mint_python/mcp/extract.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-2 (MP-MCP-EXTRACT) — close the MCP-parity gap
#     left by Phase-15 (no MCP surface for design-token extraction). Exposes
#     the `mint_extract_content` MCP tool as a thin async wrap over the
#     pure-python `mint_python.extract.extract_style` so external clients
#     (Claude Desktop, LibreChat, Cursor) can baseline an existing
#     .docx / .pptx (UC-004: design-tokens.json provenance). Read-only;
#     no allowlist (V-MP-AUTH-SHIM forbidden-1 extends to read tools —
#     VF-020 inv-2 READ-PATH-NEVER-CALLS-AUTH).
#   SCOPE: Public surface = `mint_extract_content` (FastMCP tool),
#     InvalidDocument + ExtractionFailed + UnsupportedFormat structured
#     tool errors, `CANONICAL_EXTRACT_KEYS` constant (VF-020 inv-5
#     STABLE-KEYS oracle), `_reshape_tokens` (testable internal — flat
#     extract dict → nested canonical wrap dict).
#   DEPENDS: fastmcp (Context + ToolError; server reused from
#     mint_python.mcp.document — shared instance pattern, see
#     mcp/manifest.py / mcp/validate.py), mint_python.extract.extract_style
#     + ExtractionFailedError + UnsupportedFormatError,
#     mint._security.safe_doc (path-traversal guard fired BEFORE any
#     zipfile open — VF-020 inv-4 PATH-TRAVERSAL-PRE-ZIP).
#   LINKS: docs/development-plan.xml#MP-MCP-EXTRACT,
#     docs/verification-plan.xml#V-MP-MCP-EXTRACT,
#     docs/verification-plan.xml#VF-020,
#     docs/knowledge-graph.xml#MP-MCP-EXTRACT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   InvalidDocument               - structured ToolError; INVALID_DOCUMENT
#                                   (path traversal / missing file)
#   ExtractionFailed              - structured ToolError; EXTRACTION_FAILED
#                                   (BadZipFile / unparseable archive
#                                   surfaced via ExtractionFailedError)
#   UnsupportedFormat             - structured ToolError; UNSUPPORTED_FORMAT
#                                   (extension neither .docx nor .pptx)
#   CANONICAL_EXTRACT_KEYS        - frozenset of the 4 keys always present
#                                   in the returned wrap dict (VF-020 inv-5)
#   _reshape_tokens               - flat dict from mint_python.extract →
#                                   nested {format, theme, layouts,
#                                   extracted_at}
#   mint_extract_content          - @server.tool async fn; production
#                                   entry registered on shared FastMCP
#                                   `server`
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-16 Wave-16-2 (MP-MCP-EXTRACT). Initial
#     module. SHAPE-DECISION: chose **option (B) NESTED RESHAPE** at the
#     wrap boundary. The W1 port (commit a020cfd) preserved the legacy
#     FLAT dict `{colors, typography, format, xml_sources,
#     detected_layouts?}` per Constraint-8 NO-DIVERGENCE (it's the
#     porting-parity oracle). The MCP wrap is a NEW public surface — no
#     existing wrap clients to preserve — so we ship the dev-plan's
#     nested shape `{format, theme: {colors, typography, xml_sources},
#     layouts: [...], extracted_at: ISO8601}` as the contract external
#     clients see. Rationale: (1) nested groups belong together
#     semantically (colors+typography+xml_sources = theme); (2)
#     extracted_at is wrap-time provenance that doesn't belong in the
#     pure extractor; (3) downstream MCP consumers will be tools-of-LLMs
#     that benefit from a self-describing dict shape over a flat blob.
#     For the missing-theme path we keep behavior parity with W1 (option
#     (a) in the task brief): intact-zip + missing theme.xml returns
#     success with empty theme sub-dict (not EXTRACTION_FAILED). Errors
#     inherit fastmcp.exceptions.ToolError so FastMCP surfaces them to
#     the client without traceback bleed (VF-020 forbidden-4 / inv-6).
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError

from mint._security import safe_doc
from mint_python.extract import (
    ExtractionFailedError,
    UnsupportedFormatError,
    extract_style,
)
from mint_python.mcp.document import server

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-McpExtract"

_SUPPORTED_SUFFIXES = (".docx", ".pptx")


# --------------------------------------------------------------------------- #
# Canonical dict shape — pinned for VF-020 inv-5 CANONICAL-DICT-STABLE-KEYS.
# External MCP clients depend on this exact key set; any change is a contract
# break and must update CANONICAL_EXTRACT_KEYS plus the integration tests.
#
# Per CHANGE_SUMMARY: we chose option (B) NESTED RESHAPE at the wrap
# boundary. The 4-key surface mirrors the dev-plan output spec; the
# pure-python `mint_python.extract.extract_style` continues to return the
# legacy FLAT shape for porting-parity with `mint.extract`.
# --------------------------------------------------------------------------- #


CANONICAL_EXTRACT_KEYS: frozenset[str] = frozenset(
    {
        "format",
        "theme",
        "layouts",
        "extracted_at",
    }
)


_THEME_KEYS: tuple[str, ...] = ("colors", "typography", "xml_sources")


# --------------------------------------------------------------------------- #
# Errors — surfaced via fastmcp.exceptions.ToolError so FastMCP wraps them
# into a structured MCP error response without leaking a Python traceback
# (VF-020 forbidden-4 / inv-6 STRUCTURED-ERRORS-NO-TRACEBACK).
# --------------------------------------------------------------------------- #


class InvalidDocument(ToolError):  # noqa: N818 — code INVALID_DOCUMENT mirrors class name
    """Path traversal / missing file rejected by the wrap-layer pre-checks."""


class UnsupportedFormat(ToolError):  # noqa: N818 — code UNSUPPORTED_FORMAT mirrors class name
    """Path suffix is neither .docx nor .pptx."""


class ExtractionFailed(ToolError):  # noqa: N818 — code EXTRACTION_FAILED mirrors class name
    """Underlying extractor raised ExtractionFailedError (corrupt zip,
    unparseable theme XML, etc.)."""


# --------------------------------------------------------------------------- #
# Reshape — flat extract dict → nested wrap dict
# --------------------------------------------------------------------------- #


def _reshape_tokens(flat: dict[str, Any]) -> dict[str, Any]:
    """Project the flat `extract_style` output into the canonical 4-key dict.

    Input shape (from mint_python.extract.extract_style):
        {'colors': {...}, 'typography': {...}, 'format': 'docx'|'pptx',
         'xml_sources': [...], 'detected_layouts'?: [...]}

    Output shape (canonical for the MCP boundary):
        {'format': str, 'theme': {colors, typography, xml_sources},
         'layouts': list, 'extracted_at': ISO8601 str}

    `extracted_at` is wrap-time provenance (datetime.now in UTC, ISO8601
    format with a 'Z' / '+00:00' offset suffix). `layouts` is `[]` when the
    underlying extractor didn't surface a `detected_layouts` key (the W1
    port only adds it when at least one layout was detected — see
    src/mint_python/extract.py extract_style line 247-249).
    """
    theme: dict[str, Any] = {
        "colors": flat.get("colors", {}),
        "typography": flat.get("typography", {}),
        "xml_sources": list(flat.get("xml_sources", [])),
    }
    return {
        "format": flat.get("format", ""),
        "theme": theme,
        "layouts": list(flat.get("detected_layouts", [])),
        "extracted_at": datetime.now(tz=UTC).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Public tool — mint_extract_content
#
# `server` is the shared FastMCP instance owned by mint_python.mcp.document
# (see mcp/manifest.py / mcp/validate.py / mcp/fix.py for the same pattern).
# The tail-import wiring in document.py registers this module on import so
# the decorator runs at exactly one site.
# --------------------------------------------------------------------------- #


@server.tool(name="mint_extract_content")
async def mint_extract_content(
    document_path: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Extract design-tokens + statistical layouts from a .docx / .pptx.

    Returns a canonical dict with keys exactly matching
    CANONICAL_EXTRACT_KEYS: `format` (\"docx\" / \"pptx\"), `theme`
    ({colors, typography, xml_sources}), `layouts` (list of {type, count}
    sub-dicts, possibly empty), `extracted_at` (ISO8601 UTC timestamp of
    this extract call). Read-only by contract — the underlying
    `mint_python.extract.extract_style` opens the zip in mode='r'.

    Args:
        document_path: Filesystem path to the .docx / .pptx the caller owns.
        ctx: FastMCP context (reserved; not currently consumed).

    Raises:
        InvalidDocument: path traversal rejected by safe_doc or file
            missing.
        UnsupportedFormat: extension isn't .docx / .pptx.
        ExtractionFailed: archive is corrupt, theme XML unparseable, or
            otherwise unreadable.
    """
    del ctx  # reserved for future progress reporting (e.g. per-layer progress)

    # ---- Path traversal guard --------------------------------------------
    # safe_doc fires BEFORE any zipfile open (VF-020 inv-4
    # PATH-TRAVERSAL-PRE-ZIP). Both ValueError (own raises) and OSError
    # (resolve() under perverse paths) collapse to INVALID_DOCUMENT.
    try:
        resolved = safe_doc(document_path)
    except (ValueError, OSError) as exc:
        raise InvalidDocument(
            f"INVALID_DOCUMENT: path traversal or invalid path "
            f"document_path={document_path!r}: {exc}"
        ) from exc

    # ---- Suffix check ----------------------------------------------------
    # Surface UNSUPPORTED_FORMAT at the wrap boundary BEFORE delegating —
    # the backend wraps UnsupportedFormatError into ExtractionFailedError
    # (src/mint_python/extract.py line 240-242), so without this pre-check
    # a .txt path would surface as EXTRACTION_FAILED. The dev-plan error
    # map keeps these distinct, and connected models can route on the
    # prefix without parsing prose.
    if resolved.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise UnsupportedFormat(
            f"UNSUPPORTED_FORMAT: extension must be one of "
            f"{list(_SUPPORTED_SUFFIXES)!r} "
            f"document_path={document_path!r}"
        )

    if not resolved.is_file():
        raise InvalidDocument(
            f"INVALID_DOCUMENT: not a regular file "
            f"document_path={document_path!r}"
        )

    # ---- Delegate to the backend -----------------------------------------
    # mint_python.extract.extract_style raises ExtractionFailedError for
    # BadZipFile / missing-file / lxml ParseError. The intact-zip +
    # missing-theme.xml case is NOT an error in the W1 port (line 247-249
    # treats `detected_layouts` as optional and parse_theme tolerates a
    # missing theme1.xml entry — see V-MP-EXTRACT scenario-4 reinterpreted
    # by W1). We preserve that semantics at the wrap boundary: a docx with
    # an intact zip and no theme.xml returns success with empty theme.
    try:
        flat = extract_style(resolved)
    except UnsupportedFormatError as exc:  # pragma: no cover — pre-checked above; defensive
        raise UnsupportedFormat(
            f"UNSUPPORTED_FORMAT: {exc} document_path={document_path!r}"
        ) from exc
    except ExtractionFailedError as exc:
        raise ExtractionFailed(
            f"EXTRACTION_FAILED: {exc} document_path={document_path!r}"
        ) from exc

    canonical = _reshape_tokens(flat)

    theme_keys_count = len(canonical["theme"]["colors"]) + len(
        canonical["theme"]["typography"]
    )
    layouts_count = len(canonical["layouts"])

    # START_BLOCK_EXTRACT_DONE
    logger.info(
        "[%s][run][BLOCK_EXTRACT_DONE] "
        "format=%s theme_keys_count=%d layouts_count=%d",
        _LOG_PREFIX,
        canonical["format"],
        theme_keys_count,
        layouts_count,
    )
    # END_BLOCK_EXTRACT_DONE

    return canonical


__all__ = [
    "CANONICAL_EXTRACT_KEYS",
    "ExtractionFailed",
    "InvalidDocument",
    "UnsupportedFormat",
    "mint_extract_content",
]
