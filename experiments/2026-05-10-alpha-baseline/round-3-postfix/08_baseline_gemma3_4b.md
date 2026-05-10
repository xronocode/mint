```word
# MINT: Model-Independent Normalization Toolkit

## Article Draft (WIP — for alpha/beta release)

**Working Title**: “MINT: How We Taught Any LLM to Generate Documents of Consistent Quality”

---

## 1. The Problem (Where It Started)

Generating documents reliably with Large Language Models (LLMs) presented significant challenges early on. Initial integrations revealed several key pain points:

*   **Claude** generated valid `.docx` files, but **GPT-4** with the same prompt produced broken markup, often leading to unreadable documents.
*   **Session A** consistently yielded a perfect `.xlsx` file, while **Session B** produced empty tables, highlighting the lack of predictability.
*   The absence of a standardized process meant manual inspection of every generated file was required to ensure quality, a highly inefficient and time-consuming task.
*   Each new document format (e.g., `.pptx` to `.xlsx` to `.docx`) presented a separate, complex hurdle involving diverse libraries and numerous edge cases.

**Key Insight**: The core issue wasn’t the inherent “badness” of the LLMs themselves. Instead, it was the lack of a robust “normalization layer” between the raw output from the LLM and a valid Open Office XML (OOXML) document format.

---

## 2. The Idea (What)

Rather than attempting to force every LLM to be perfect, we conceived of a pipeline designed for maximum flexibility and consistency:

```
LLM → Skill Prompt → Sandboxed JS → Validation → Auto-Fix → Visual QA → Ready Document
```

This approach shifted the model’s responsibility solely to generating content and structuring the document. Everything else – validation, correction, and security – would be handled by deterministic, model-independent processes.

**MINT = Model-Independent Normalization Toolkit**. The goal was to achieve Claude-quality output regardless of the underlying LLM being used.

---

## 3. Architecture Evolution (How It Grew)

The MINT architecture evolved through four distinct phases, each born from a specific practical challenge:

### Phase 1: “Works on My Machine” (Foundation)

*   **Description**: This initial phase focused on creating a basic Python package.
*   **Components**:
    *   `config.py`: Configuration settings.
    *   `create.py`:  Core document creation logic.
    *   `validate.py`: Initial format validation.
*   **Skill Prompts**: Markdown files used to guide the LLM.
*   **Validation**: Minimal format validation was implemented.
*   **Pain Point**:  Every new document format required duplicating logic within the package, leading to code redundancy.

### Phase 2: “Sandbox & Rules” (Sandbox + Rules)

*   **Description**: This phase introduced a Node.js sandbox for executing generated JavaScript code safely.
*   **Components**:
    *   Node.js Sandbox: Provided a secure environment for running JavaScript libraries like `docx-js`, `PptxGenJS`, and `ExcelJS`.
    *   YAML-based Rules Engine:  A declarative system for defining validation rules.
    *   Style Fingerprinting:  Extracting design tokens from reference documents to ensure consistent styling.
*   **Pain Point**: The rules and validation were “best effort” and lacked guarantees of consistent output.

### Phase 3: “GRACE — When Agents Need a Compass” (Governance)

*   **Description**: Born from the need for improved agent behavior within the codebase, GRACE introduced a structured governance approach.
*   **Key Elements**:
    *   Knowledge Graph (`knowledge-graph.xml`): A centralized repository for navigating the codebase, acting as a “map” for autonomous agents.
    *   MODULE_CONTRACT: A source of truth for defining contracts between modules, ensuring consistency.
    *   Verification Plan: A formalized testing strategy integrated into the architecture, not an afterthought.
    *   Semantic Markup (`START_BLOCK_*`/`END_BLOCK_*):  Used as a load-bearing structure for LLM navigation, rather than just documentation.
*   **Quote**: “GRACE is not a framework for humans. It’s a protocol for interaction between autonomous agents and a codebase.”

### Phase 4: “Security & Maturity” (Security + DRY)

*   **Description**: Focused on hardening the codebase and reducing code duplication.
*   **Improvements**:
    *   Security Audit: Identified and addressed vulnerabilities such as path traversal, zip slip, sandbox bypass, and JavaScript injection.
    *   DRY (Don't Repeat Yourself) Pass: Consolidated shared utilities to eliminate redundant code (e.g., `Path(__file__)`).
    *   Thread-Safe Singleton: Implemented a thread-safe configuration using a singleton pattern.
    *   Consolidated XML Parsing: Streamlined XML parsing logic within the `_postprocess_docx` module, reducing the number of passes from five to two.

---

## 4. Parallel Branch: code-review-skill

The development of skill prompts for code review led to valuable open-source contributions:

*   While developing skill prompts for code review, we discovered the `awesome-skills/code-review-skill` repository.
*   We identified a lack of guides for popular programming languages like Angular, Svelte, NestJS, Django, Kotlin, and C#.
*   We wrote six guides (+4700 lines of code) and submitted them as a pull request (#10).
*   We then provided a universal code-quality guide and performance patterns, also submitted as a pull request (#11).
*   These contributions served as “ammunition” for MINT’s quality assurance (QA) pipeline, improving review skills and, consequently, auto-fix capabilities.

---

## 5. Key Architectural Decisions

| Decision | Why |
|---|---|
| Skill prompts, not hardcoded logic |  Ensured compatibility with any LLM, any model, and any provider. |
| Node.js sandbox, not Python eval |  `docx-js`, `PptxGenJS`, and `ExcelJS` are the de-facto standards for OOXML in JavaScript. |
| YAML rules, not Python |  Allows non-trivial agents to add rules without modifying the core code. |
| GRACE semantic markup |  Enables LLM navigation through the codebase without requiring the LLM to read entire files. |
| MCP tools (g1/g2) |  Facilitates integration with models like Claude Code, Cursor, and other MCP (Model-Controlled Prompt) clients. |
| `json.dumps()` instead of string interpolation |  Mitigates JavaScript injection vulnerabilities by using a safe method for string conversion. |
| `Path.resolve() + ..` check |  Prevents tests from accessing files outside the project root directory. |

---

## 6. Where We Are & Where We're Going

**Now**: We operate a functional runtime environment with validation, auto-fix, fingerprinting, MCP integration, and GRACE-governed codebase.

**Next**: We are developing a template engine and a visual QA pipeline (L1 deterministic + L2 LLM-assisted) – ultimately aiming for a “docx-as-code” solution for any LLM.

---

## 7. Article Structure (for final version)

1.  **Prologue**:  A concrete example – “LLM generated a `.docx` file, the client opened it, and inside was a perfectly formatted table with the data we requested.”
2.  **The Problem**: 3-4 paragraphs detailing the pain points of inconsistent LLM output.
3.  **Architecture**: A pipeline diagram with detailed explanations for each stage.
4.  **GRACE**:  How the methodology emerged from practical needs of autonomous agents.
5.  **Security Story**:  Specific vulnerabilities and how they were found and fixed.
6.  **Open-source Feedback Loop**:  How contributions to `code-review-skill` improve MINT.
7.  **Lessons Learned**:  What we’d do differently (e.g., GRACE from the very beginning).
8.  **Epilogue**:  The vision – “Any LLM, any document, guaranteed quality.”

---

## Article Draft v2 (2026-05-09 — Pure Python Edition)

**Working Title**: “MINT v0.3.0: How We Dropped Node.js and Got a Cleaner, Safer, One-Command Toolkit”

### 1. What Changed Since v1

v1 ended at *Phase 4: Security & Maturity* – Python orchestrator over a Node.js sandbox running `docx-js`/`pptxgenjs`. v2 is the **Pure Python Pivot** – Phases 5–12 in the GRACE plan, shipped as v0.3.0.

### 2. The Trigger: “Why Are We Shipping Two Languages?”

The accumulating pain points that drove the shift to a pure Python implementation:

*   **Install Friction**: `pip install` + `npm install` + Node version pin + sandbox bootstrap.
*   **Five-Hop Pipeline**: Python → JSON → JS code-string → sandbox stdout → Python validator.
*   **Two Attack Surfaces**: Python `eval` guard *and* Node sandbox escape.
*   **matplotlib was Already Python**: Charts rendered server-side – the JS round-trip earned nothing.

> **Insight**: `docx-js` was right *for Claude* (JS-first stack). For an open toolkit, it was technical debt.

### 3. Architecture: What Replaced What

```
Before: LLM → JS code → Node sandbox → docx-js → .docx
After: LLM → mint_python.sdk → python-docx → .docx
```

Ten `MP-*` modules + one shared hash utility:

| Layer | Modules | Purpose |
|---|---|---|
| Surface | MP-SDK | Fluent re-export (Document, Section, Table, Chart, Style, Image, TOC, Pt, ColorPalette, presets) |
| Authoring | MP-DOCUMENT / MP-SECTION / MP-CONTENT / MP-TABLE / MP-CHART | Builders; 7 chart factories + matplotlib/seaborn/plotly adapters |
| Style | MP-STYLE | Typography, ColorPalette, three built-in presets |
| Validation | MP-RULES / MP-VALIDATE / MP-FIX | YAML rules, XPath checks, AUDIT/LENIENT/STRICT, auto-fix cascade (max 3) |
| Metadata | MP-GRACE | Custom XML Parts injection (`urn:mint:grace:2026:manifest`) |

### 4. Design System as Data, Not as Code

Style presets ship as JSON in `core/presets/*.json` against a documented schema. Three built-ins: `alga_corporate`, `minimal`, `compact`. Documents reference palette tokens (`@primary`) – a brand swap is one JSON file, not a code change.

### 5. Security After the Pivot

The Node sandbox was removed, but pure-Python introduced new surfaces:

*   **ZIP Bomb on Auto-Fix**: Stream-based rewrite + 100 MB size guard.
*   **XPath DoS in Validation**: `signal.alarm` 5s timeout per check.
*   **XXE in GRACE Manifest Parse**: Explicit `XMLParser(resolve_entities=False)` everywhere.

### 6. GRACE Paid for Itself

The pivot rewrote ~80% of the codebase. Without module contracts, the knowledge graph, and the verification plan, it would have been a rewrite-from-scratch. Instead, it ran wave-by-wave, every phase gated by an integrity audit. Reviews stayed scoped to graph-declared module boundaries.

### 7. Where v0.3.0 Stands

*   615 tests, 100% coverage on `src/mint_python/`
*   Zero stubs, zero `NotImplementedError`
*   One-command install: `pip install mint-runtime`
*   Showcase E2E baseline: 260 KB document – cover + TOC + 8 sections + 7 chart types + 3 tables + image

### 8. Lessons Learned, Revised

| v1 Lesson | v2 Update |
|---|---|
| “GRACE from the very beginning” | Confirmed – GRACE absorbed the language pivot without a rewrite. |
| “Skill prompts, not hardcoded logic” | Still valid for *content* generation, but **runtime quality belongs in code, not in the prompt**. |
| “Node.js sandbox is de-facto for OOXML” | False for open toolkits. Pick the runtime your users already have. |
| “Visual QA is the next step” | Shipped – Gotenberg HTTP integration via `Document.to_pdf` |

### 9. Known Gaps (Next Chapter)

*   Per-run formatting (bold/italic/color/font_size) – Run carries only text + Style preset.
*   Lists (bulleted/numbered/checklist) as a first-class block.
*   Merged table cells, hyperlinks, bookmarks, footnotes.
*   Multi-column / landscape sections, headers/footers per section.

### 10. Epilogue, Revised

> v1: “Any LLM, any document, guaranteed quality.”
> v2: “Any LLM, any document, guaranteed quality – and one `pip install`.”
```