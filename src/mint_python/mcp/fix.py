# FILE: src/mint_python/mcp/fix.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-1 (MP-MCP-FIX) — thin MCP wrap over the
#     existing pure-python `mint_python.fix.apply_fixes` (entry point of
#     `mint_python.fix.fix`). Exposes the `mint_fix_document` MCP tool so
#     connected models can request safe + visual auto-fixes on a docx the
#     caller already owns. Closes the MCP-parity gap left by Phase-15:
#     validate is reachable via MCP, but the symmetric "now repair what
#     you found" surface only existed in-process. Mutates the caller-
#     supplied path in place, preserving the backup-before-write
#     invariant (a `.bak` lands beside the original BEFORE any rewrite).
#   SCOPE: Public surface = `mint_fix_document` (FastMCP tool),
#     FixDocumentError + 4 structured error subclasses
#     (InvalidDocument / DestructiveRejected / BackupFailed /
#     CascadeDetected), `CANONICAL_FIX_KEYS` (the 6-key tuple of the
#     canonical FixReport dict shape returned to the connected model),
#     and `_canonicalize_report` (testable internal mapping
#     mint_python.fix.FixReport -> canonical dict).
#   DEPENDS: fastmcp (Context, FastMCP server reused from
#     mint_python.mcp.document), mint_python.fix (apply_fixes +
#     create_backup + the four domain exceptions
#     BackupFailedError / CascadeDetectedError /
#     DestructiveRejectedError / FixError plus the FixReport dataclass),
#     mint_python.validate (run_checks + SeverityMode for the
#     audit/lenient/strict surface), mint._security.safe_doc (path
#     traversal guard fires before any zipfile open).
#   LINKS: docs/development-plan.xml#MP-MCP-FIX,
#     docs/verification-plan.xml#V-MP-MCP-FIX,
#     docs/verification-plan.xml#VF-020,
#     docs/knowledge-graph.xml#MP-MCP-FIX
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FixDocumentError              - base error for the tool
#   InvalidDocument               - INVALID_DOCUMENT (bad zip / traversal /
#                                   not a regular file)
#   DestructiveRejected           - DESTRUCTIVE_REJECTED (one or more
#                                   violations classified destructive —
#                                   short-circuit BEFORE backup attempt)
#   BackupFailed                  - BACKUP_FAILED (cannot write .bak)
#   CascadeDetected               - CASCADE_DETECTED (fixes not converging
#                                   after max_iterations)
#   CANONICAL_FIX_KEYS            - tuple of the 6 keys in the canonical
#                                   dict shape (returned dict is exactly
#                                   these keys, no more)
#   _SEVERITY_MAP                 - Literal-string → SeverityMode bridge
#                                   so the MCP-facing API stays str-typed
#   _canonicalize_report          - FixReport -> canonical dict
#   mint_fix_document             - @server.tool async fn; the production
#                                   entry registered on the shared
#                                   FastMCP `server`
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-16 Wave-16-1 (MP-MCP-FIX). Initial module.
#     Mirrors the MP-MANIFEST-READ INTEGRATION pattern: safe_doc guard
#     before any I/O → pure-python core (run_checks + apply_fixes from
#     mint_python.fix) → canonical dict projection → structured tool
#     errors with code-style prefixes. Calls apply_fixes directly (not
#     the convenience wrapper mint_python.fix.fix) so the caller's
#     max_iterations parameter is honored — mint_python.fix.fix does not
#     surface max_iterations on its signature (see VF-020 + handover
#     notes for Phase-16 W1). NO MP-AUTH-SHIM call site here: the doc
#     under fix is caller-owned (not a template / preset), so the
#     V-MP-AUTH-SHIM forbidden-1 extension explicitly forbids the
#     require_template_writer dispatch.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import zipfile
from typing import Any, Literal

from fastmcp import Context

from mint._security import safe_doc
from mint_python.fix import (
    BackupFailedError,
    CascadeDetectedError,
    DestructiveRejectedError,
    FixReport,
    apply_fixes,
)
from mint_python.mcp.document import server
from mint_python.mcp.telemetry import track_call
from mint_python.validate import SeverityMode, run_checks

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-McpFix"

SeverityLiteral = Literal["audit", "lenient", "strict"]

CANONICAL_FIX_KEYS: tuple[str, ...] = (
    "applied_fixes",
    "remaining_violations",
    "backup_path",
    "iterations",
    "diff_summary",
    "severity_mode",
)


# --------------------------------------------------------------------------- #
# Errors — structured tool errors, surfaced to the MCP client without leaking
# Python tracebacks (VF-020 forbidden-4). Each carries a code-style message
# prefix so connected models can route on the prefix without parsing prose.
# --------------------------------------------------------------------------- #


class FixDocumentError(Exception):
    """Base for mint_fix_document tool errors."""


class InvalidDocument(FixDocumentError):  # noqa: N818 — error code INVALID_DOCUMENT mirrors class name
    """Path is not a valid .docx zip / path traversal rejected / missing."""


class DestructiveRejected(FixDocumentError):  # noqa: N818 — error code DESTRUCTIVE_REJECTED mirrors class name
    """Caller's document carries violations classified destructive — no fix."""


class BackupFailed(FixDocumentError):  # noqa: N818 — error code BACKUP_FAILED mirrors class name
    """Could not write the .bak file (e.g. parent dir not writable)."""


class CascadeDetected(FixDocumentError):  # noqa: N818 — error code CASCADE_DETECTED mirrors class name
    """Fix loop failed to converge after max_iterations passes."""


# --------------------------------------------------------------------------- #
# Severity bridge — keep the MCP-facing surface str-typed (per the
# development-plan.xml contract `severity_mode: Literal['audit','lenient',
# 'strict']`) while the pure-python core expects the SeverityMode StrEnum.
# Build the map up-front rather than calling SeverityMode(value) at the call
# site so unknown values surface as a domain-shaped error instead of
# ValueError("'foo' is not a valid SeverityMode").
# --------------------------------------------------------------------------- #


_SEVERITY_MAP: dict[str, SeverityMode] = {
    "audit": SeverityMode.AUDIT,
    "lenient": SeverityMode.LENIENT,
    "strict": SeverityMode.STRICT,
}


# --------------------------------------------------------------------------- #
# Canonicalizer
# --------------------------------------------------------------------------- #


def _canonicalize_report(
    report: FixReport,
    severity_mode: SeverityLiteral,
) -> dict[str, Any]:
    """Project a mint_python.fix.FixReport into the canonical 6-key dict.

    The pure-python FixReport carries a `diff: str` field ("file modified"
    or "no changes") and a `remaining_violations: list[Violation]` of
    dataclass instances. Neither is directly JSON-serializable for the MCP
    surface, so we re-key to `diff_summary` and project each Violation into
    a small dict carrying the four fields a connected model actually needs
    (rule_id / severity / fix_category / message). `backup_path` becomes a
    string (None if create_backup was somehow skipped — though apply_fixes
    always returns a non-None backup_path on the non-destructive path).

    Echoing `severity_mode` back on the canonical dict lets the caller
    verify which strictness level the report was produced under without
    a second round-trip.
    """
    remaining: list[dict[str, str]] = []
    for v in report.remaining_violations:
        remaining.append(
            {
                "rule_id": v.rule_id,
                "severity": str(v.severity.value),
                "fix_category": str(v.fix_category.value),
                "message": v.message,
            }
        )

    return {
        "applied_fixes": list(report.applied_fixes),
        "remaining_violations": remaining,
        "backup_path": str(report.backup_path) if report.backup_path else None,
        "iterations": report.iterations,
        "diff_summary": report.diff,
        "severity_mode": severity_mode,
    }


# --------------------------------------------------------------------------- #
# Public tool — mint_fix_document
# --------------------------------------------------------------------------- #


@server.tool(name="mint_fix_document")
async def mint_fix_document(
    document_path: str,
    severity_mode: SeverityLiteral = "lenient",
    max_iterations: int = 3,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Apply safe and visual auto-fixes to a caller-owned .docx.

    The document at `document_path` is mutated in place. A `.bak` lands
    beside the original BEFORE any rewrite (VF-020 inv-3 BACKUP-BEFORE-
    WRITE). Destructive violations short-circuit BEFORE the backup is
    attempted, leaving the source bytes untouched.

    Returns a dict with the 6 canonical keys
    (applied_fixes, remaining_violations, backup_path, iterations,
    diff_summary, severity_mode). `applied_fixes` is the list of rule_ids
    that were repaired (in iteration order); `remaining_violations` is a
    list of small dicts describing whatever the final re-validation pass
    still found.

    Args:
        document_path: Filesystem path to the .docx the caller owns.
        severity_mode: Strictness for the initial validation pass —
            'audit' / 'lenient' / 'strict'. Defaults to 'lenient'.
        max_iterations: Cap on the fix-then-revalidate loop. Defaults to 3.
        ctx: FastMCP context (reserved; not currently consumed).

    Raises:
        InvalidDocument: path is not a valid .docx zip, the path
            traversal guard rejected it, or it isn't a regular file.
        DestructiveRejected: at least one violation is fix_category=
            DESTRUCTIVE; source bytes UNCHANGED.
        BackupFailed: the .bak file could not be written; source bytes
            UNCHANGED (apply_fixes raises before any rewrite).
        CascadeDetected: the fix loop didn't converge after
            `max_iterations` passes.
    """
    del ctx  # reserved for future progress reporting

    with track_call("mint_fix_document"):
        if severity_mode not in _SEVERITY_MAP:
            raise InvalidDocument(
                f"INVALID_DOCUMENT: unknown severity_mode "
                f"severity_mode={severity_mode!r} "
                f"(expected one of {sorted(_SEVERITY_MAP)!r})"
            )

        # Path traversal guard — fires BEFORE any zipfile open
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

        if not zipfile.is_zipfile(resolved):
            raise InvalidDocument(
                f"INVALID_DOCUMENT: not a valid zip archive "
                f"document_path={document_path!r}"
            )

        mode = _SEVERITY_MAP[severity_mode]

        try:
            validation = run_checks(resolved, mode)
        except Exception as exc:
            raise InvalidDocument(
                f"INVALID_DOCUMENT: validation pre-pass failed "
                f"document_path={document_path!r}: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            report = apply_fixes(
                resolved,
                validation.violations,
                max_iterations=max_iterations,
            )
        except DestructiveRejectedError as exc:
            # START_BLOCK_DESTRUCTIVE_REJECTED
            logger.info(
                "[%s][apply][BLOCK_DESTRUCTIVE_REJECTED] document_path=%s",
                _LOG_PREFIX,
                str(resolved),
            )
            # END_BLOCK_DESTRUCTIVE_REJECTED
            raise DestructiveRejected(
                f"DESTRUCTIVE_REJECTED: refusing to apply destructive fixes "
                f"document_path={document_path!r}: {exc}"
            ) from exc
        except BackupFailedError as exc:
            # START_BLOCK_BACKUP_FAILED
            logger.info(
                "[%s][apply][BLOCK_BACKUP_FAILED] document_path=%s",
                _LOG_PREFIX,
                str(resolved),
            )
            # END_BLOCK_BACKUP_FAILED
            raise BackupFailed(
                f"BACKUP_FAILED: could not write .bak beside document "
                f"document_path={document_path!r}: {exc}"
            ) from exc
        except CascadeDetectedError as exc:
            # START_BLOCK_CASCADE_DETECTED
            logger.info(
                "[%s][apply][BLOCK_CASCADE_DETECTED] document_path=%s max_iterations=%d",
                _LOG_PREFIX,
                str(resolved),
                max_iterations,
            )
            # END_BLOCK_CASCADE_DETECTED
            raise CascadeDetected(
                f"CASCADE_DETECTED: fix loop did not converge after "
                f"max_iterations={max_iterations} document_path={document_path!r}: {exc}"
            ) from exc

        canonical = _canonicalize_report(report, severity_mode)

        # START_BLOCK_FIX_DONE
        logger.info(
            "[%s][apply][BLOCK_FIX_DONE] "
            "iterations=%d applied_count=%d remaining_violations=%d backup_path=%s",
            _LOG_PREFIX,
            report.iterations,
            len(report.applied_fixes),
            len(report.remaining_violations),
            str(report.backup_path) if report.backup_path else "",
        )
        # END_BLOCK_FIX_DONE

        return canonical


__all__ = [
    "CANONICAL_FIX_KEYS",
    "BackupFailed",
    "CascadeDetected",
    "DestructiveRejected",
    "FixDocumentError",
    "InvalidDocument",
    "SeverityLiteral",
    "mint_fix_document",
]
