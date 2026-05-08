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
#   cmd_edit - apply EditPlan JSON to existing DOCX (Phase-5)
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - Added cmd_edit for Phase-5 edit pipeline
# END_CHANGE_SUMMARY

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

    prompt_text = args.prompt
    for var in args.var:
        if "=" not in var:
            print(f"Error: --var expects KEY=VALUE, got '{var}'", file=sys.stderr)
            sys.exit(1)
        key, value = var.split("=", 1)
        prompt_text = prompt_text.replace(f"{{{{{key}}}}}", value)

    req = CreateRequest(
        format=args.format,
        tier=args.tier,
        prompt=prompt_text,
        design_tokens=design_tokens,
        template_name=args.template,
        model_response_override=model_response,
        llm_base_url=args.llm_base_url,
        llm_api_key=args.llm_api_key,
        llm_model=args.llm_model,
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


def cmd_edit(args: argparse.Namespace) -> None:
    from dataclasses import asdict

    from mint.edit import EditError, edit, edit_plan_from_dict

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(
            f"Error: plan file not found: {plan_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        raw = json.loads(plan_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"Error: invalid plan JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        plan = edit_plan_from_dict(raw)
    except EditError as exc:
        print(f"Error: {exc.code or 'EDIT_PLAN_INVALID'}: {exc}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else None

    try:
        result = edit(
            Path(args.document),
            plan,
            author=args.author,
            output_path=output_path,
        )
    except EditError as exc:
        print(f"Error: {exc.code or 'EDIT_FAILED'}: {exc}", file=sys.stderr)
        sys.exit(1)

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
    }
    print(json.dumps(payload, indent=2))
    if not result.success:
        sys.exit(1)


def cmd_theme(args: argparse.Namespace) -> None:
    from mint.paths import THEMES_DIR
    from mint.theme import load_theme
    from mint.theme_extract import register_user_theme

    action = args.theme_action

    if action == "list":
        if not THEMES_DIR.exists():
            print(json.dumps({"themes": []}))
            return
        names = sorted(p.stem for p in THEMES_DIR.glob("*.toml"))
        themes = []
        for name in names:
            try:
                t = load_theme(name)
                themes.append(
                    {
                        "name": t.name,
                        "description": t.description,
                        "version": t.version,
                    }
                )
            except Exception as exc:
                themes.append({"name": name, "error": str(exc)})
        print(json.dumps({"themes": themes}, indent=2))
        return

    if action == "show":
        theme = load_theme(args.name)
        print(
            json.dumps(
                {
                    "name": theme.name,
                    "description": theme.description,
                    "version": theme.version,
                    "palette": {
                        "primary": theme.palette.primary,
                        "body": theme.palette.body,
                        "muted": theme.palette.muted,
                        "border": theme.palette.border,
                        "alt_row": theme.palette.alt_row,
                        "accent": theme.palette.accent,
                    },
                    "tables": {
                        "target_width_dxa": theme.tables.target_width_dxa,
                        "header_fill": theme.tables.header.fill,
                        "header_text": theme.tables.header.text,
                        "body_text": theme.tables.body.text,
                    },
                    "typography": {
                        k: {"size": s.size, "color": s.color}
                        for k, s in theme.typography.styles.items()
                    },
                },
                indent=2,
            )
        )
        return

    if action == "extract":
        path = register_user_theme(
            Path(args.document),
            name=args.name,
            description=args.description,
        )
        print(
            json.dumps(
                {
                    "success": True,
                    "name": args.name,
                    "path": str(path),
                    "loadable_via": f"load_theme('{args.name}')",
                },
                indent=2,
            )
        )
        return

    raise SystemExit(f"unknown theme action: {action!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mint",
        description="MINT Runtime — Model-Independent Normalization Toolkit",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    srv = sub.add_parser("serve", help="Start MCP server (G1+G2 tools)")
    srv.add_argument("--transport", default="stdio", choices=["stdio", "sse"])
    srv.add_argument("--host", default="127.0.0.1")
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
    cr.add_argument("--llm-base-url", default=None, help="LLM API base URL")
    cr.add_argument("--llm-api-key", default=None, help="LLM API key")
    cr.add_argument("--llm-model", default=None, help="LLM model name")
    cr.add_argument("--var", action="append", default=[], metavar="KEY=VALUE",
                     help="Template variable substitution (repeatable)")

    # extract
    ex = sub.add_parser("extract", help="Extract design tokens from document")
    ex.add_argument("document", help="Path to DOCX or PPTX file")

    # edit
    ed = sub.add_parser("edit", help="Apply EditPlan JSON to existing DOCX")
    ed.add_argument("document", help="Path to existing DOCX file")
    ed.add_argument(
        "--plan", required=True, help="Path to EditPlan JSON file"
    )
    ed.add_argument(
        "--author", default="MINT", help="Author for revision/comment ops"
    )
    ed.add_argument(
        "--output",
        default=None,
        help="Output path (default: <stem>.edited<ext> next to input)",
    )

    # theme
    th = sub.add_parser("theme", help="Manage MINT design themes")
    th_sub = th.add_subparsers(dest="theme_action", required=True)
    th_sub.add_parser(
        "list", help="List installed themes (mint/themes/*.toml)"
    )
    th_show = th_sub.add_parser("show", help="Print theme tokens as JSON")
    th_show.add_argument("name", help="Theme name (e.g. showcase_v1)")
    th_extract = th_sub.add_parser(
        "extract", help="Extract theme from a reference DOCX"
    )
    th_extract.add_argument("document", help="Path to reference DOCX")
    th_extract.add_argument(
        "name",
        help="Theme name to register (alphanumeric/underscore/hyphen only)",
    )
    th_extract.add_argument(
        "--description",
        default=None,
        help="Optional description; defaults to 'extracted from <file>'",
    )

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
        "edit": cmd_edit,
        "theme": cmd_theme,
    }
    commands[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    main()
