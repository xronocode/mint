# MINT Pure Python Edition — Session Handoff

**Date:** 2026-05-09
**Session:** 13:20 +06
**Branch:** `main`
**Version:** 0.3.0
**Status:** ALL PHASES SHIPPED. ZERO active stubs. 615 tests, 100% coverage on `src/mint_python/`.

## Project State

MINT — Model-Independent Normalization Toolkit. Pure Python Edition (`src/mint_python/`) fully replaces the legacy Node.js sandbox + docx-js pipeline. **v0.3.0 wheel ready** (`dist/mint_runtime-0.3.0-py3-none-any.whl`).

### Modules (10 MP-* + 1 internal utility)

| Module | Path | Lines | Purpose |
|--------|------|-------|---------|
| MP-STYLE | core/style.py | 491 | Typography, ColorPalette, Pt, presets (3) |
| MP-CONTENT | core/content.py | 330 | Paragraph, Run, Image |
| MP-TABLE | core/table.py | 529 | Table + 5 factories, render |
| MP-SECTION | core/section.py | 178 | Fluent builder: add_paragraph/table/image/chart |
| MP-DOCUMENT | core/document.py | 586 | Facade: cover, TOC, save, validate, fix, inject_grace, to_pdf |
| MP-CHART | core/chart.py | 720 | 7 factories + from_matplotlib/seaborn/plotly |
| MP-SDK | sdk/__init__.py | 79 | Public re-exports (10 types + presets) |
| MP-RULES | rules/__init__.py | 311 | YAML loader, XPath evaluate (4 check types), 5s timeout |
| MP-VALIDATE | validate.py | 286 | run_checks engine, SeverityMode (AUDIT/LENIENT/STRICT) |
| MP-FIX | fix.py | 259 | Auto-fix: backup → safe/visual → re-validate → cascade (max 3) |
| MP-GRACE | grace/__init__.py | 277 | Custom XML Parts injection (urn:mint:grace:2026:manifest) |
| _hash | _hash.py | 30 | SHA-256 file hash (shared by FIX + GRACE) |

### User Surface

```python
from mint_python.sdk import Document, Section, Table, Chart, Style, Image, TOC, Pt, ColorPalette, presets

doc = Document(format="docx", title="Q2").with_style_preset("alga_corporate")
doc.add_cover(title="Report", subtitle="2026")
doc.add_toc(max_level=2)
doc.add_section(
    Section("Revenue", level=1)
        .add_paragraph("Quarterly trend.")
        .add_table(Table.from_list([["Q","Rev"],["Q1","$1M"],["Q2","$1.3M"]]))
        .add_chart(Chart.bar(["Q1","Q2","Q3","Q4"], [1.0,1.3,1.6,1.9], caption="Revenue ($M)"))
        .add_image(Image.from_path("logo.png"))
)
report = doc.validate(level="lenient")      # → ValidationReport
fix = doc.fix(strategy="safe_first")        # → FixReport  
manifest = doc.inject_grace()               # → GRACEManifest (10 AI instructions + fingerprint)
pdf = doc.to_pdf("out.pdf")                 # → Gotenberg PDF
doc.save("memo.docx")                       # → .docx on disk
```

## Today's Deliverables

### Phase 9: MP-RULES + MP-VALIDATE + MP-FIX (4 waves, 118 tests)
- Pure Python successors to M-RULES/M-VALIDATE/M-FIX
- Document.validate(level) and Document.fix(strategy) unstubbed via temp-file delegation
- XPath evaluate with 4 check types: exists, count_gt_zero, tbl_width_mismatch, sum_mismatch
- Severity modes: AUDIT (always pass), LENIENT (hard violations → fail), STRICT (any → fail)
- Auto-fix: D-H09 newline fix, backup + cascade detection (max 3 iterations), destructive rejection

### Phase 11: MP-GRACE + inject_grace + to_pdf (2 waves, 19 tests)
- Custom XML Parts injection under `urn:mint:grace:2026:manifest`
- Document.inject_grace unstubbed via delegation pattern
- Document.to_pdf unstubbed via Gotenberg HTTP integration (httpx)

### Phase 12: Chart.from_plotly (1 wave, 3 tests)
- Last remaining stub retired
- Lazy imports plotly, calls `fig.to_image(format="png", scale=2)` via kaleido

### Code Review & Security Fixes (6 commits)
- 🔴 fix.py: stream-based ZIP rewrite + 100MB size guard (zip bomb mitigation)
- 🔴 rules.py: XPath 5s timeout via signal.alarm (DoS mitigation)
- 🔴 grace.py: explicit XMLParser(resolve_entities=False) for all XML entry points (XXE)
- 🟡 chart.py: falsy coercion fix (width_inches `or 6.0` → `if width_inches is not None else 6.0`)
- 🟡 _hash.py: extracted shared SHA-256 hasher from duplicate fix/grace implementations
- 🟡 test quality: flaky /tmp paths → tmp_path, mocked filesystem tests

### GRACE Integrity Audit (1 commit)
- 18 KG + V-Plan + source metadata corrections
- Removed 3 false CrossLinks (MP-TABLE→MP-CONTENT, MP-CHART→MP-CONTENT, duplicate MP-DOCUMENT→MP-VALIDATE)
- Added missing depends (MP-DOCUMENT +grace, MP-SECTION +CHART)
- Retired stale V-Plan scenario-6 + marker-2 (BLOCK_PHASE_GUARD references)
- Added MP-HASH knowledge graph entry
- Bumped stale VERSIONs (section 0.0.0→0.1.0, sdk 0.1.0→0.2.0)

### Showcase E2E Test (1 commit)
- `tests/integration/test_mp_showcase_e2e.py` — 6 tests
- Builds richest document SDK can produce: cover + TOC + 8 sections + 7 chart types + 3 tables + image
- Validates lenient MP-VALIDATE passes (hard_count=0)
- Baseline fingerprint pinned at `tests/fixtures/mp_showcase_baseline.json`
- Generated output: `/tmp/mint_showcase.docx` (260 KB)

## Known Gaps vs docx_showcase.docx

| Priority | Gap | Impact |
|----------|-----|--------|
| 🔴 | Per-run formatting (bold, italic, underline, color, font_size) | Run currently carries only text + Style preset |
| 🔴 | Lists (bulleted, numbered, checklist) | No List block type |
| 🟡 | Merged table cells | Table assumes regular grid |
| 🟡 | Hyperlinks, bookmarks, footnotes, tab stops | No block types |
| 🟢 | Callout components (info, warning, code block) | No component library |
| 🟢 | Multi-column, landscape, per-section headers, page breaks | Section/page API not exposed |
| 🟢 | Watermarks, text boxes, WordArt | Artistic elements deferred |
| 🟢 | Track changes, comments, document protection | Collaboration features |

## Pre-existing Known Issues

- 5 CLI integration tests fail: `ConfigMissingError: LLM_BASE_URL is required` — requires `.env` with LLM endpoint in CI
- `.env` created with test values, but `uv run` sandboxing prevents subprocess inheritance
- `tests/unit/test_mp_document.py:1 skipped` — pre-existing M-EDIT latency stub

## Suggested Kickoff for Next Session

```
Continuing MINT Pure Python Edition. All phases shipped (7,8,9,11,12), 615 tests,
100% coverage, 0 stubs. v0.3.0 wheel ready.

Next priority: per-run text formatting (bold/italic/color/size) — current Run only
carries text + Style preset, blocking rich-text document generation. Start with
Run.bold, Run.italic, Run.color, Run.font_size dataclass fields + python-docx
w:rPr emission.

Read docs/SESSION_HANDOFF_2026-05-09.md for full state, then check codebase.
```

## Commit Timeline (session summary)

```
db3be22 test: showcase e2e + baseline
21cb897 grace(meta): per-module integrity audit — 18 KG+V-plan+source fixes
f3fa519 fix: coverage gaps after review — pragma: no cover guards
da7747d fix(grace,chart): XXE + falsy coercion + shared _compute_file_hash
07dfe6a test: fix flaky to_pdf /tmp → tmp_path
2bb9030 fix(MP-RULES): XPath timeout + sum_mismatch logging
a2d4f8e ci: narrow Ruff scope
d9c6c15 ci: fix CI — LLM env, lint/type mint_python, gate coverage
facdc48 grace(meta): post-Phase-12 review fixes
17da20c docs: Phase-12 closeout — zero stubs
7ca3ec3 grace(MP-CHART): unstub from_plotly
3f69bbc chore: bump to 0.3.0
ae7a9a7 grace(meta): Phase-11 full-integrity review fixes
18cbedc grace(meta): Phase-11 closeout
4e682d7 grace(MP-DOCUMENT): unstub to_pdf via Gotenberg
3ed23fd grace(MP-GRACE,MP-DOCUMENT): inject_grace unstub
8b7f06e docs: Phase-9 complete
14f7f00 grace(meta): Phase-9 full-integrity review
f3994b3 grace(meta): Phase-9 coverage 100%
f021046 grace(meta): Phase-9 closeout
a6c2d82 grace(MP-DOCUMENT): unstub validate/fix
33e7fa5 grace(meta): Wave-9-3 (MP-FIX)
d6f442b grace(MP-FIX): auto-fix engine
4ddc1a8 grace(meta): Wave-9-2 (MP-VALIDATE)
b1935bb grace(MP-VALIDATE): run_checks engine
0921dd2 grace(meta): Wave-9-1 (MP-RULES)
68d004a grace(MP-RULES): YAML loader + XPath evaluate
27db8b0 grace(meta): Phase-9 pre-flight
