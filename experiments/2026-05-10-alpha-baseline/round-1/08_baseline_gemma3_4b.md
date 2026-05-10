```word
# MINT: Model-Independent Normalization Toolkit

## Article Draft (WIP — for alpha/beta release)

**Working Title:** “MINT: How We Taught Any LLM to Generate Documents of Consistent Quality”

---

## 1. The Problem (Where It Started)

Generating documents consistently with Large Language Models (LLMs) presented significant challenges early on. Initial integrations revealed several pain points:

*   **Claude** generated valid .docx files, but **GPT-4** with the same prompt produced broken markup, often leading to display issues.
*   **Session A** yielded a perfect .xlsx file, while **Session B** produced empty tables, highlighting the lack of deterministic output.
*   Without a robust mechanism, guaranteeing quality required manual inspection of every generated file, a process that was both time-consuming and prone to human error.
*   Each new document format (e.g., .pptx → .xlsx → .docx) presented a separate hurdle, requiring the duplication of logic and careful handling of format-specific edge cases.

**Key Insight:** The core problem wasn't the inherent “badness” of the LLMs themselves. Instead, it was the absence of a **normalization layer** between the raw LLM output and a valid Open Office XML (OOXML) document format.

---

## 2. The Idea (What)

Rather than attempting to perfect each LLM individually, we conceived of a pipeline designed to provide consistent, high-quality document generation regardless of the underlying model:

```
LLM → Skill Prompt → Sandboxed JS → Validation → Auto-Fix → Visual QA → Ready Document
```

This approach shifted the model’s responsibility solely to content and structural elements. The remaining stages – validation, correction, and security – were designed to be deterministic and independent of the LLM.

**MINT = Model-Independent Normalization Toolkit**. The goal was to achieve Claude-quality output from any LLM.

---

## 3. Architecture Evolution (How It Grew)

The MINT architecture evolved through four distinct phases, each born from a specific pain point:

### Phase 1: “Works on My Machine” (Foundation)

*   **Description:** This initial phase focused on establishing a basic Python package.
*   **Components:**
    *   `config.py`: Configuration settings.
    *   `create.py`:  Functions for generating document content.
    *   `validate.py`: Initial format validation checks.
*   **Skill Prompts:** Markdown files defining the desired document structure and content.
*   **Validation:** Minimal format validation, primarily focused on basic file extensions.
*   **Pain Point:** Every new document format required duplicating logic, leading to code redundancy and increased maintenance overhead.

### Phase 2: “Sandbox & Rules” (Sandbox + Rules)

*   **Description:** This phase introduced a Node.js sandbox for safe execution of generated JavaScript code.
*   **Components:**
    *   Node.js Sandbox: Provided a secure environment for running JavaScript libraries.
    *   docx-js, PptxGenJS, ExcelJS: JavaScript libraries for generating OOXML documents.
    *   YAML-based Rules Engine: A declarative system for defining validation rules.
    *   Style Fingerprinting:  Extracting design tokens (e.g., font styles, colors) from reference documents.
*   **Pain Point:** The rules and validation were “best effort,” offering no guarantees of consistent output.

### Phase 3: “GRACE — When Agents Need a Compass” (Governance)

*   **Description:** Born from practical experience, GRACE emerged as a methodology for managing the increasingly complex codebase.
*   **Key Elements:**
    *   Knowledge Graph (`knowledge-graph.xml`): A centralized navigation map for autonomous agents.
    *   Module Contracts (`MODULE_CONTRACT`):  A source of truth for defining the interfaces and dependencies between modules.
    *   Verification Plan: A formalized testing strategy integrated into the architecture, rather than an afterthought.
    *   Semantic Markup (`START_BLOCK_*`/`END_BLOCK_*):  Used as a load-bearing structure for LLM navigation within the code, not just for documentation.
*   **Quote:** “GRACE is not a framework for humans. It’s a protocol for interaction between autonomous agents and a codebase.”

### Phase 4: “Security & Maturity” (Security + DRY)

*   **Description:** This phase focused on hardening the codebase and improving maintainability.
*   **Improvements:**
    *   Security Audit: Identified and addressed vulnerabilities, including path traversal, zip slip, sandbox bypass, and JavaScript injection attacks (19 files, +335 lines of code).
    *   DRY (Don't Repeat Yourself): Consolidated shared utilities, reducing code duplication (8+ copies of `Path(__file__)` were eliminated).
    *   Thread-Safe Singleton for Configuration: Ensured consistent configuration across the application.
    *   Consolidated XML Parsing: Streamlined XML parsing logic within the `_postprocess_docx` module (reducing 5 passes to 2).

---

## 4. Parallel Branch: code-review-skill

The development of skill prompts for code review led to valuable open-source contributions:

*   While developing skill prompts for code review, we discovered the `awesome-skills/code-review-skill` repository.
*   We identified a lack of guides for popular programming languages: Angular, Svelte, NestJS, Django, Kotlin, C#.
*   We wrote 6 guides (+4700 lines of code) and submitted them as a pull request (#10).
*   We then provided a universal code-quality guide and performance patterns, also submitted as a pull request (#11).
*   These contributions served as “ammunition” for the MINT’s QA pipeline, improving review skills and ultimately enhancing auto-fix capabilities.

---

## 5. Key Architectural Decisions

| Decision | Why |
|---|---|
| Skill prompts, not hardcoded logic | Ensured compatibility with any LLM, any model, and any provider. |
| Node.js sandbox, not Python eval | docx-js, PptxGenJS, ExcelJS are the de-facto standard for OOXML in JavaScript. |
| YAML rules, not Python | Allowed non-trivial agents to add rules without modifying the core code. |
| GRACE semantic markup | Enabled LLM navigation through code without requiring the LLM to parse entire files. |
| MCP tools (g1/g2) | Facilitated integration with Claude Code, Cursor, and other MCP (Model-Controlled Prompt) clients. |
| `json.dumps()` instead of string interpolation |  `json.dumps()` provides a more robust defense against JavaScript injection attacks via crafted input. |
| `Path.resolve() + ..` check |  Used temporary directories outside the project root to prevent path traversal vulnerabilities. |

---

## 6. Where We Are & Where We're Going

**Now:** MINT operates as a runtime environment with validation, auto-fix, fingerprinting, MCP integration, and GRACE-governed codebase.

**Next:** We are developing a template engine and a visual QA pipeline (L1 deterministic + L2 LLM-assisted) – ultimately aiming for “docx-as-code” for any LLM.

---

## 7. Article Structure (for final version)

1.  **Prologue:** A concrete example – “LLM generated a .docx file, the client opened it, and inside was a perfectly formatted table with the data we requested.”
2.  **The Problem:** 3-4 paragraphs detailing the pain points of inconsistent LLM output.
3.  **Architecture:** A pipeline diagram with detailed explanations for each stage.
4.  **GRACE:** How the methodology emerged from practical needs of autonomous agents.
5.  **Security Story:**  A specific account of a vulnerability found and fixed.
6.  **Open-source Feedback Loop:** How contributions to `code-review-skill` improve MINT.
7.  **Lessons Learned:** What we’d do differently (e.g., GRACE from the very beginning).
8.  **Epilogue:** Our vision – “Any LLM, any document, guaranteed quality.”

---
```