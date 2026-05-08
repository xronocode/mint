# MINT Runtime

**Model-Independent Normalization Toolkit** — Claude-quality document generation for any LLM.

MINT is a self-contained runtime that replicates Claude's document generation pipeline as a standalone, model-agnostic toolkit. It uses skill prompts, a sandboxed execution engine, deterministic validation, visual QA, and GRACE metadata injection.

## Features

- **Model-agnostic**: Works with any OpenAI-compatible API (GPT-4, GLM, Qwen, Llama, local models)
- **Three-tier quality**: Frontier (JS code execution), Medium (guided code), Small (template fill)
- **OOXML validation**: 20+ rules for DOCX (D-H01..D-H10, D-S01..D-S05) and PPTX (P-H01..P-H05)
- **Auto-fix**: Safe and visual fixes applied automatically; destructive rejected with educational hints
- **Style extraction**: Design tokens from existing documents (colors, typography, layouts)
- **Visual QA**: L1 programmatic checks + L2 render-based via Gotenberg (overlap, margins, fonts)
- **GRACE metadata**: Custom XML Parts injection for document structure and edit rules
- **MCP server**: Full Model Context Protocol support for LLM tool integration

## Quick Start

### Install

```bash
# Clone and install
git clone https://github.com/xronocode/mint.git
cd mint
pip install -e ".[dev]"
npm install  # for sandbox (docx-js, pptxgenjs, exceljs)
```

### Configure

```bash
cp .env.example .env
# Edit .env with your LLM endpoint
```

### CLI Usage

> **Phase-6 note:** the default engine is `python`, which currently raises
> `NotImplementedError` until rollout Phase 1. Set `MINT_ENGINE=js` in your
> `.env` or pass `--engine js` to use the JS-backed runtime. Examples below
> are written with `--engine js` so they remain runnable today.

```bash
# Validate a document
mint --engine js validate document.docx --severity strict

# Auto-fix violations
mint --engine js fix document.docx

# Compute style fingerprint
mint --engine js fingerprint document.docx

# Extract design tokens
mint --engine js extract document.pptx

# Generate a document (with model response)
mint --engine js create docx "Create a business memo" --tier frontier --model-response-file response.js

# Generate from template (small models)
mint --engine js create docx "Quarterly report" --tier small --template business-memo --model-response-file content.json

# Start MCP server
mint --engine js serve
```

### MCP Server

MINT exposes all tools via the Model Context Protocol:

**G1 Tools (Validation)**
- `mint_validate` — Validate document against rules
- `mint_fix` — Auto-fix safe/visual violations
- `mint_fingerprint` — Compute style drift hash

**G2 Tools (Generation)**
- `mint_create` — Generate document (code or template mode)
- `mint_extract_style` — Extract design tokens from document
- `mint_list_templates` — List available templates

### Docker

```bash
docker compose up -d
```

Starts three containers:
- **MINT** (port 8080) — MCP server + Node.js sandbox
- **Gotenberg** (port 3000) — PDF/PNG rendering for visual QA
- **LibreChat** (port 3080) — Multi-model UI with MCP support

## Architecture

```
src/mint/
  config.py       — Configuration (env vars, .env file)
  sandbox/         — Node.js execution sandbox (docx-js, pptxgenjs, exceljs)
  rules/           — YAML-based OOXML validation rules
  validate.py      — Validation engine (audit/lenient/strict modes)
  fix.py           — Auto-fix engine (safe/visual/destructive categories)
  fingerprint.py   — SHA256 style fingerprint for drift detection
  skills/          — Skill prompt registry (tier × format)
  templates/       — Template engine (placeholder fill + design tokens)
  extract.py       — Style extraction (Layer 1: theme, Layer 2: statistical)
  create.py        — Create orchestrator (skill → model → execute → validate)
  qa/              — QA pipeline (L1 programmatic + L2 render-based)
  grace/           — GRACE metadata injection (Custom XML Parts)
  mcp_g1.py        — MCP tools: validate, fix, fingerprint
  mcp_g2.py        — MCP tools: create, extract_style, list_templates
  cli.py           — CLI entry point
```

## Validation Rules

### DOCX Hard Rules (D-H01..D-H10)
| Rule | Description | Fix Category |
|------|-------------|--------------|
| D-H01 | Column widths must sum to table width | destructive |
| D-H02 | No default body text style | destructive |
| D-H03 | Fixed widths only, no percentage | destructive |
| D-H09 | No raw newlines in text runs | safe |

### DOCX Soft Rules (D-S01..D-S05)
Style consistency checks (font usage, heading hierarchy, etc.)

### PPTX Hard Rules (P-H01..P-H05)
Font embedding, layout safety, margin checks.

## Testing

```bash
# Run all tests
pytest tests/ -v

# Lint and typecheck
ruff check src/mint/
mypy src/mint/
```

137 tests, ruff clean, mypy strict clean.

## License

MIT
