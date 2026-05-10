# MINT: Model-Independent Normalization Toolkit
## Ensuring Consistent Document Quality Across Any LLM

### Prologue: The Broken Document

It started with a simple request. An engineer asked an LLM to generate a quarterly report in DOCX format. The model produced a file. It opened in Word. The text was there, but the formatting was corrupted. The tables were empty. The headers were missing.

In a different session, the same prompt yielded a perfect spreadsheet. In another, a presentation deck that failed to render images. The models were not "bad"—they were capable of generating valid content. The problem was the absence of a normalization layer between raw LLM output and a valid OOXML document.

This is the problem MINT solves.

---

### 1. The Problem: Inconsistent LLM Output

Integrating Large Language Models (LLMs) into document workflows introduces significant friction. While models excel at generating text, they struggle with the structural rigidity required by document formats like DOCX, XLSX, and PPTX.

Key pain points discovered during early integration include:

*   **Format Fragility:** A prompt that generates valid DOCX with one model (e.g., Claude) may produce broken markup with another (e.g., GPT-4).
*   **Session Variance:** Session A might yield a perfect spreadsheet, while Session B produces empty tables using the exact same prompt.
*   **Lack of Guarantees:** There is no automated way to guarantee quality without manually inspecting every generated file.
*   **Format Hell:** Each new format (PPTX → XLSX → DOCX) requires separate libraries, edge case handling, and maintenance.

The core insight is that the problem is not that models are "bad" at documents. It is that they lack a **normalization layer** to ensure their output conforms to strict document standards.

---

### 2. The Solution: MINT Architecture

MINT (Model-Independent Normalization Toolkit) shifts the responsibility of the LLM. The model is responsible **only for content and structure**. Everything else—validation, correction, security, and formatting—is handled by a deterministic, model-independent pipeline.

The MINT pipeline follows this flow:

```
LLM → Skill Prompt → Sandboxed Execution → Validation → Auto-Fix → Visual QA → Ready Document
```

This architecture ensures that regardless of the underlying model provider, the output adheres to a consistent quality standard.

#### Core Architectural Decisions

| Decision | Rationale |
| :--- | :--- |
| **Skill Prompts** | Decouples logic from code; allows any LLM to generate content without hardcoded constraints. |
| **Sandboxed Execution** | Isolates generated code to prevent security risks during document construction. |
| **YAML Rules Engine** | Enables non-technical agents to add validation rules without modifying core code. |
| **GRACE Semantic Markup** | Provides load-bearing structure for LLM navigation without reading entire files. |
| **MCP Tools** | Facilitates integration with Claude Code, Cursor, and other MCP clients. |

---

### 3. Architecture Evolution: From Node.js to Pure Python

The toolkit has evolved through four distinct phases, each born from real-world pain points.

#### Phase 1: Foundation
The initial iteration was a simple Python package (`config.py`, `create.py`, `validate.py`). It used skill prompts as Markdown files with minimal format validation.
*   **Limitation:** Every new format required duplicating logic.

#### Phase 2: Sandbox & Rules
To handle complexity, the architecture introduced a Node.js sandbox for safe execution of generated JavaScript (using `docx-js`, `PptxGenJS`, `ExcelJS`). A YAML-based rules engine handled declarative validation.
*   **Limitation:** Rules and validation were "best effort" with no guarantees.

#### Phase 3: GRACE Governance
As autonomous agents grew within the codebase, they required navigation aids. GRACE (Governance, Rules, and Contracts for Execution) was introduced:
*   **Knowledge Graph:** A `knowledge-graph.xml` file acts as a navigation map.
*   **Module Contracts:** The `MODULE_CONTRACT` serves as the source of truth.
*   **Verification Plans:** Tests are written as part of the architecture, not as an afterthought.
*   **Semantic Markup:** Tags like `START_BLOCK_*` and `END_BLOCK_*` provide load-bearing structure for LLM navigation.

> "GRACE is not a framework for humans. It's a protocol for interaction between autonomous agents and a codebase."

#### Phase 4: Security & Maturity
A rigorous security audit addressed path traversal, zip slip, sandbox bypass, and JS injection vulnerabilities. The codebase underwent a DRY (Don't Repeat Yourself) pass, consolidating XML parsing and configuration into thread-safe singletons.

---

### 4. The Pivot: MINT v0.3.0 (Pure Python)

In version 0.3.0, MINT underwent a significant architectural pivot. The dependency on a Node.js sandbox was removed in favor of a **Pure Python** runtime.

#### Why the Change?
The previous architecture required `pip install` + `npm install`, along with Node version pinning and sandbox bootstrapping. This created a five-hop pipeline:
1.  Python orchestrator
2.  JSON serialization
3.  JS code-string generation
4.  Sandbox stdout capture
5.  Python validation

This introduced two attack surfaces and unnecessary friction. The insight was that while `docx-js` is standard for JS-first stacks, it was technical debt for an open Python toolkit.

#### New Architecture
The pipeline is now streamlined:

```
LLM → mint_python.sdk → python-docx → .docx
```

| Layer | Modules | Purpose |
| :--- | :--- | :--- |
| **Surface** | `MP-SDK` | Fluent re-export (Document, Section, Table, Chart, Style, Image, TOC, Pt, ColorPalette). |
| **Authoring** | `MP-DOCUMENT`, `MP-SECTION`, `MP-CONTENT`, `MP-TABLE`, `MP-CHART` | Builders with 7 chart factories + matplotlib/seaborn/plotly adapters. |
| **Style** | `MP-STYLE` | Typography, ColorPalette, and three built-in presets. |
| **Validation** | `MP-RULES`, `MP-VALIDATE`, `MP-FIX` | YAML rules, XPath checks, AUDIT/LENIENT/STRICT modes, auto-fix cascade. |
| **Metadata** | `MP-GRACE` | Custom XML Parts injection (`urn:mint:grace:2026:manifest`). |

#### Design System as Data
Style presets are shipped as JSON in `core/presets/*.json` against a documented schema. Three built-ins include `alga_corporate`, `minimal`, and `compact`. Documents reference palette tokens (e.g., `@primary`). A brand swap is now a single JSON file change, not a code modification.

---

### 5. Security Story

Removing the Node sandbox reduced the attack surface, but pure Python introduced new vectors. MINT addressed these with specific mitigations:

*   **ZIP Bomb on Auto-Fix:** Implemented stream-based rewriting with a 100 MB size guard.
*   **XPath DoS in Validation:** Added a `signal.alarm` 5-second timeout per check.
*   **XXE in GRACE Manifest Parse:** Enforced explicit `XMLParser(resolve_entities=False)` everywhere.

The security audit covered 19 files and over 335 lines of defensive code, ensuring the toolkit remains safe for production use.

---

### 6. Open Source Feedback Loop

MINT’s development is tightly coupled with the open-source community. While developing skill prompts for code review, the team identified gaps in the `awesome-skills/code-review-skill` repository.

*   **Contributions:** Six guides were written (+4,700 lines) covering Angular, Svelte, NestJS, Django, Kotlin, and C#.
*   **Universal Quality:** A universal code-quality guide and performance patterns were returned via PR #11.
*   **Impact:** Each contribution acts as "ammunition" for MINT's QA pipeline. Better review skills lead to better auto-fix capabilities.

---

### 7. Lessons Learned

The journey from v1 to v0.3.0 has yielded critical insights for future development.

| Lesson | Update |
| :--- | :--- |
| **"GRACE from the very beginning"** | Confirmed. GRACE absorbed the language pivot without a rewrite. |
| **"Skill prompts, not hardcoded logic"** | Valid for *content* generation, but **runtime quality belongs in code, not in the prompt**. |
| **"Node.js sandbox is de-facto for OOXML"** | False for open toolkits. Pick the runtime your users already have. |
| **"Visual QA is the next step"** | Shipped. Gotenberg HTTP integration via `Document.to_pdf` is now available. |

---

### 8. Current Status and Known Gaps

**Current State:**
MINT v0.3.0 is a working runtime with validation, auto-fix, fingerprinting, MCP integration, and a GRACE-governed codebase.
*   **Coverage:** 615 tests, 100% coverage on `src/mint_python/`.
*   **Installation:** One-command install: `pip install mint-runtime`.
*   **Showcase:** A 260 KB E2E baseline document including cover, TOC, 8 sections, 7 chart types, 3 tables, and images.

**Known Gaps (Next Chapter):**
*   Per-run formatting (bold/italic/color/font_size) — currently limited to text + Style preset.
*   Lists (bulleted/numbered/checklist) as a first-class block.
*   Merged table cells, hyperlinks, bookmarks, and footnotes.
*   Multi-column / landscape sections, headers/footers per section.

---

### 9. Epilogue

The vision for MINT is clear.

> **v1:** "Any LLM, any document, guaranteed quality."
> **v2:** "Any LLM, any document, guaranteed quality — and one `pip install`."

By decoupling content generation from document construction, MINT ensures that the quality of your output depends on your standards, not the whims of a model.