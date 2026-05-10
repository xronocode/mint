# MINT: Model-Independent Normalization Toolkit

## Article Draft (WIP — for alpha/beta release)

**Working title**: "MINT: How We Taught Any LLM to Generate Documents of Consistent Quality"

---

## 1. The Problem (Where It Started)

Pain points discovered when integrating LLMs into document workflows:

- **Claude** generates valid docx, but **GPT-4** with the same prompt produces broken markup
- **Session A** yields a perfect xlsx, **Session B** — empty tables
- No way to **guarantee** quality without manually inspecting every file
- Each new format (pptx → xlsx → docx) is a separate hell with libraries and edge cases

**Key insight**: the problem isn't that models are "bad" — it's the absence of a **normalization layer** between raw LLM output and a valid OOXML document.

---

## 2. The Idea (What)

Instead of teaching every model to be perfect — build a pipeline:

```
LLM → Skill Prompt → Sandboxed JS → Validation → Auto-Fix → Visual QA → Ready Document
```

The model is responsible **only for content and structure**. Everything else — validation, correction, security — is deterministic and model-independent.

**MINT = Model-Independent Normalization Toolkit**. Claude-quality output for any model.

---

## 3. Architecture Evolution (How It Grew)

Described as **4 phases**, each born from real pain:

### Phase 1: "Works on My Machine" (Foundation)
- Simple Python package: `config.py`, `create.py`, `validate.py`
- Skill prompts as Markdown files
- Minimal format validation
- **Pain**: every new format required duplicating logic

### Phase 2: "Sandbox & Rules" (Sandbox + Rules)
- Node.js sandbox for safe execution of generated JS code (docx-js, PptxGenJS, ExcelJS)
- YAML-based rules engine for declarative validation rules
- Style fingerprinting — extracting design tokens from reference documents
- **Pain**: rules and validation were "best effort", no guarantees

### Phase 3: "GRACE — When Agents Need a Compass" (Governance)

GRACE was born from practice, not theory:

- Agents got lost in the codebase → **Knowledge Graph** (`knowledge-graph.xml`) as a navigation map
- Contracts drifted from code → **MODULE_CONTRACT** as source of truth
- Tests were written after the fact → **Verification Plan** as part of architecture, not an afterthought
- Semantic markup (`START_BLOCK_*/END_BLOCK_*`) — not documentation, but **load-bearing structure** for LLM navigation

> "GRACE is not a framework for humans. It's a protocol for interaction between autonomous agents and a codebase."

### Phase 4: "Security & Maturity" (Security + DRY)
- Security audit: path traversal, zip slip, sandbox bypass, JS injection — 19 files, +335 lines
- DRY pass: `_security.py`, `paths.py`, `_xml_ns.py` — shared utilities replacing 8+ copies of `Path(__file__)`
- Thread-safe singleton for configuration
- Consolidated XML parsing in `_postprocess_docx` (5 passes → 2)

---

## 4. Parallel Branch: code-review-skill

How MINT work led to open-source contributions:

- While developing skill prompts for code review, we found `awesome-skills/code-review-skill`
- Saw missing guides for Angular, Svelte, NestJS, Django, Kotlin, C#
- Wrote 6 guides (+4700 lines) → PR #10
- Returned with universal code-quality guide and performance patterns → PR #11
- Each contribution is "ammunition" for MINT's QA pipeline: better review skills → better auto-fix

---

## 5. Key Architectural Decisions

| Decision | Why |
|---|---|
| Skill prompts, not hardcoded logic | Any LLM, any model, any provider |
| Node.js sandbox, not Python eval | docx-js/PptxGenJS/ExcelJS — de-facto standard for OOXML in JS |
| YAML rules, not Python | Non-trivial agents can add rules without changing code |
| GRACE semantic markup | LLM navigation through code without reading entire files |
| MCP tools (g1/g2) | Integration with Claude Code, Cursor, other MCP clients |
| `json.dumps()` instead of string interpolation | JS injection via crafted input is a real attack vector |
| Path.resolve() + `..` check | Tests use temp dirs outside project root |

---

## 6. Where We Are & Where We're Going

**Now**: working runtime with validation, auto-fix, fingerprinting, MCP integration, and GRACE-governed codebase.

**Next**: Template engine + visual QA pipeline (L1 deterministic + L2 LLM-assisted) → full "docx-as-code" for any LLM.

---

## 7. Article Structure (for final version)

1. **Prologue**: one concrete example — "LLM generated a docx, client opened it, and inside was…"
2. **The Problem**: 3-4 paragraphs about the pain of inconsistent LLM output
3. **Architecture**: pipeline diagram with explanations for each stage
4. **GRACE**: how the methodology emerged from practical needs of autonomous agents
5. **Security Story**: specific vulnerabilities and how they were found/fixed
6. **Open-source Feedback Loop**: how contributions to code-review-skill improve MINT
7. **Lessons Learned**: what we'd do differently (e.g., GRACE from the very beginning)
8. **Epilogue**: vision — "any LLM, any document, guaranteed quality"

---

## Article Draft v2 (2026-05-09 — Pure Python Edition)

**Working title**: "MINT v0.3: How We Dropped Node.js and Got a Cleaner, Safer, One-Command Toolkit"

### 1. What changed since v1

v1 ended at *Phase 4: Security & Maturity* — Python orchestrator over a Node.js sandbox running docx-js/pptxgenjs. v2 is the **Pure Python pivot** — Phases 5–12 in the GRACE plan, shipped as v0.3.0.

### 2. The trigger: "why are we shipping two languages?"

Pain points that piled up after Phase 4:

- **Install friction**: `pip install` + `npm install` + Node version pin + sandbox bootstrap
- **Five-hop pipeline**: Python → JSON → JS code-string → sandbox stdout → Python validator
- **Two attack surfaces**: Python eval guard *and* Node sandbox escape
- **matplotlib was already Python**: charts rendered server-side — the JS round-trip earned nothing

> **Insight**: docx-js was right *for Claude* (JS-first stack). For an open toolkit it was technical debt.

### 3. Architecture: what replaced what

```
Before:  LLM → JS code → Node sandbox → docx-js → .docx
After:   LLM → mint_python.sdk → python-docx → .docx
```

Ten MP-* modules + one shared hash utility:

| Layer | Modules | Purpose |
|---|---|---|
| Surface | MP-SDK | Fluent re-export (Document, Section, Table, Chart, Style, Image, TOC, Pt, ColorPalette, presets) |
| Authoring | MP-DOCUMENT / MP-SECTION / MP-CONTENT / MP-TABLE / MP-CHART | Builders; 7 chart factories + matplotlib/seaborn/plotly adapters |
| Style | MP-STYLE | Typography, ColorPalette, three built-in presets |
| Validation | MP-RULES / MP-VALIDATE / MP-FIX | YAML rules, XPath checks, AUDIT/LENIENT/STRICT, auto-fix cascade (max 3) |
| Metadata | MP-GRACE | Custom XML Parts injection (`urn:mint:grace:2026:manifest`) |

### 4. Design system as data, not as code

Style presets ship as JSON in `core/presets/*.json` against a documented schema. Three built-ins: `alga_corporate`, `minimal`, `compact`. Documents reference palette tokens (`@primary`) — a brand swap is one JSON file, not a code change.

### 5. Security after the pivot

The Node sandbox left, but pure-Python opened new surfaces:

- **ZIP bomb on auto-fix**: stream-based rewrite + 100 MB size guard
- **XPath DoS in validation**: `signal.alarm` 5s timeout per check
- **XXE in GRACE manifest parse**: explicit `XMLParser(resolve_entities=False)` everywhere

### 6. GRACE paid for itself

The pivot rewrote ~80% of the codebase. Without module contracts, knowledge graph, and verification plan it would have been a rewrite-from-scratch. Instead it ran wave-by-wave, every phase gated by an integrity audit. Reviews stayed scoped to graph-declared module boundaries.

### 7. Where v0.3.0 stands

- 615 tests, 100% coverage on `src/mint_python/`
- Zero stubs, zero `NotImplementedError`
- One-command install: `pip install mint-runtime`
- Showcase E2E baseline: 260 KB document — cover + TOC + 8 sections + 7 chart types + 3 tables + image

### 8. Lessons learned, revised

| v1 lesson | v2 update |
|---|---|
| "GRACE from the very beginning" | Confirmed — GRACE absorbed the language pivot without a rewrite |
| "Skill prompts, not hardcoded logic" | Still valid for *content* generation, but **runtime quality belongs in code, not in the prompt** |
| "Node.js sandbox is de-facto for OOXML" | False for open toolkits. Pick the runtime your users already have |
| "Visual QA is the next step" | Shipped — Gotenberg HTTP integration via `Document.to_pdf` |

### 9. Known gaps (next chapter)

- Per-run formatting (bold/italic/color/font_size) — Run carries only text + Style preset
- Lists (bulleted/numbered/checklist) as a first-class block
- Merged table cells, hyperlinks, bookmarks, footnotes
- Multi-column / landscape sections, headers/footers per section

### 10. Epilogue, revised

> v1: "Any LLM, any document, guaranteed quality."
> v2: "Any LLM, any document, guaranteed quality — and one `pip install`."
