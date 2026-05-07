# FILE: src/mint/mcp_g2.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: FastMCP server extending G1 with generation tools:
#             mint_create, mint_extract_style, mint_list_templates
#   SCOPE: MCP tool registration and dispatch for G2 generation pipeline
#   DEPENDS: M-CREATE, M-EXTRACT, M-TEMPLATES
#   LINKS: docs/knowledge-graph.xml#M-MCP-G2, docs/verification-plan.xml#V-M-MCP-G2
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   mint_create - MCP tool: generate document (code, template, or modular mode)
#   mint_extract_style - MCP tool: extract design tokens from document
#   mint_list_templates - MCP tool: list available templates
#   server_g2 - FastMCP server instance for G2 tools
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from mint.create import CreateRequest, create
from mint.extract import extract_style
from mint.templates import TemplateEngine

mcp_g2 = FastMCP("MINT-G2", instructions="MINT G2 tools: create, extract_style, list_templates")

SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"
TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
RULES_DIR = Path(__file__).parent.parent.parent / "rules"


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
    tokens = extract_style(Path(document_path))
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


server_g2 = mcp_g2
