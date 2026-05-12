# FILE: src/mint_python/mcp/edit.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-3c (MP-MCP-EDIT) — thin MCP wrap over the
#     pure-python edit pipeline at `mint_python.edit.edit`. Exposes the
#     `mint_edit_document` MCP tool so connected models can apply a typed
#     EditPlan (JSON dict) against a caller-owned .docx. Closes the
#     final Phase-16 surface gap: MP-EDIT exists in-process via the W3b
#     port, but external clients need a governed wire-shape +
#     wrap-layer raw-OOXML rejection (UC-008 acceptance at the MCP
#     boundary). The doc is caller-owned (not a template / preset), so
#     V-MP-AUTH-SHIM forbidden-1 extension forbids MP-AUTH-SHIM dispatch
#     (VF-021 inv-5 NO-AUTH-CALL).
#   SCOPE: Public surface = `mint_edit_document` (FastMCP tool),
#     `CANONICAL_EDIT_RESULT_KEYS` (the 10-key oracle for VF-021 inv-6
#     STABLE-KEYS), and 8 structured ToolError subclasses (one per
#     EditError.code + INVALID_DOCUMENT). Internal helpers
#     `_reject_raw_ooxml_in_plan`, `_canonicalize_edit_result`,
#     `_canonicalize_op_outcome`, `_remap_edit_error` are exposed for
#     direct unit-coverage so the integration suite can drive the
#     branches without round-tripping a docx.
#   DEPENDS: fastmcp (Context + ToolError; server reused from
#     mint_python.mcp.document — shared instance pattern), mint_python.edit
#     (W3b port; full public surface — edit, edit_plan_from_dict, EditError,
#     EditPlan, EditResult, OpOutcome), mint_python.validate.SeverityMode,
#     mint_python.mcp.validate._canonicalize_report (reused so the
#     `validation_report` projection on the canonical dict matches the
#     V-MP-MCP-VALIDATE wire-shape verbatim — no shape drift between
#     `mint_validate_document` and the validation_report field of
#     `mint_edit_document`), mint._security.safe_doc (path traversal
#     guard fired BEFORE any zipfile open).
#   LINKS: docs/development-plan.xml#MP-MCP-EDIT,
#     docs/verification-plan.xml#V-MP-MCP-EDIT,
#     docs/verification-plan.xml#VF-021,
#     docs/knowledge-graph.xml#MP-MCP-EDIT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   InvalidDocument               - structured ToolError; INVALID_DOCUMENT
#                                   (traversal / not-a-zip / missing / unknown
#                                   severity_mode)
#   EditPlanInvalid               - EDIT_PLAN_INVALID (from EditError OR from
#                                   the wrap-layer raw-OOXML rejection)
#   EditOpUnsupported             - EDIT_OP_UNSUPPORTED (pptx, unknown op type)
#   EditAnchorNotFound            - EDIT_ANCHOR_NOT_FOUND (carries op_id when
#                                   mid-pipeline)
#   EditAnchorAmbiguous           - EDIT_ANCHOR_AMBIGUOUS
#   EditValidationFailed          - EDIT_VALIDATION_FAILED (strict mode regress)
#   EditBackupFailed              - EDIT_BACKUP_FAILED (REMAPPED from MP-EDIT's
#                                   BACKUP_FAILED code — wrap-layer rename so
#                                   the MCP surface speaks an EDIT_-prefixed
#                                   vocabulary that's easy for clients to
#                                   route on)
#   EditTrackedChangeInvalid      - EDIT_TRACKED_CHANGE_INVALID
#   EditUnknown                   - EDIT_UNKNOWN (catch-all for new
#                                   EditError.code values introduced by a
#                                   future MP-EDIT refactor without breaking
#                                   the wrap)
#   CANONICAL_EDIT_RESULT_KEYS    - the 10-key tuple of the canonical
#                                   EditResult dict (VF-021 inv-6 oracle)
#   _RAW_OOXML_NEEDLES            - tuple of case-insensitive substrings that
#                                   identify raw OOXML run / paragraph / text
#                                   markup leaking into anchor.value
#                                   (wrap-layer hardening for UC-008 +
#                                   VF-021 inv-3 NO-RAW-OOXML-IN-PLAN)
#   _SEVERITY_MAP                 - Literal severity → SeverityMode bridge
#   _EDIT_ERROR_MAP               - EditError.code → ToolError subclass +
#                                   message-prefix (preserves vocabulary
#                                   except BACKUP_FAILED → EDIT_BACKUP_FAILED)
#   _reject_raw_ooxml_in_plan     - walk plan_json ops + raise EditPlanInvalid
#                                   when any anchor.value carries OOXML
#                                   markup substrings
#   _canonicalize_op_outcome      - OpOutcome dataclass → 6-key sub-dict
#   _canonicalize_edit_result     - EditResult dataclass → canonical 10-key
#                                   dict (output_path / backup_path
#                                   stringified; validation_report projected
#                                   through MP-MCP-VALIDATE's canonicalizer
#                                   or surfaced as None on mid-pipeline
#                                   failure)
#   _remap_edit_error             - EditError → corresponding ToolError
#                                   subclass with carried op_id (when known)
#   mint_edit_document            - @server.tool async fn; production entry
#                                   registered on the shared FastMCP server
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-16 Wave-16-3c (MP-MCP-EDIT). Initial module.
#     Mirrors the MP-MCP-FIX write-mutation pattern: safe_doc guard +
#     zipfile-is_zipfile guard BEFORE backup; structured ToolError subclasses
#     so FastMCP surfaces them without traceback bleed (VF-021 forbidden-3).
#     Wrap-layer hardening: anchor.value substring rejection of `<w:r`, `<w:p`,
#     `<w:t`, `</w:` BEFORE edit_plan_from_dict, which the W3b port deferred
#     to this layer per the development-plan UC-008 acceptance at the MCP
#     boundary. EDIT_BACKUP_FAILED remap of MP-EDIT's BACKUP_FAILED code so
#     the MCP-facing vocabulary stays EDIT_-prefixed end-to-end.
#     validation_report uses the V-MP-MCP-VALIDATE canonicalizer (single
#     source of truth for the validation wire-shape). NO MP-AUTH-SHIM call
#     site — V-MP-AUTH-SHIM forbidden-1 extension.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import zipfile
from typing import Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError

from mint._security import safe_doc
from mint_python.edit import (
    EditError,
    EditResult,
    OpOutcome,
    edit_plan_from_dict,
)
from mint_python.edit import (
    edit as _backend_edit,
)
from mint_python.mcp.document import server
from mint_python.mcp.telemetry import track_call
from mint_python.validate import SeverityMode

# _canonicalize_report is imported lazily inside _canonicalize_edit_result to
# avoid a circular import: validate.py imports server from document.py, and
# document.py tail-imports edit (us). Importing validate at module-load time
# would create a cycle when `import mint_python.mcp.validate` is the first
# trigger (the module-init order leaves _canonicalize_report undefined until
# validate.py finishes its own module-level work). Lazy import deferred to
# first call avoids the cycle entirely. See Gate-Phase-16 W4 finding.

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-McpEdit"

SeverityLiteral = Literal["audit", "lenient", "strict"]


# --------------------------------------------------------------------------- #
# Canonical dict shape — pinned for VF-021 inv-6 CANONICAL-EDIT-RESULT-KEYS.
# Order mirrors the EditResult dataclass field declaration order in
# mint_python.edit so the wire-shape is the dataclass shape, period.
# --------------------------------------------------------------------------- #


CANONICAL_EDIT_RESULT_KEYS: tuple[str, ...] = (
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


_OP_OUTCOME_KEYS: tuple[str, ...] = (
    "op_id",
    "success",
    "error_code",
    "affected_part",
    "before_snippet",
    "after_snippet",
)


# --------------------------------------------------------------------------- #
# Structured tool errors — every error_code the MCP surface emits has a
# dedicated subclass so connected models can route on the class name (or the
# code-style prefix at the start of the message string) without parsing prose.
# All inherit fastmcp.exceptions.ToolError so FastMCP wraps them into a
# structured MCP error response without leaking a Python traceback
# (VF-021 forbidden-3 / inv-7 STRUCTURED-ERRORS-NO-TRACEBACK).
# --------------------------------------------------------------------------- #


class InvalidDocument(ToolError):  # noqa: N818 — code INVALID_DOCUMENT mirrors class name
    """Path traversal / not-a-zip / missing file / unknown severity_mode."""


class EditPlanInvalid(ToolError):  # noqa: N818 — error code mirrors class name
    """EditPlan shape is invalid OR carried raw OOXML in anchor.value."""


class EditOpUnsupported(ToolError):  # noqa: N818 — error code mirrors class name
    """Op type not in SUPPORTED_OP_TYPES or pptx format requested."""


class EditAnchorNotFound(ToolError):  # noqa: N818 — error code mirrors class name
    """Anchor did not resolve to a paragraph in the live tree."""


class EditAnchorAmbiguous(ToolError):  # noqa: N818 — error code mirrors class name
    """Anchor matched multiple paragraphs and disambiguation failed."""


class EditValidationFailed(ToolError):  # noqa: N818 — error code mirrors class name
    """Strict-mode MP-VALIDATE regression on the output document."""


class EditBackupFailed(ToolError):  # noqa: N818 — error code mirrors class name
    """Could not write the .bak file (e.g. parent dir not writable).

    Remapped from MP-EDIT's BACKUP_FAILED code so the MCP-facing
    vocabulary is uniformly EDIT_-prefixed.
    """


class EditTrackedChangeInvalid(ToolError):  # noqa: N818 — error code mirrors class name
    """Tracked-change op payload was malformed (bad change_id, etc.)."""


class EditUnknown(ToolError):  # noqa: N818 — error code mirrors class name
    """Catch-all for EditError.code values the wrap doesn't recognize."""


# --------------------------------------------------------------------------- #
# Wrap-layer raw-OOXML rejection (UC-008 acceptance + VF-021 inv-3).
# The W3b port preserved legacy behavior in validate_plan (control-char +
# oversize + empty rejection only). The literal-OOXML-substring guard is
# pulled UP to the wrap layer so the MCP boundary explicitly rejects raw
# OOXML before edit_plan_from_dict touches the dict.
# --------------------------------------------------------------------------- #


# Substrings chosen for unambiguity in document text — `<w:r`, `<w:p`, `<w:t`,
# `</w:` are OOXML run / paragraph / text / closing-namespaced-tag markers that
# don't naturally appear in human-authored prose. Case-insensitive because a
# producer might canonicalize differently than lxml's serializer.
_RAW_OOXML_NEEDLES: tuple[str, ...] = (
    "<w:r",
    "<w:p",
    "<w:t",
    "</w:",
)


def _reject_raw_ooxml_in_plan(plan_json: dict[str, Any]) -> None:
    """Walk plan_json['ops'][*]['anchor']['value'] and raise EditPlanInvalid
    when any value carries an OOXML substring (case-insensitive).

    Defensive against missing / non-dict shapes — those are
    edit_plan_from_dict's job to surface, not ours. We only inspect the
    well-shaped path; anything else falls through silently to the
    downstream validator.
    """
    ops = plan_json.get("ops")
    if not isinstance(ops, list):
        return
    for op in ops:
        if not isinstance(op, dict):
            continue
        anchor = op.get("anchor")
        if not isinstance(anchor, dict):
            continue
        value = anchor.get("value")
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        for needle in _RAW_OOXML_NEEDLES:
            if needle in lowered:
                op_id = op.get("op_id", "<unknown>")
                raise EditPlanInvalid(
                    f"EDIT_PLAN_INVALID: anchor.value carries raw OOXML markup "
                    f"op_id={op_id!r} needle={needle!r} "
                    f"(UC-008: MCP boundary rejects raw OOXML in EditPlan)"
                )


# --------------------------------------------------------------------------- #
# Severity bridge
# --------------------------------------------------------------------------- #


_SEVERITY_MAP: dict[str, SeverityMode] = {
    "audit": SeverityMode.AUDIT,
    "lenient": SeverityMode.LENIENT,
    "strict": SeverityMode.STRICT,
}


# --------------------------------------------------------------------------- #
# EditError.code → ToolError subclass mapping. The wrap-layer rename of
# BACKUP_FAILED → EDIT_BACKUP_FAILED happens here so every other code is
# preserved verbatim.
# --------------------------------------------------------------------------- #


_EDIT_ERROR_MAP: dict[str, tuple[type[ToolError], str]] = {
    "EDIT_PLAN_INVALID": (EditPlanInvalid, "EDIT_PLAN_INVALID"),
    "EDIT_OP_UNSUPPORTED": (EditOpUnsupported, "EDIT_OP_UNSUPPORTED"),
    "EDIT_ANCHOR_NOT_FOUND": (EditAnchorNotFound, "EDIT_ANCHOR_NOT_FOUND"),
    "EDIT_ANCHOR_AMBIGUOUS": (EditAnchorAmbiguous, "EDIT_ANCHOR_AMBIGUOUS"),
    "EDIT_VALIDATION_FAILED": (EditValidationFailed, "EDIT_VALIDATION_FAILED"),
    "EDIT_TRACKED_CHANGE_INVALID": (
        EditTrackedChangeInvalid,
        "EDIT_TRACKED_CHANGE_INVALID",
    ),
    # BACKUP_FAILED is the legacy error code preserved verbatim by the W3b
    # MP-EDIT port (legacy mint package + mint_python tree both raise it). The
    # MCP surface speaks an EDIT_-prefixed vocabulary, so we rename to
    # EDIT_BACKUP_FAILED at the boundary. Documented in MODULE_MAP.
    "BACKUP_FAILED": (EditBackupFailed, "EDIT_BACKUP_FAILED"),
}


def _remap_edit_error(
    exc: EditError, *, document_path: str, op_id: str | None = None
) -> ToolError:
    """Build the matching ToolError subclass for an EditError.

    The MP-EDIT contract pins exc.code to one of the known constants. A new
    code introduced by a future MP-EDIT refactor without a matching wrap
    update falls through to EditUnknown — this keeps the wrap forward-
    compatible (no traceback bleed even on the unknown branch).
    """
    cls, prefix = _EDIT_ERROR_MAP.get(exc.code, (EditUnknown, "EDIT_UNKNOWN"))
    suffix = f" op_id={op_id!r}" if op_id is not None else ""
    return cls(
        f"{prefix}: {exc}{suffix} document_path={document_path!r}"
    )


# --------------------------------------------------------------------------- #
# Canonicalizers — OpOutcome and EditResult → JSON-friendly dicts.
# --------------------------------------------------------------------------- #


def _canonicalize_op_outcome(outcome: OpOutcome) -> dict[str, Any]:
    """Project an OpOutcome dataclass into its canonical 6-key sub-dict.

    All fields are JSON-safe primitives in MP-EDIT's port; we keep the
    `error_code` field as-is (it's None on success, a string code on
    failure). before_snippet / after_snippet are str (already truncated to
    SNIPPET_MAX_LEN by MP-EDIT)."""
    return {
        "op_id": outcome.op_id,
        "success": outcome.success,
        "error_code": outcome.error_code,
        "affected_part": outcome.affected_part,
        "before_snippet": outcome.before_snippet,
        "after_snippet": outcome.after_snippet,
    }


def _canonicalize_edit_result(
    result: EditResult, severity_mode: SeverityLiteral
) -> dict[str, Any]:
    """EditResult dataclass → canonical 10-key dict (stable key order).

    validation_report is projected through MP-MCP-VALIDATE's
    `_canonicalize_report` so the wire-shape on the validation_report field
    matches `mint_validate_document` byte-for-byte (single source of truth
    for the validation dict shape). On mid-pipeline op failure MP-EDIT
    returns EditResult.validation_report=None and EditResult.output_path=
    None; we surface both as None on the canonical dict.
    """
    if result.validation_report is None:
        validation_dict: dict[str, Any] | None = None
    else:
        # Lazy import to break the circular dependency documented at the top
        # of this module — validate <-> document <-> edit.
        from mint_python.mcp.validate import _canonicalize_report

        validation_dict = _canonicalize_report(
            result.validation_report, _SEVERITY_MAP[severity_mode]
        )

    return {
        "output_path": str(result.output_path) if result.output_path else None,
        "backup_path": str(result.backup_path),
        "success": result.success,
        "ops_total": result.ops_total,
        "ops_succeeded": result.ops_succeeded,
        "ops_failed": result.ops_failed,
        "validation_report": validation_dict,
        "diff": [_canonicalize_op_outcome(o) for o in result.diff],
        "duration_ms": result.duration_ms,
        "error": result.error,
    }


# --------------------------------------------------------------------------- #
# Public tool — mint_edit_document
# --------------------------------------------------------------------------- #


@server.tool(name="mint_edit_document")
async def mint_edit_document(
    document_path: str,
    plan_json: dict[str, Any],
    severity_mode: SeverityLiteral = "lenient",
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Apply a typed EditPlan to a caller-owned .docx via the MP-EDIT pipeline.

    The plan is validated (shape + wrap-layer raw-OOXML rejection) BEFORE
    any filesystem touch — so a malformed plan never leaves a `.bak`
    behind (VF-021 inv-1 BACKUP-PRE-MUTATION supplement: pre-validation
    means no backup attempt either). A `.bak` lands beside the original
    BEFORE any tree mutation on the success path (VF-021 inv-2).

    Returns a canonical dict whose keys exactly match
    CANONICAL_EDIT_RESULT_KEYS: output_path, backup_path, success,
    ops_total, ops_succeeded, ops_failed, validation_report (None or
    MP-MCP-VALIDATE canonical dict), diff (list of 6-key OpOutcome
    sub-dicts), duration_ms, error.

    Args:
        document_path: Filesystem path to the .docx the caller owns.
        plan_json: JSON-decoded EditPlan dict. Required schema:
            {
              "format": "docx",
              "ops": [
                {
                  "type": "replace_text" | "insert_paragraph" |
                          "delete_paragraph" | "set_paragraph_style" |
                          "tracked_replace" | "tracked_delete" |
                          "add_comment" | "accept_change" |
                          "reject_change",
                  "op_id": "<unique string>",
                  "anchor": {
                    "type": "paragraph_index" | "text" | "hash",
                    "value": "<int for paragraph_index, str for text/hash>"
                  },
                  ...payload keys depending on op type (e.g. "text",
                  "style_name", "author", "comment_text")
                }
              ],
              "metadata": {}
            }
        severity_mode: Strictness for the post-edit validation pass —
            'audit' / 'lenient' / 'strict'. Defaults to 'lenient'.
        ctx: FastMCP context (reserved; not currently consumed).

    Raises:
        InvalidDocument: path traversal rejected by safe_doc, the file
            is missing / not a zip, or severity_mode is out of domain.
        EditPlanInvalid: plan shape is invalid OR anchor.value carries
            raw OOXML markup (wrap-layer UC-008 hardening).
        EditOpUnsupported: op type unsupported / plan.format='pptx'.
        EditAnchorNotFound: anchor failed to resolve mid-pipeline
            (the message carries the offending op_id).
        EditAnchorAmbiguous: anchor matched multiple paragraphs.
        EditValidationFailed: strict-mode validation regressed the output.
        EditBackupFailed: .bak could not be written (remapped from MP-EDIT
            BACKUP_FAILED).
        EditTrackedChangeInvalid: tracked-change payload malformed.
    """
    del ctx  # reserved for future progress reporting

    with track_call("mint_edit_document"):
        # ---- Severity validation (defensive — Literal pin should suffice) -----
        if severity_mode not in _SEVERITY_MAP:
            raise InvalidDocument(
                f"INVALID_DOCUMENT: unknown severity_mode={severity_mode!r} "
                f"(expected one of {sorted(_SEVERITY_MAP)!r})"
            )

        # ---- Path traversal guard — VF-021 inv-1 PATH-TRAVERSAL-PRE-ZIP -------
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

        # ---- Wrap-layer raw-OOXML rejection (UC-008 / VF-021 inv-3) -----------
        _reject_raw_ooxml_in_plan(plan_json)

        # ---- Plan shape validation — BEFORE any backup attempt ----------------
        try:
            plan = edit_plan_from_dict(plan_json)
        except EditError as exc:
            raise _remap_edit_error(exc, document_path=document_path) from exc

        mode = _SEVERITY_MAP[severity_mode]

        # ---- Delegate to the backend ------------------------------------------
        try:
            result = _backend_edit(
                resolved,
                plan,
                severity_mode=mode,
            )
        except EditError as exc:
            raise _remap_edit_error(exc, document_path=document_path) from exc

        # ---- Mid-pipeline op failure → structured error with op_id ------------
        if not result.success and result.output_path is None and result.diff:
            last = result.diff[-1]
            code = last.error_code or "EDIT_UNKNOWN"
            synth = EditError(
                result.error or "mid-pipeline op failure",
                code=code,
            )
            raise _remap_edit_error(
                synth, document_path=document_path, op_id=last.op_id
            )

        canonical = _canonicalize_edit_result(result, severity_mode)

        # START_BLOCK_EDIT_DONE
        logger.info(
            "[%s][edit][BLOCK_EDIT_DONE] "
            "ops_total=%d ops_succeeded=%d ops_failed=%d duration_ms=%d",
            _LOG_PREFIX,
            result.ops_total,
            result.ops_succeeded,
            result.ops_failed,
            result.duration_ms,
        )
        # END_BLOCK_EDIT_DONE

        return canonical


__all__ = [
    "CANONICAL_EDIT_RESULT_KEYS",
    "EditAnchorAmbiguous",
    "EditAnchorNotFound",
    "EditBackupFailed",
    "EditOpUnsupported",
    "EditPlanInvalid",
    "EditTrackedChangeInvalid",
    "EditUnknown",
    "EditValidationFailed",
    "InvalidDocument",
    "SeverityLiteral",
    "mint_edit_document",
]
