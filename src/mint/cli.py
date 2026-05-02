# FILE: src/mint/cli.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: CLI entry point for MINT runtime
#   SCOPE: argparse CLI with subcommands: serve, validate, fix, fingerprint, create, extract
#   DEPENDS: M-MCP-G1, M-MCP-G2, M-VALIDATE, M-FIX, M-FINGERPRINT, M-CREATE, M-EXTRACT
#   LINKS: docs/knowledge-graph.xml
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   main - CLI entry point with argument parsing
#   cmd_serve - start MCP server (G1+G2)
#   cmd_validate - validate document
#   cmd_fix - auto-fix document
#   cmd_fingerprint - compute style fingerprint
#   cmd_create - generate document
#   cmd_extract - extract design tokens
# END_MODULE_MAP

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_serve(args: argparse.Namespace) -> None:
    from mint.mcp_g1 import mcp as g1_server
    from mint.mcp_g2 import mcp_g2 as g2_server

    transport = getattr(args, "transport", "stdio")
    if transport == "stdio":
        from fastmcp import FastMCP

        merged = FastMCP("MINT")
        merged.mount(g1_server)
        merged.mount(g2_server)
        merged.run(transport="stdio")
    else:
        from fastmcp import FastMCP

        merged = FastMCP("MINT")
        merged.mount(g1_server)
        merged.mount(g2_server)
        merged.run(transport="sse", host=args.host, port=args.port)


def cmd_validate(args: argparse.Namespace) -> None:
    from mint.config import SeverityMode
    from mint.validate import run_checks

    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    report = run_checks(
        Path(args.document),
        SeverityMode(args.severity),
        rules_dir=rules_dir,
    )
    result = {
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
    print(json.dumps(result, indent=2))
    if not report.passed:
        sys.exit(1)


def cmd_fix(args: argparse.Namespace) -> None:
    from mint.fix import fix as fix_document

    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    result = fix_document(args.document, rules_dir=rules_dir)
    output = {
        "fixed_path": str(result.fixed_path) if result.fixed_path else None,
        "backup_path": str(result.backup_path) if result.backup_path else None,
        "iterations": result.iterations,
        "applied_fixes": result.applied_fixes,
        "remaining_violations": [v.rule_id for v in result.remaining_violations],
        "diff": result.diff,
    }
    print(json.dumps(output, indent=2))


def cmd_fingerprint(args: argparse.Namespace) -> None:
    from mint.fingerprint import compute as fp_compute

    result = fp_compute(Path(args.document))
    output = {
        "hash": result.hash,
        "format": result.format,
        "xml_sources": result.xml_sources,
    }
    print(json.dumps(output, indent=2))


def cmd_create(args: argparse.Namespace) -> None:
    from mint.create import CreateRequest, create

    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    skills_dir = Path(args.skills_dir) if args.skills_dir else None
    templates_dir = Path(args.templates_dir) if args.templates_dir else None

    design_tokens = None
    if args.design_tokens:
        design_tokens = json.loads(args.design_tokens)

    model_response = None
    if args.model_response_file:
        model_response = Path(args.model_response_file).read_text()

    req = CreateRequest(
        format=args.format,
        tier=args.tier,
        prompt=args.prompt,
        design_tokens=design_tokens,
        template_name=args.template,
        model_response_override=model_response,
    )
    result = create(
        req,
        skills_dir=skills_dir,
        templates_dir=templates_dir,
        rules_dir=rules_dir,
    )
    output = {
        "success": result.success,
        "output_path": str(result.output_path) if result.output_path else None,
        "execution_mode": result.execution_mode,
        "duration_ms": result.duration_ms,
        "error": result.error,
    }
    print(json.dumps(output, indent=2))
    if not result.success:
        sys.exit(1)


def cmd_extract(args: argparse.Namespace) -> None:
    from mint.extract import extract_style

    tokens = extract_style(Path(args.document))
    print(json.dumps(tokens, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mint",
        description="MINT Runtime — Model-Independent Normalization Toolkit",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    srv = sub.add_parser("serve", help="Start MCP server (G1+G2 tools)")
    srv.add_argument("--transport", default="stdio", choices=["stdio", "sse"])
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=8080)

    # validate
    val = sub.add_parser("validate", help="Validate OOXML document")
    val.add_argument("document", help="Path to DOCX or PPTX file")
    val.add_argument("--severity", default="audit", choices=["audit", "lenient", "strict"])
    val.add_argument("--rules-dir", default=None)

    # fix
    fx = sub.add_parser("fix", help="Auto-fix OOXML document")
    fx.add_argument("document", help="Path to DOCX or PPTX file")
    fx.add_argument("--rules-dir", default=None)

    # fingerprint
    fp = sub.add_parser("fingerprint", help="Compute style fingerprint")
    fp.add_argument("document", help="Path to DOCX or PPTX file")

    # create
    cr = sub.add_parser("create", help="Generate document")
    cr.add_argument("format", choices=["docx", "pptx"], help="Output format")
    cr.add_argument("prompt", help="Document creation prompt")
    cr.add_argument("--tier", default="frontier", choices=["small", "medium", "frontier"])
    cr.add_argument("--model-response-file", default=None, help="File with model JS code or JSON")
    cr.add_argument("--template", default=None, help="Template name for small tier")
    cr.add_argument("--design-tokens", default=None, help="Design tokens JSON string")
    cr.add_argument("--rules-dir", default=None)
    cr.add_argument("--skills-dir", default=None)
    cr.add_argument("--templates-dir", default=None)

    # extract
    ex = sub.add_parser("extract", help="Extract design tokens from document")
    ex.add_argument("document", help="Path to DOCX or PPTX file")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    commands = {
        "serve": cmd_serve,
        "validate": cmd_validate,
        "fix": cmd_fix,
        "fingerprint": cmd_fingerprint,
        "create": cmd_create,
        "extract": cmd_extract,
    }
    commands[args.command](args)
