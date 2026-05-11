# FILE: src/mint_python/mcp/memo.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Backwards-compat shim for Phase-13 MEMO-POC. The full pipeline
#     moved to mcp/document.py during Phase-14 W1 (MP-DOC-GENERIC); this
#     module re-exports the memo-named symbols and registers the legacy
#     create_memo tool on the shared FastMCP server. Existing
#     claude_desktop_config.json entries that import
#     `from mint_python.mcp.memo import server` keep working unchanged
#     (server is the same instance document.py creates), and the memo
#     tool keeps emitting [MP-Memo] log markers so V-MP-MEMO-POC trace
#     scenarios stay green.
#   SCOPE: re-exports only — no business logic. Memo* classes alias the
#     Document* counterparts; helpers are partial-applications fixing
#     doc_type="memo".
#   DEPENDS: mint_python.mcp.document (the actual implementation).
#   LINKS: docs/development-plan.xml#MP-DOC-GENERIC,
#     docs/verification-plan.xml#V-MP-DOC-GENERIC,
#     docs/development-plan.xml#MP-MEMO-POC (alias preserved per
#     V-MP-DOC-GENERIC forbidden-2)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 — Phase-14 W1. Pipeline moved to mcp/document.py;
#     this file collapsed into a compat shim. create_memo tool registered
#     on the shared server here so the discoverable tool list at the MCP
#     handshake is unchanged for Claude Desktop sessions.
# END_CHANGE_SUMMARY

from __future__ import annotations

from typing import Any

from fastmcp import Context
from fastmcp.tools.tool import ToolResult

from mint_python.mcp import document as _doc
from mint_python.mcp.document import (
    _DEFAULT_OUTPUT_DIR,
    MEMO_REQUIRED_FIELDS,
    DocumentTypeNotFound,
    _build_document,
    _emit_body_block,
    _heuristic_extract,
    _normalize_body_markdown,
    _render_body,
    _resolve_output_dir,
    _substitute,
    _to_tool_result,
    server,
)
from mint_python.mcp.document import (
    DocumentElicitationRejected as MemoElicitationRejected,
)
from mint_python.mcp.document import (
    DocumentError as MemoError,
)
from mint_python.mcp.document import (
    DocumentGenerationFailed as MemoGenerationFailed,
)
from mint_python.mcp.document import (
    DocumentSpec as MemoSpec,
)
from mint_python.mcp.document import (
    DocumentTemplate as MemoTemplate,
)
from mint_python.mcp.document import (
    DocumentTemplateNotFound as MemoTemplateNotFound,
)

import mint_python.mcp.telemetry as _telemetry  # noqa: F401 — registers mint_version + mint_telemetry tools


def _load_template() -> MemoTemplate:
    """Compat wrapper — loads templates/memo.yaml. The Phase-14 generic
    loader takes a doc_type argument; existing Phase-13 callers expect a
    no-arg call returning the memo template."""
    return _doc._load_template("memo")


def _memo_filename(spec: MemoSpec, audit_id: str) -> str:
    """Compat wrapper — `memo_<date>_<subject>_<short>.docx` filename used
    by the Phase-13 tests' substring asserts."""
    return _doc._document_filename(spec, audit_id, "memo")


async def _run_memo_pipeline(
    intent: str,
    source_md: str | None,
    ctx: Context,
) -> dict[str, Any]:
    """Compat wrapper — invokes the generic pipeline with doc_type='memo'
    and the [MP-Memo] log prefix preserved (V-MP-MEMO-POC scenarios assert
    the prefix verbatim).

    Returns the same dict shape Phase-13 tests expect; the generic pipeline
    adds doc_type and template_version to the structured result, which is
    additive — existing readers ignore the extra keys."""
    return await _doc._run_pipeline(
        intent, "memo", source_md, ctx, log_prefix="MP-Memo"
    )


@server.tool(name="mint_create_memo")
async def create_memo(
    intent: str,
    source_md: str | None = None,
    *,
    ctx: Context,
) -> ToolResult:
    """Generate a klawd-themed Memo via planning dialog.

    Backwards-compat alias preserved per V-MP-DOC-GENERIC forbidden-2:
    delegates to create_document(intent, doc_type='memo', source_md). Kept
    until at least one Claude Desktop session is verified working against
    create_document directly. Scheduled for removal as part of Phase-14 W4
    (MP-MCP-RESOURCES) along with the broader server rename."""
    result = await _run_memo_pipeline(intent, source_md, ctx)
    return _to_tool_result(result)


__all__ = [
    "MEMO_REQUIRED_FIELDS",
    "_DEFAULT_OUTPUT_DIR",
    "DocumentTypeNotFound",
    "MemoElicitationRejected",
    "MemoError",
    "MemoGenerationFailed",
    "MemoSpec",
    "MemoTemplate",
    "MemoTemplateNotFound",
    "_build_document",
    "_emit_body_block",
    "_heuristic_extract",
    "_load_template",
    "_memo_filename",
    "_normalize_body_markdown",
    "_render_body",
    "_resolve_output_dir",
    "_run_memo_pipeline",
    "_substitute",
    "_to_tool_result",
    "create_memo",
    "server",
]
