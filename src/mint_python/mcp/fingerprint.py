# FILE: src/mint_python/mcp/fingerprint.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-2 (MP-MCP-FINGERPRINT) — thin MCP wrap over
#     the pure-python `mint_python.fingerprint.fingerprint` + `compare`
#     port that landed in W1 (commit b5d4748). Exposes the
#     `mint_fingerprint_document` MCP tool so connected models can
#     fingerprint a caller-owned .docx / .pptx (and optionally compare
#     against a baseline hash) through the same governed surface that
#     produced it. Closes the MCP-parity gap left after Phase-15
#     retired the legacy fingerprint MCP entry alongside mcp_g1/g2.
#   SCOPE: Public surface = `mint_fingerprint_document` (FastMCP tool),
#     FingerprintDocumentError + 2 structured error subclasses
#     (InvalidDocument / MissingStylesXml), `CANONICAL_FP_KEYS` (the
#     5-key tuple of the canonical dict shape, oracle for VF-020 inv-5
#     STABLE-KEYS), and `_canonicalize_result` (testable internal
#     mapping mint_python.fingerprint.FingerprintResult + optional
#     baseline_hash → canonical dict).
#   DEPENDS: fastmcp (Context, FastMCP server reused from
#     mint_python.mcp.document — shared instance pattern, see
#     mcp/manifest.py:72), mint_python.fingerprint (fingerprint +
#     compare + DriftStatus + FingerprintError / MissingStyleXmlError /
#     HashFailedError + FingerprintResult), mint._security.safe_doc
#     (path traversal guard fired BEFORE any zipfile open — VF-020
#     inv-4 PATH-TRAVERSAL-PRE-ZIP).
#   LINKS: docs/development-plan.xml#MP-MCP-FINGERPRINT,
#     docs/verification-plan.xml#V-MP-MCP-FINGERPRINT,
#     docs/verification-plan.xml#VF-020,
#     docs/knowledge-graph.xml#MP-MCP-FINGERPRINT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FingerprintDocumentError      - base error for the tool
#   InvalidDocument               - INVALID_DOCUMENT (path traversal /
#                                   non-zip / missing / unsupported
#                                   extension)
#   MissingStylesXml              - MISSING_STYLES_XML (zip is valid but
#                                   carries no style XML members — the
#                                   W1 port's MissingStyleXmlError
#                                   surfaced as a structured tool error)
#   CANONICAL_FP_KEYS             - tuple of the 5 keys always present
#                                   in the returned dict (VF-020 inv-5
#                                   STABLE-KEYS oracle)
#   _canonicalize_result          - FingerprintResult + baseline_hash →
#                                   canonical 5-key dict
#   mint_fingerprint_document     - @server.tool async fn; production
#                                   entry registered on shared FastMCP
#                                   `server`
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-16 Wave-16-2 (MP-MCP-FINGERPRINT). Initial
#     module. Wraps the pure-python fingerprint() pipeline; canonical
#     dict shape is the contract surface external clients depend on, so
#     key set + order is pinned via CANONICAL_FP_KEYS for VF-020 inv-5.
#     drift_status is the DriftStatus StrEnum value as a string ("match"
#     / "drift" / "unknown") when baseline_hash is supplied, else None.
#     NO MP-AUTH-SHIM call site here: fingerprint is a READ tool over a
#     caller-owned doc (V-MP-AUTH-SHIM forbidden-1 + VF-020 inv-2
#     NO-AUTH-CALL).
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError

from mint._security import safe_doc
from mint_python.fingerprint import (
    FingerprintError,
    FingerprintResult,
    MissingStyleXmlError,
    compare,
    fingerprint,
)
from mint_python.mcp.document import server
from mint_python.mcp.telemetry import track_call

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-McpFingerprint"


# --------------------------------------------------------------------------- #
# Canonical dict shape — pinned for VF-020 inv-5 CANONICAL-DICT-STABLE-KEYS.
# External MCP clients depend on this exact key set; any change is a contract
# break and must update CANONICAL_FP_KEYS plus the integration tests.
# Key ORDER mirrors the FingerprintResult dataclass field order, with
# drift_status appended last because it depends on caller-supplied state
# (baseline_hash), not on the fingerprint computation itself.
# --------------------------------------------------------------------------- #


CANONICAL_FP_KEYS: tuple[str, ...] = (
    "hash",
    "format",
    "has_styles_xml",
    "byte_count",
    "drift_status",
)


# --------------------------------------------------------------------------- #
# Errors — surfaced via fastmcp.exceptions.ToolError so FastMCP wraps them
# into a structured MCP error response without leaking a Python traceback
# (VF-020 forbidden-4 / inv-6 STRUCTURED-ERRORS-NO-TRACEBACK). Each carries
# a code-style message prefix so connected models can route on the prefix
# without parsing prose.
# --------------------------------------------------------------------------- #


class FingerprintDocumentError(ToolError):
    """Base for mint_fingerprint_document tool errors."""


class InvalidDocument(FingerprintDocumentError):  # noqa: N818 — code INVALID_DOCUMENT mirrors class name
    """Path traversal / non-zip / missing file / unsupported extension."""


class MissingStylesXml(FingerprintDocumentError):  # noqa: N818 — code MISSING_STYLES_XML mirrors class name
    """Document zip carries no style XML members the hash can stand over."""


# --------------------------------------------------------------------------- #
# Canonicalizer
# --------------------------------------------------------------------------- #


def _canonicalize_result(
    result: FingerprintResult,
    baseline_hash: str | None,
) -> dict[str, Any]:
    """Project a FingerprintResult plus an optional baseline_hash into the
    canonical 5-key dict.

    The W1 port's FingerprintResult carries (hash, format, has_styles_xml,
    byte_count); we copy those through unchanged and append drift_status —
    the DriftStatus StrEnum value as a bare string (e.g. "match" / "drift"
    / "unknown") when baseline_hash is supplied, else None. Callers reading
    the dict get a deterministic, JSON-serializable shape regardless of
    whether a comparison was requested.
    """
    if baseline_hash is None:
        drift_status: str | None = None
    else:
        # DriftStatus is a StrEnum, so its .value is already the bare string
        # contract the wire expects. We use .value rather than str() so a
        # future repr-customization on the enum can't drift the surface.
        drift_status = compare(result.hash, baseline_hash).value

    return {
        "hash": result.hash,
        "format": result.format,
        "has_styles_xml": result.has_styles_xml,
        "byte_count": result.byte_count,
        "drift_status": drift_status,
    }


# --------------------------------------------------------------------------- #
# Public tool — mint_fingerprint_document
# --------------------------------------------------------------------------- #


@server.tool(name="mint_fingerprint_document")
async def mint_fingerprint_document(
    document_path: str,
    baseline_hash: str | None = None,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Fingerprint a caller-supplied .docx / .pptx and optionally compare
    its hash against a baseline.

    Returns a canonical dict with keys exactly matching CANONICAL_FP_KEYS:
    `hash` (SHA-256 hex digest of the document's style XML), `format`
    ("docx" / "pptx"), `has_styles_xml` (bool — true iff the primary
    word/styles.xml or ppt/theme/theme1.xml member was hashed), `byte_count`
    (total bytes of style XML fed into the hash), and `drift_status`
    ("match" / "drift" / "unknown" when `baseline_hash` is supplied, else
    None).

    Read-only by contract — every zipfile open inside the backend uses
    mode='r'. NO MP-AUTH-SHIM call site (V-MP-AUTH-SHIM forbidden-1 +
    VF-020 inv-2 NO-AUTH-CALL).

    Args:
        document_path: Filesystem path to the .docx / .pptx the caller
            owns.
        baseline_hash: Optional SHA-256 hex digest from a prior
            fingerprint call. When provided, drift_status is set to the
            DriftStatus value; when None, drift_status is None.
        ctx: FastMCP context (reserved; not currently consumed).

    Raises:
        InvalidDocument: path traversal rejected by safe_doc, the file
            is missing, isn't a regular file, isn't a supported zip
            archive, or the suffix isn't .docx / .pptx.
        MissingStylesXml: document is a valid zip but carries no style
            XML members the fingerprint hash can stand over.
    """
    del ctx  # reserved for future progress reporting

    with track_call("mint_fingerprint_document"):
        # ---- Path traversal guard --------------------------------------------
        try:
            resolved = safe_doc(document_path)
        except (ValueError, OSError) as exc:
            raise InvalidDocument(
                f"INVALID_DOCUMENT: path traversal or invalid path "
                f"document_path={document_path!r}: {exc}"
            ) from exc

        if not resolved.is_file():
            raise InvalidDocument(
                f"INVALID_DOCUMENT: not a regular file "
                f"document_path={document_path!r}"
            )

        # ---- Delegate to the pure-python core --------------------------------
        try:
            result = fingerprint(resolved)
        except MissingStyleXmlError as exc:
            # START_BLOCK_FP_MISSING_STYLES
            logger.info(
                "[%s][compute][BLOCK_FP_MISSING_STYLES] document_path=%s",
                _LOG_PREFIX,
                str(resolved),
            )
            # END_BLOCK_FP_MISSING_STYLES
            raise MissingStylesXml(
                f"MISSING_STYLES_XML: no style XML members in "
                f"document_path={document_path!r}: {exc}"
            ) from exc
        except FingerprintError as exc:
            raise InvalidDocument(
                f"INVALID_DOCUMENT: fingerprint backend rejected document "
                f"document_path={document_path!r}: {exc}"
            ) from exc

        canonical = _canonicalize_result(result, baseline_hash)

        # START_BLOCK_FP_DONE
        logger.info(
            "[%s][compute][BLOCK_FP_DONE] "
            "hash=%s format=%s drift_status=%s",
            _LOG_PREFIX,
            canonical["hash"],
            canonical["format"],
            canonical["drift_status"],
        )
        # END_BLOCK_FP_DONE

        return canonical


__all__ = [
    "CANONICAL_FP_KEYS",
    "FingerprintDocumentError",
    "InvalidDocument",
    "MissingStylesXml",
    "mint_fingerprint_document",
]
