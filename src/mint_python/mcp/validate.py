# FILE: src/mint_python/mcp/validate.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-1 (MP-MCP-VALIDATE) — close the MCP-parity
#     gap left by Phase-15 (legacy `mint_validate` deleted along with
#     mcp_g1; no MCP successor existed). Exposes the
#     `mint_validate_document` MCP tool as a thin async wrap over the
#     pure-python `mint_python.validate.validate` so external clients
#     (Claude Desktop, LibreChat, Cursor) can validate caller-supplied
#     .docx / .pptx files through the same governed surface that produces
#     them. Read-only; no allowlist (V-MP-AUTH-SHIM forbidden-1 extends
#     to read tools — VF-020 inv-2 READ-PATH-NEVER-CALLS-AUTH).
#   SCOPE: Public surface = `mint_validate_document` (FastMCP tool),
#     `InvalidDocument` + `ValidationBackendError` structured tool errors,
#     `CANONICAL_VALIDATE_KEYS` constant (VF-020 inv-5 STABLE-KEYS oracle),
#     `_canonicalize_report` (testable internal — ValidationReport →
#     canonical dict).
#   DEPENDS: fastmcp (Context + ToolError; server reused from
#     mint_python.mcp.document — shared instance pattern, see
#     mcp/manifest.py:72), mint_python.validate.validate +
#     ValidationReport + SeverityMode + InvalidDocumentError,
#     mint_python.rules.Violation (canonical violation dict shape),
#     mint._security.safe_doc (path-traversal guard fired BEFORE any
#     zipfile open — VF-020 inv-4 PATH-TRAVERSAL-PRE-ZIP).
#   LINKS: docs/development-plan.xml#MP-MCP-VALIDATE,
#     docs/verification-plan.xml#V-MP-MCP-VALIDATE,
#     docs/verification-plan.xml#VF-020,
#     docs/knowledge-graph.xml#MP-MCP-VALIDATE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   InvalidDocument               - structured ToolError; INVALID_DOCUMENT
#                                   (path traversal / non-zip / missing /
#                                   unsupported extension)
#   ValidationBackendError        - structured ToolError; VALIDATION_BACKEND
#                                   _ERROR (rules dir missing OR lxml
#                                   parse error / KeyError surfaced from
#                                   mint_python.validate before evaluation)
#   CANONICAL_VALIDATE_KEYS       - frozenset of the 5 keys always present
#                                   in the returned dict (VF-020 inv-5)
#   _canonicalize_violation       - Violation dataclass → canonical 5-key
#                                   sub-dict (rule_id, severity, message,
#                                   hint, location)
#   _canonicalize_report          - ValidationReport dataclass → canonical
#                                   dict with stable key order
#   _resolve_severity_mode        - Literal['audit','lenient','strict']
#                                   → SeverityMode enum; rejects unknown
#   mint_validate_document        - @server.tool async fn; production
#                                   entry registered on shared FastMCP
#                                   `server`
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-16 Wave-16-1 (MP-MCP-VALIDATE). Initial
#     module. Wraps the pure-python validate() pipeline; canonical dict
#     shape is the contract surface external clients depend on, so key
#     set + order is pinned via CANONICAL_VALIDATE_KEYS for VF-020 inv-5.
#     Errors inherit fastmcp.exceptions.ToolError so FastMCP surfaces
#     them to the client without traceback bleed (VF-020 forbidden-4).
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from typing import Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError

from mint._security import safe_doc
from mint_python.mcp.document import server
from mint_python.rules import Violation
from mint_python.validate import (
    InvalidDocumentError,
    SeverityMode,
    ValidationReport,
)
from mint_python.validate import (
    validate as _backend_validate,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Canonical dict shape — pinned for VF-020 inv-5 CANONICAL-DICT-STABLE-KEYS.
# External MCP clients depend on this exact key set; any change is a contract
# break and must update CANONICAL_VALIDATE_KEYS plus the integration tests.
# --------------------------------------------------------------------------- #


CANONICAL_VALIDATE_KEYS: frozenset[str] = frozenset(
    {
        "passed",
        "severity_mode",
        "violations",
        "counts",
        "format",
    }
)


_VIOLATION_KEYS: tuple[str, ...] = (
    "rule_id",
    "severity",
    "message",
    "hint",
    "location",
)


# --------------------------------------------------------------------------- #
# Errors — surfaced via fastmcp.exceptions.ToolError so FastMCP wraps them
# into a structured MCP error response without leaking a Python traceback
# (VF-020 forbidden-4 / inv-6 STRUCTURED-ERRORS-NO-TRACEBACK).
# --------------------------------------------------------------------------- #


class InvalidDocument(ToolError):  # noqa: N818 — code INVALID_DOCUMENT mirrors class name
    """Path traversal / non-zip / missing file / unsupported extension."""


class ValidationBackendError(ToolError):
    """Backend rule-loading or XML-parse error before rule evaluation."""


# --------------------------------------------------------------------------- #
# Canonicalizers
# --------------------------------------------------------------------------- #


def _canonicalize_violation(v: Violation) -> dict[str, Any]:
    """Project a Violation dataclass into its canonical 5-key sub-dict.

    The MCP boundary returns JSON-serializable values; Severity is a
    StrEnum so `str(v.severity)` yields its `.value` ("hard" / "soft").
    location is preserved as-is (empty string when the rule didn't carry
    one — mirrors Violation's default)."""
    return {
        "rule_id": v.rule_id,
        "severity": str(v.severity),
        "message": v.message,
        "hint": v.hint,
        "location": v.location,
    }


def _canonicalize_report(
    report: ValidationReport, severity_mode: SeverityMode
) -> dict[str, Any]:
    """ValidationReport dataclass → canonical dict (stable key order).

    The returned dict is constructed key-by-key in the order documented
    by CANONICAL_VALIDATE_KEYS so JSON-line readers downstream see a
    deterministic shape. severity_mode is echoed from the input
    parameter rather than the report (cheaper to read, identical value
    on the success path; report.mode is a stringified enum, and we want
    the bare 'audit' / 'lenient' / 'strict' literal).
    """
    return {
        "passed": report.passed,
        "severity_mode": str(severity_mode),
        "violations": [_canonicalize_violation(v) for v in report.violations],
        "counts": {
            "hard": report.hard_count,
            "soft": report.soft_count,
            "total": report.total,
        },
        "format": report.document_format,
    }


# --------------------------------------------------------------------------- #
# Severity mode parser
# --------------------------------------------------------------------------- #


_SEVERITY_MODE_MAP: dict[str, SeverityMode] = {
    "audit": SeverityMode.AUDIT,
    "lenient": SeverityMode.LENIENT,
    "strict": SeverityMode.STRICT,
}


def _resolve_severity_mode(raw: str) -> SeverityMode:
    """Map a wire-string severity_mode to the typed enum.

    The MCP tool signature pins severity_mode as Literal['audit', 'lenient',
    'strict'] so a well-behaved client never sends anything else, but we
    validate defensively — connected models occasionally emit casing
    drift and we'd rather surface INVALID_DOCUMENT-style structured
    errors than blow up inside the backend.
    """
    normalized = raw.lower() if isinstance(raw, str) else raw
    if normalized not in _SEVERITY_MODE_MAP:
        raise InvalidDocument(
            f"INVALID_DOCUMENT: unsupported severity_mode={raw!r}; "
            f"expected one of audit / lenient / strict"
        )
    return _SEVERITY_MODE_MAP[normalized]


# --------------------------------------------------------------------------- #
# Public tool — mint_validate_document
# --------------------------------------------------------------------------- #


@server.tool(name="mint_validate_document")
async def mint_validate_document(
    document_path: str,
    severity_mode: Literal["audit", "lenient", "strict"] = "lenient",
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Validate a caller-supplied .docx / .pptx against MINT's rule set.

    Returns a canonical dict with keys exactly matching
    CANONICAL_VALIDATE_KEYS: `passed` (bool), `severity_mode` (echoed
    literal), `violations` (list of 5-key sub-dicts), `counts` ({hard,
    soft, total}), `format` ("docx" / "pptx" / empty when XML didn't
    parse). Read-only by contract — every zipfile open inside the
    backend uses mode='r'.

    Raises:
        InvalidDocument: path traversal rejected by safe_doc, file is
            missing, or extension isn't .docx / .pptx.
        ValidationBackendError: rules dir missing or lxml parse error
            before any rule evaluation (carries the failing rule_id or
            'XML-001').
    """
    del ctx  # reserved for future progress reporting (e.g. per-rule progress)

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

    if not resolved.is_file():
        raise InvalidDocument(
            f"INVALID_DOCUMENT: not a regular file "
            f"document_path={document_path!r}"
        )

    mode = _resolve_severity_mode(severity_mode)

    # ---- Delegate to the backend ------------------------------------------
    # mint_python.validate.validate handles InvalidDocumentError internally
    # for the BadZipFile / missing-internal-XML cases by returning a report
    # with an XML-001 violation. That's the desired behavior here too —
    # scenario-2 (broken styles.xml) is expected to return passed=False with
    # violations, NOT raise. So we only treat raw exceptions from the
    # backend (rules-dir disappeared, OSError on read) as
    # VALIDATION_BACKEND_ERROR.
    try:
        report = _backend_validate(resolved, severity_mode=mode)
    except InvalidDocumentError as exc:  # pragma: no cover — backend catches internally
        # Defense-in-depth: the backend currently catches its own
        # InvalidDocumentError and surfaces an XML-001 violation. If a
        # future refactor lets it bubble, route to ValidationBackendError
        # so the MCP boundary stays consistent.
        raise ValidationBackendError(
            f"VALIDATION_BACKEND_ERROR: backend raised on parse "
            f"document_path={document_path!r}: {exc}"
        ) from exc
    except FileNotFoundError as exc:
        # Rules dir disappeared between checks (rare; race against a
        # repo cleanup tool). Surface as backend error rather than
        # invalid-doc — the doc was fine, the validator infrastructure
        # broke.
        raise ValidationBackendError(
            f"VALIDATION_BACKEND_ERROR: rules dir missing: {exc}"
        ) from exc

    canonical = _canonicalize_report(report, mode)

    # START_BLOCK_VALIDATE_DONE
    logger.info(
        "[MP-McpValidate][run][BLOCK_VALIDATE_DONE] "
        "severity_mode=%s hard_count=%d soft_count=%d passed=%s",
        canonical["severity_mode"],
        report.hard_count,
        report.soft_count,
        report.passed,
    )
    # END_BLOCK_VALIDATE_DONE

    return canonical


__all__ = [
    "CANONICAL_VALIDATE_KEYS",
    "InvalidDocument",
    "ValidationBackendError",
    "mint_validate_document",
]
