# FILE: src/mint/mcp_g1.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: FastMCP server exposing G1 tools: mint_validate, mint_fix, mint_fingerprint
#   SCOPE: MCP tool registration and dispatch
#   DEPENDS: M-VALIDATE, M-FIX, M-FINGERPRINT
#   LINKS: docs/knowledge-graph.xml#M-MCP-G1, docs/verification-plan.xml#V-M-MCP-G1
# END_MODULE_CONTRACT

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from mint.config import SeverityMode
from mint.fingerprint import compute as fp_compute
from mint.fix import fix as fix_document
from mint.validate import ValidationReport, run_checks

mcp = FastMCP("MINT-G1", instructions="MINT G1 tools: validate, fix, fingerprint")

RULES_DIR = Path(__file__).parent.parent.parent / "rules"


def _report_to_dict(report: ValidationReport) -> dict[str, object]:
    return {
        "passed": report.passed,
        "total": report.total,
        "hard_count": report.hard_count,
        "soft_count": report.soft_count,
        "mode": report.mode,
        "violations": [
            {
                "rule_id": v.rule_id,
                "severity": v.severity.value,
                "fix_category": v.fix_category.value,
                "message": v.message,
                "hint": v.hint,
            }
            for v in report.violations
        ],
    }


@mcp.tool()
def mint_validate(
    document_path: str,
    severity_mode: str = "audit",
) -> str:
    """Validate an OOXML document against MINT rules.

    Args:
        document_path: Path to DOCX or PPTX file
        severity_mode: audit (log only), lenient (fail hard), strict (fail any)
    """
    mode = SeverityMode(severity_mode)
    report = run_checks(
        Path(document_path), mode, rules_dir=RULES_DIR
    )
    return json.dumps(_report_to_dict(report), indent=2)


@mcp.tool()
def mint_fix(document_path: str) -> str:
    """Auto-fix safe/visual violations in an OOXML document.

    Creates backup before fixing. Rejects destructive fixes.

    Args:
        document_path: Path to DOCX or PPTX file
    """
    result = fix_document(Path(document_path), rules_dir=RULES_DIR)
    return json.dumps(
        {
            "fixed_path": str(result.fixed_path) if result.fixed_path else None,
            "backup_path": str(result.backup_path) if result.backup_path else None,
            "iterations": result.iterations,
            "applied_fixes": result.applied_fixes,
            "remaining_violations": [
                v.rule_id for v in result.remaining_violations
            ],
            "diff": result.diff,
        },
        indent=2,
    )


@mcp.tool()
def mint_fingerprint(document_path: str) -> str:
    """Compute style fingerprint hash for an OOXML document.

    Args:
        document_path: Path to DOCX or PPTX file
    """
    result = fp_compute(Path(document_path))
    return json.dumps(
        {
            "hash": result.hash,
            "format": result.format,
            "xml_sources": result.xml_sources,
            "drift_status": None,
        },
        indent=2,
    )


server_g1 = mcp
