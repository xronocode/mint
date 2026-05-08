# FILE: src/mint/mcp_g2.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: FastMCP server extending G1 with generation and edit tools:
#             mint_create, mint_extract_style, mint_list_templates, mint_edit
#   SCOPE: MCP tool registration and dispatch for G2 generation + edit pipeline
#   DEPENDS: M-CREATE, M-EXTRACT, M-TEMPLATES, M-EDIT
#   LINKS: docs/knowledge-graph.xml#M-MCP-G2, docs/verification-plan.xml#V-M-MCP-G2
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   mint_create - MCP tool: generate document (code, template, or modular mode)
#   mint_extract_style - MCP tool: extract design tokens from document
#   mint_list_templates - MCP tool: list available templates
#   mint_edit - MCP tool: apply EditPlan JSON to existing DOCX (Phase-5)
#   server_g2 - FastMCP server instance for G2 tools
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - Added mint_edit for Phase-5 edit pipeline
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from fastmcp import FastMCP

from mint.create import CreateRequest, create
from mint.edit import EditError, edit, edit_plan_from_dict
from mint.extract import extract_style
from mint.paths import RULES_DIR, SKILLS_DIR, TEMPLATES_DIR
from mint.templates import TemplateEngine

mcp_g2 = FastMCP("MINT-G2", instructions="MINT G2 tools: create, extract_style, list_templates")
ALLOWED_BASE = Path.cwd().resolve()


def _safe_doc(raw: str) -> Path:
    resolved = Path(raw).resolve()
    if ".." in Path(raw).parts:
        raise ValueError(f"Path traversal detected in '{raw}'")
    return resolved


@mcp_g2.tool()
def mint_create(
    format: str,
    prompt: str,
    tier: str = "frontier",
    model_response_override: str | None = None,
    template_name: str | None = None,
    design_tokens_json: str | None = None,
) -> str:
    """Generate a document using skill prompts, sandbox execution, or template fill.

    Args:
        format: Document format ('docx' or 'pptx')
        prompt: User's document creation request
        tier: Model tier ('small', 'medium', 'frontier')
        model_response_override: Pre-generated model response (JS code or JSON)
        template_name: Template to use for small tier (default: auto-selected)
        design_tokens_json: Optional design tokens as JSON string
    """
    tokens = None
    if design_tokens_json:
        tokens = json.loads(design_tokens_json)

    req = CreateRequest(
        format=format,
        tier=tier,
        prompt=prompt,
        model_response_override=model_response_override,
        template_name=template_name,
        design_tokens=tokens,
    )
    result = create(
        req,
        skills_dir=SKILLS_DIR,
        templates_dir=TEMPLATES_DIR,
        rules_dir=RULES_DIR,
    )
    return json.dumps(
        {
            "success": result.success,
            "output_path": str(result.output_path) if result.output_path else None,
            "execution_mode": result.execution_mode,
            "duration_ms": result.duration_ms,
            "error": result.error,
            "validation": (
                {
                    "passed": result.validation_report.passed,
                    "violations": len(result.validation_report.violations),
                }
                if result.validation_report
                else None
            ),
        },
        indent=2,
    )


@mcp_g2.tool()
def mint_extract_style(document_path: str) -> str:
    """Extract design tokens (colors, typography, layout) from an existing OOXML document.

    Args:
        document_path: Path to DOCX or PPTX file
    """
    tokens = extract_style(_safe_doc(document_path))
    return json.dumps(tokens, indent=2)


@mcp_g2.tool()
def mint_list_templates() -> str:
    """List all available document templates from builtin, extracted, and custom directories."""
    engine = TemplateEngine(TEMPLATES_DIR)
    templates = engine.list_templates()
    return json.dumps(
        [
            {
                "name": t.name,
                "format": t.format,
                "source": t.source,
                "path": str(t.path),
            }
            for t in templates
        ],
        indent=2,
    )


@mcp_g2.tool()
def mint_edit(
    document_path: str,
    edit_plan_json: str,
    author: str = "MINT",
) -> str:
    """Apply a typed EditPlan to an existing DOCX without regeneration.

    The LLM that produced the plan never sees raw OOXML — see M-EDIT contract.
    PPTX is currently rejected with EDIT_OP_UNSUPPORTED (deferred to v0.5).

    Args:
        document_path: Path to DOCX file to edit.
        edit_plan_json: EditPlan as JSON string (see M-EDIT.edit_plan_from_dict).
        author: Author name applied to revision and comment ops; defaults to "MINT".

    Returns:
        EditResult JSON with success / ops_total / ops_succeeded / ops_failed /
        output_path / backup_path / per-op diff / validation report summary /
        duration_ms / error.
    """
    try:
        raw = json.loads(edit_plan_json)
    except json.JSONDecodeError as exc:
        return json.dumps(
            {
                "success": False,
                "error": f"MCP_TOOL_ERROR: invalid edit_plan_json — {exc}",
                "output_path": None,
                "backup_path": None,
                "ops_total": 0,
                "ops_succeeded": 0,
                "ops_failed": 0,
            },
            indent=2,
        )

    try:
        plan = edit_plan_from_dict(raw)
    except EditError as exc:
        return json.dumps(
            {
                "success": False,
                "error": f"{exc.code or 'EDIT_PLAN_INVALID'}: {exc}",
                "output_path": None,
                "backup_path": None,
                "ops_total": 0,
                "ops_succeeded": 0,
                "ops_failed": 0,
            },
            indent=2,
        )

    try:
        result = edit(_safe_doc(document_path), plan, author=author)
    except EditError as exc:
        return json.dumps(
            {
                "success": False,
                "error": f"{exc.code or 'EDIT_FAILED'}: {exc}",
                "output_path": None,
                "backup_path": None,
                "ops_total": len(plan.ops),
                "ops_succeeded": 0,
                "ops_failed": len(plan.ops),
            },
            indent=2,
        )

    payload = {
        "success": result.success,
        "output_path": str(result.output_path) if result.output_path else None,
        "backup_path": str(result.backup_path) if result.backup_path else None,
        "ops_total": result.ops_total,
        "ops_succeeded": result.ops_succeeded,
        "ops_failed": result.ops_failed,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "diff": [asdict(o) for o in result.diff],
        "validation": (
            {
                "passed": result.validation_report.passed,
                "violations": len(result.validation_report.violations),
            }
            if result.validation_report
            else None
        ),
    }
    return json.dumps(payload, indent=2)


server_g2 = mcp_g2
