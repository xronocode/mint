# MINT Runtime

**Model-Independent Normalization Toolkit** — Claude-quality document generation for any LLM.

MINT is a self-contained Python toolkit for producing validated, design-token-aware OOXML documents. The **Pure Python Edition (v0.3.0)** replaces the legacy Node.js sandbox + docx-js pipeline with a single-process Python implementation.

## Features

- **Pure Python** — `pip install`, no Node.js, no sandbox bridge
- **Fluent SDK** — `Document → Section → Table/Chart/Image` builders
- **Style presets as data** — JSON-defined typography + ColorPalette, three built-ins (`alga_corporate`, `minimal`, `compact`)
- **7 chart factories** + matplotlib/seaborn/plotly adapters via `Chart.from_*`
- **Validation engine** — YAML rules, XPath checks, AUDIT/LENIENT/STRICT severity
- **Auto-fix** — safe/visual cascade (max 3 iterations), destructive rejected with educational hints
- **GRACE metadata** — Custom XML Parts injection (`urn:mint:grace:2026:manifest`)
- **PDF export** — optional, via Gotenberg HTTP

## Quick Start

```bash
pip install mint-runtime
```

```python
from mint_python.sdk import Document, Section, Table, Chart, Image, presets

doc = (
    Document(format="docx", title="Q2 Review")
    .with_style_preset("alga_corporate")
)
doc.add_cover(title="Quarterly Report", subtitle="2026")
doc.add_toc(max_level=2)
doc.add_section(
    Section("Revenue", level=1)
        .add_paragraph("Quarterly trend.")
        .add_table(Table.from_list([["Q", "Rev"], ["Q1", "$1M"], ["Q2", "$1.3M"]]))
        .add_chart(Chart.bar(["Q1", "Q2", "Q3", "Q4"], [1.0, 1.3, 1.6, 1.9],
                              caption="Revenue ($M)"))
)

report = doc.validate(level="lenient")    # ValidationReport
fix = doc.fix(strategy="safe_first")      # FixReport
manifest = doc.inject_grace()             # GRACEManifest
doc.save("output/memo.docx")              # writes the .docx
```

## Architecture

```
src/mint_python/
  core/           Document, Section, Table, Chart, Style, Image, TOC
  sdk/            Public re-exports + presets registry
  rules/          YAML loader + XPath evaluator
  grace/          Custom XML Parts injection
  validate.py     run_checks engine
  fix.py          Auto-fix engine (safe/visual/destructive)
```

Ten MP-* modules + one shared utility, 615 tests, 100% coverage on `src/mint_python/`.

See [`AGENTS.md`](AGENTS.md) for the GRACE engineering protocol and the **Output Path Convention** (where MCP server / CLI / SDK / tests write artifacts — never `/tmp` by default).

## Validation Rules

DOCX hard rules: `D-H01..D-H10` (column width sums, DXA units, bullet formats, raw newlines, TOC levels). Soft rules: `D-S01..D-S05`. PPTX: `P-H01..P-H05`. Rules are YAML-loaded — see `rules/`.

## Style Preset Schema

Documented in [`docs/style-preset-schema.md`](docs/style-preset-schema.md). Validated at module import time; presets ship under `src/mint_python/core/presets/*.json`.

## Testing

```bash
pytest tests/ -v
ruff check src/mint_python/
mypy src/mint_python/
```

## Reference Documents

- [`docs/reference/anthropic_claude_baseline.docx`](docs/reference/anthropic_claude_baseline.docx) — Anthropic Claude reference (gold standard)
- [`docs/reference/docx_showcase_guide.md`](docs/reference/docx_showcase_guide.md) — capabilities target spec
- [`docs/reference/qa_evolution_v2.md`](docs/reference/qa_evolution_v2.md) — QA quality evolution report

## Optional: PDF Rendering

`Document.to_pdf()` calls a Gotenberg HTTP endpoint:

```bash
docker run -d -p 3000:3000 gotenberg/gotenberg:8
export GOTENBERG_URL=http://localhost:3000
```

```python
doc.to_pdf("output/memo.pdf")
```

## License

MIT
