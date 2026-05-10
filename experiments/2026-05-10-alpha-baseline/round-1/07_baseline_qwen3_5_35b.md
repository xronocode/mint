# MINT: Model-Independent Normalization Toolkit
## Ensuring Consistent Document Quality Across Any LLM

### Introduction

Integrating Large Language Models (LLMs) into document workflows promises efficiency, but it often introduces instability. A common scenario involves an LLM generating a valid `.docx` file in one session, only to produce broken markup in the next. One model might yield a perfect spreadsheet, while another returns empty tables. Without a robust normalization layer, guaranteeing quality requires manual inspection of every file, turning automation into a bottleneck.

The **Model-Independent Normalization Toolkit (MINT)** addresses this gap. It is not a model itself, but a pipeline designed to ensure that any LLM—regardless of provider or version—can generate documents of consistent, validated quality. By separating content generation from structural integrity, MINT transforms unpredictable LLM output into reliable, production-ready documents.

---

### 1. The Problem: Inconsistency in Automation

The core challenge in LLM-driven document generation is the absence of a normalization layer between raw model output and a valid OOXML document.

**Key Pain Points:**
*   **Markup Fragility:** A prompt that works for Claude may produce broken XML for GPT-4.
*   **Session Variance:** Identical prompts can yield perfect results in Session A and empty tables in Session B.
*   **Format Fragmentation:** Each new format (`.pptx`, `.xlsx`, `.docx`) requires separate libraries and edge-case handling.
*   **Lack of Guarantees:** There is no deterministic way to ensure a generated file is valid without human inspection.

The issue is not that the models are "bad"; it is that the pipeline lacks a deterministic safety net. MINT was built to fill this void.

---

### 2. The Solution: A Normalization Pipeline

MINT operates on a simple principle: the model is responsible **only for content and structure**. Everything else—validation, correction, security, and formatting—is handled by a deterministic, model-independent pipeline.

**The MINT Workflow:**
```text
LLM → Skill Prompt → Sandboxed Execution → Validation → Auto-Fix → Visual QA → Ready Document
```

This architecture ensures that the model focuses on creativity and content, while the toolkit enforces technical correctness. The result is **Claude-quality output for any model**.

---

### 3. Architectural Evolution

The toolkit has evolved through distinct phases, each born from real-world pain points encountered during development.

#### Phase 1: Foundation
The initial iteration was a simple Python package featuring `config.py`, `create.py`, and `validate.py`. Skill prompts were stored as Markdown files with minimal format validation.
*   **Limitation:** Every new format required duplicating logic, leading to rapid code bloat.

#### Phase 2: Sandbox & Rules
To handle complex OOXML libraries, the architecture introduced a Node.js sandbox for safe execution of generated JavaScript (utilizing `docx-js`, `PptxGenJS`, and `ExcelJS`). A YAML-based rules engine allowed for declarative validation, and style fingerprinting extracted design tokens from reference documents.
*   **Limitation:** Rules and validation were "best effort" without guarantees.

#### Phase 3: GRACE (Governance)
As autonomous agents began navigating the codebase, a governance protocol named **GRACE** was introduced.
*   **Knowledge Graph:** A `knowledge-graph.xml` file acts as a navigation map for agents.
*   **Contracts:** `MODULE_CONTRACT` serves as the source of truth, preventing drift between code and documentation.
*   **Verification Plans:** Testing is integrated into the architecture, not added as an afterthought.
*   **Semantic Markup:** Tags like `START_BLOCK_*` and `END_BLOCK_*` provide load-bearing structure for LLM navigation without requiring full file reads.

> **GRACE Philosophy:** "GRACE is not a framework for humans. It's a protocol for interaction between autonomous agents and a codebase."

#### Phase 4: Security & Maturity
The final phase of the initial architecture focused on hardening. A security audit identified 19 files and over 335 lines of code dedicated to preventing path traversal, zip slip, sandbox bypass, and JS injection. Shared utilities replaced duplicated logic, and XML parsing was consolidated for efficiency.

---

### 4. The Pivot: From Node.js to Pure Python (v0.3.0)

While the initial architecture was effective, shipping two languages (Python orchestrator + Node.js sandbox) introduced friction.

**The Trigger:**
*   **Install Friction:** Users required `pip install` + `npm install` + Node version pinning.
*   **Pipeline Complexity:** The process involved five hops: Python → JSON → JS code-string → sandbox stdout → Python validator.
*   **Dual Attack Surfaces:** Security risks existed in both the Python eval guard and the Node sandbox escape vectors.
*   **Redundancy:** Since `matplotlib` was already Python-based, the JS round-trip for charts offered no benefit.

**The Decision:**
MINT v0.3.0 pivoted to a **Pure Python** runtime. This eliminated the Node.js dependency, reducing the attack surface and simplifying deployment.

**Architecture Comparison:**

| Feature | v1 (Node.js Sandbox) | v2 (Pure Python) |
| :--- | :--- | :--- |
| **Runtime** | Python Orchestrator + Node Sandbox | Pure Python |
| **Libraries** | `docx-js`, `PptxGenJS`, `ExcelJS` | `python-docx`, `matplotlib`, `seaborn` |
| **Install** | `pip` + `npm` + Version Pinning | `pip install mint-runtime` |
| **Security** | Sandbox escape + Eval guard | Stream-based rewrites + XPath timeouts |
| **Charts** | JS-based rendering | Server-side Python rendering |

**New Module Structure:**
The v0.3.0 release includes ten MP-* modules and one shared hash utility, organized into layers:
*   **Surface:** `MP-SDK` (Fluent re-export of Document, Section, Table, Chart, Style, etc.)
*   **Authoring:** `MP-DOCUMENT`, `MP-SECTION`, `MP-CONTENT`, `MP-TABLE`, `MP-CHART`
*   **Style:** `MP-STYLE` (Typography, ColorPalette, presets)
*   **Validation:** `MP-RULES`, `MP-VALIDATE`, `MP-FIX` (YAML rules, XPath checks, auto-fix cascade)
*   **Metadata:** `MP-GRACE` (Custom XML Parts injection)

---

### 5. Security Hardening

The transition to Pure Python opened new security surfaces, which were addressed with specific mitigations.

| Vulnerability | Mitigation Strategy |
| :--- | :--- |
| **ZIP Bomb** | Stream-based rewrite + 100 MB size guard during auto-fix. |
| **XPath DoS** | `signal.alarm` 5s timeout per validation check. |
| **XXE Injection** | Explicit `XMLParser(resolve_entities=False)` in GRACE manifest parsing. |
| **Path Traversal** | `Path.resolve()` + `..` check to prevent directory escape. |
| **JS Injection** | `json.dumps()` used instead of string interpolation for all inputs. |

---

### 6. Design System as Data

MINT treats design systems as data rather than code. Style presets are shipped as JSON files in `core/presets/` against a documented schema.

*   **Built-in Presets:** `alga_corporate`, `minimal`, `compact`.
*   **Tokenization:** Documents reference palette tokens (e.g., `@primary`).
*   **Benefit:** A brand swap requires changing one JSON file, not rewriting code.

---

### 7. Ecosystem and Open Source

MINT’s development is tightly coupled with the broader open-source community. While developing skill prompts for code review, the team identified gaps in `awesome-skills/code-review-skill` regarding frameworks like Angular, Svelte, NestJS, Django, Kotlin, and C#.

*   **Contributions:** Six guides (4,700+ lines) were written and merged via PR #10.
*   **Universal Patterns:** A universal code-quality guide and performance patterns were returned via PR #11.
*   **Feedback Loop:** Each contribution acts as "ammunition" for MINT's QA pipeline; better review skills lead to better auto-fix capabilities.

---

### 8. Current State and Roadmap

**Current Capabilities (v0.3.0):**
*   **Runtime:** Working validation, auto-fix, fingerprinting, and MCP integration.
*   **Coverage:** 615 tests with 100% coverage on `src/mint_python/`.
*   **Zero Stubs:** No `NotImplementedError` or placeholder code.
*   **E2E Baseline:** A 260 KB document featuring a cover, TOC, 8 sections, 7 chart types, 3 tables, and images.

**Future Roadmap:**
*   **Template Engine:** Implementing a template engine for "docx-as-code" workflows.
*   **Visual QA Pipeline:** A two-layer approach (L1 deterministic + L2 LLM-assisted) for visual verification.
*   **Gotenberg Integration:** HTTP integration via `Document.to_pdf` for visual QA.

**Known Gaps (Next Chapter):**
*   Per-run formatting (bold/italic/color/font_size) — currently limited to Style presets.
*   Lists (bulleted/numbered/checklist) as first-class blocks.
*   Merged table cells, hyperlinks, bookmarks, and footnotes.
*   Multi-column/landscape sections and per-section headers/footers.

---

### 9. Lessons Learned

The evolution of MINT offers several key takeaways for building LLM tooling:

1.  **GRACE from the Beginning:** The governance protocol absorbed the language pivot without a full rewrite. It is essential for long-term maintainability.
2.  **Runtime Quality vs. Prompts:** While skill prompts are vital for content, runtime quality belongs in code, not in the prompt.
3.  **Runtime Selection:** Do not assume a JS sandbox is de-facto for OOXML. Pick the runtime your users already have (Python).
4.  **Visual QA is Critical:** Deterministic validation is not enough; visual QA is the necessary final step for document integrity.

---

### 10. Conclusion

MINT represents a shift in how we approach LLM document generation. By decoupling content generation from structural validation, we have moved from a state of uncertainty to one of guarantee.

**v1 Vision:** "Any LLM, any document, guaranteed quality."
**v2 Vision:** "Any LLM, any document, guaranteed quality — and one `pip install`."

With the Pure Python pivot, MINT is now a streamlined, secure, and robust toolkit ready for production workflows.