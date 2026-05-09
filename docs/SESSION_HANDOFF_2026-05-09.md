# MINT Pure Python Edition — Session Handoff

**Date:** 2026-05-09
**Branch:** `main`
**Status:** Section/Page API shipped. Per-run formatting, lists, merged cells, hyperlinks, bookmarks, tab stops, footnotes, callouts, multi-column / landscape / per-section H&F all live. **721 tests, 6 fixture-only skips, 100% coverage on `src/mint_python/`, CI green.**

## Project State

MINT — Model-Independent Normalization Toolkit. Pure Python Edition (`src/mint_python/`) is the active implementation; legacy Node.js + docx-js path remains in `src/mint/` for the CLI's `--engine js` (read-only commands now work without LLM credentials).

### Modules

| Module | Path | Lines | Purpose |
|--------|------|-------|---------|
| MP-STYLE | core/style.py | 491 | Typography, ColorPalette, Pt, presets (3) |
| MP-CONTENT | core/content.py | 747 | Paragraph, Run (per-run formatting), Image, hyperlinks, bookmarks, footnotes, tab stops |
| MP-TABLE | core/table.py | 573 | Table + factories, merged cells via Cell.colspan/rowspan |
| MP-LIST | core/list_block.py | 136 | Bullet / numbered / checklist as a first-class block |
| MP-CALLOUT | core/callout.py | 196 | Info / warning / code callout boxes |
| MP-CHART | core/chart.py | 720 | 7 factories + from_matplotlib/seaborn/plotly |
| MP-SECTION | core/section.py | 230 | Fluent builder + with_page_layout |
| MP-PAGE_LAYOUT | core/page_layout.py | 175 | Margins, PageLayout (orientation, columns, header/footer, page break) |
| MP-DOCUMENT | core/document.py | 587 | Facade: cover, TOC, save, validate, fix, inject_grace, to_pdf |
| MP-SDK | sdk/__init__.py | 104 | Public re-exports (~20 types + presets) |
| MP-RULES | rules/__init__.py | 311 | YAML loader, XPath evaluate, 5s timeout |
| MP-VALIDATE | validate.py | 286 | run_checks engine, SeverityMode |
| MP-FIX | fix.py | 259 | Auto-fix: backup → safe/visual → re-validate → cascade |
| MP-GRACE | grace/__init__.py | 277 | Custom XML Parts injection |
| _hash | _hash.py | 30 | SHA-256 file hash |

### User Surface

```python
from mint_python.sdk import (
    Document, Section, Table, Cell, Chart, Style, Image,
    List, ListKind, Callout, CalloutKind,
    PageLayout, Margins,
    TabStop, TabAlignment, TabLeader,
    TOC, Pt, ColorPalette, presets,
)

doc = Document(format="docx", title="Q2").with_style_preset("alga_corporate")
doc.add_cover(title="Report", subtitle="2026")
doc.add_toc(max_level=2)

# Landscape charts section with per-section header/footer.
doc.add_section(
    Section("Charts", level=1)
        .with_page_layout(PageLayout(
            orientation="landscape",
            margins=Margins(top=0.75, bottom=0.75, left=0.75, right=0.75),
            header="Q2 2026", footer="Confidential",
        ))
        .add_chart(Chart.bar(["Q1","Q2","Q3","Q4"], [1.0,1.3,1.6,1.9], caption="Revenue ($M)"))
        .add_callout(Callout(kind=CalloutKind.INFO, body="Bar charts wider in landscape."))
)

# Two-column flow.
doc.add_section(
    Section("Glossary", level=1)
        .with_page_layout(PageLayout(columns=2))
        .add_list(List(kind=ListKind.BULLET, items=["term — defn", "another — defn"]))
)

report = doc.validate(level="lenient")
fix = doc.fix(strategy="safe_first")
manifest = doc.inject_grace()
doc.save("memo.docx")
```

## This Session's Deliverables

Listed by feature — one feat commit per gap closed. All landed on main, all CI green.

### Feature work (7 commits)

- **`b8361ad` MP-CONTENT — per-run formatting** (🔴): Run gains bold/italic/underline/strike/font_size/color overrides. Direct `w:rPr` emission alongside the inherited Paragraph style.
- **`eb093cc` MP-LIST — first-class List block** (🔴): bullet, numbered, checklist via a typed ListKind enum. Numbering definitions emitted into `word/numbering.xml` on demand.
- **`b1d0c57` MP-TABLE — merged cells** (🟡): Cell carries colspan/rowspan; render path emits `w:gridSpan` and `w:vMerge=restart/continue`.
- **`f29978e` MP-CONTENT — hyperlinks + bookmarks** (🟡): `Run.link("https://...")` and `Run.bookmark("anchor")`. External links register relationships on save.
- **`db8ee3d` MP-CONTENT — tab stops on Paragraph** (🟡): TabStop/TabAlignment/TabLeader public types; emitted in paragraph `w:pPr/w:tabs`.
- **`86dc6ba` MP-CONTENT — footnotes via Run.footnote** (🟡): on-demand `word/footnotes.xml` part injection; auto-numbered marker insertion.
- **`2239e7b` MP-CALLOUT — info / warning / code blocks** (🟢): typed CalloutKind enum; bordered/shaded paragraph block with optional title.

### Section/Page API (1 feat commit + 1 lint follow-up)

- **`1d253d6` MP-PAGE_LAYOUT** (🟢, multi-feature): `Section.with_page_layout(PageLayout)`. Configurable orientation (with width/height swap), per-side margins, columns 1..12 with spacing, per-section header/footer overrides, page-break-before vs continuous. Section opens its own docx section break before the heading; preceding cover/TOC stay under the document's default sectPr. **14 unit tests + 1 integration test** covering range validation, dimension swap both ways, twips conversion, header/footer survival in saved parts, idempotent w:cols re-application.
- **`f4d681a` lint follow-up**: replaced `×` with `x` in a comment after CI ruff RUF003 caught it.

### Test, lint, and CI fixes (5 commits)

- **`31d09b0` / `3cbf912` / `4fc6ed5` showcase fingerprint** — three attempts to keep a pinned-hash regression catcher: hashed-bytes → content-hash → c14n2 → finally dropped. Cross-platform divergence ran deeper than serialization. Lenient MP-VALIDATE in the same e2e suite remains as the structural-regression catcher.
- **`d2b2a9e` test_validate_does_not_mutate** — same root cause: raw-byte SHA caught zip-wrapper mtime churn. Replaced with content-hash helper.
- **`105c718` mint_python E501** — three pre-existing long lines with `# pragma: no cover — reason` suffixes; CI ruff was failing on every push because of these. Shortened text where possible, used `# noqa: E501` on the one signal-handler line where the pragma had to stay inline.
- **`bbdb176` CLI engine selection lazy** — `mint validate / fingerprint / extract / edit` no longer require `LLM_BASE_URL`/`LLM_MODEL`. `_select_engine_from_env` reads `MINT_ENGINE` directly; full `config()` is only loaded by commands that actually need it (e.g. `cmd_create`). **6 CLI integration tests went from failing to passing.**

## Showcase Status

`tests/integration/test_mp_showcase_e2e.py::build_showcase_document` exercises every block type and now also exercises:

- Charts § — `with_page_layout(PageLayout(orientation="landscape", margins=..., header="...", footer="..."))`
- Style System § — `with_page_layout(PageLayout(columns=2))`
- All 7 chart factories, all 3 list kinds, all 3 callout kinds, run-level bold/italic/color/links/bookmarks/footnotes/tab stops

The Known Gaps section in the showcase document itself now lists only deferred surfaces (watermarks/textboxes/WordArt, track changes/comments/protection, OLE objects).

## Tests & CI

- **721 passing, 6 skipped** — all skips are fixture-only:
  - `tests/unit/test_theme_extract.py` ×4 — needs `docs/docx_showcase.docx` (reference Word document, expected to be hand-authored; not generated)
  - `tests/unit/test_edit.py::fixture-21` ×1 — needs `large_5mb.docx`; latency budget enforced opportunistically
  - `tests/unit/test_mp_document.py` ×1 — pre-existing parametrize empty-set
- **100% coverage** on `src/mint_python/` (CI gate)
- **CI green** — `lint-and-test (3.12)` covers ruff (`src/mint src/mint_python`), mypy strict, pytest with coverage gate

## Known follow-ups (not blocking)

- 12 pre-existing E501 in `tests/` — CI doesn't lint tests, so these stay until a tests-lint gate gets added
- The legacy Node.js engine (`src/mint/`) remains for `--engine js`; mint_python is the path forward but legacy is still tested and shipped

## Suggested Kickoff for Next Session

```
Continuing MINT Pure Python Edition. Section/page API now shipped (orientation,
margins, columns, per-section H&F, page breaks). 721 tests, 100% coverage, CI green.

The showcase docstring's gap list is down to the deferred-by-design tier:
  - Watermarks, text boxes, WordArt
  - Track changes, comments, document protection
  - Embedded OLE objects

Read docs/SESSION_HANDOFF_2026-05-09.md for full state. Codebase audit otherwise
unblocked: pick from the deferred tier, add a fixture-generator for the
docs/docx_showcase.docx reference (closes 4 theme_extract skips), or tackle
something else from the user.
```

## Commit Timeline (this session, oldest → newest)

```
b8361ad feat(MP-CONTENT): per-run formatting overrides — bold/italic/underline/...
eb093cc feat(MP-LIST): first-class List block — bullet/numbered/checklist
b1d0c57 feat(MP-TABLE): merged cells via Cell.colspan/rowspan render path
f29978e feat(MP-CONTENT): hyperlinks + bookmarks via Run.link / Run.bookmark
db8ee3d feat(MP-CONTENT): tab stops on Paragraph (TabStop / TabAlignment / Ta...
86bc6ba feat(MP-CONTENT): footnotes via Run.footnote with on-demand part inje...
2239e7b feat(MP-CALLOUT): info / warning / code callout block
31d09b0 fix(test): make showcase fingerprint deterministic across runs
bbdb176 fix(cli): make engine selection lazy so read-only commands don't need...
105c718 fix(lint): silence pre-existing E501 in mint_python so CI ruff passes
3cbf912 fix(test): canonicalize XML entries (c14n2) so fingerprint is platfor...
4fc6ed5 fix(test): drop showcase fingerprint test — divergence is deeper than...
d2b2a9e fix(test): hash docx contents in test_validate_does_not_mutate
1d253d6 feat(MP-PAGE_LAYOUT): per-Section page-layout API
f4d681a fix(lint): replace × with x in page_layout comment to satisfy CI ruff...
```
