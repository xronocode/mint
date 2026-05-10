# MINT: Model-Independent Normalization Toolkit
### Ensuring Consistent Document Quality Across Any LLM

## Prologue: The Broken Document

Imagine this scenario: An LLM generates a document based on a complex prompt. The file is created, saved, and sent to a client. When the client opens the file, the formatting is corrupted, tables are empty, or the markup is invalid.

This is not a hypothetical edge case; it is a recurring pain point in modern document workflows. When integrating Large Language Models (LLMs) into production environments, consistency is the primary challenge. One model (e.g., Claude) may generate valid OOXML, while another (e.g., GPT-4) produces broken markup using the same prompt. Session A yields a perfect spreadsheet, while Session B results in empty tables.

The root cause is rarely that the models are "bad." The problem is the absence of a **normalization layer** between raw LLM output and a valid OOXML document. Without this layer, every new format (`.docx`, `.xlsx`, `.pptx`) becomes a separate hell of libraries and edge cases.

## The Challenge: Inconsistent LLM Output

Integrating LLMs into document workflows introduces three critical friction points:

1.  **Format Fragility:** LLMs are probabilistic text generators, not deterministic document builders. They often hallucinate XML tags or omit required structural elements.
2.  **Security Risks:** Allowing LLMs to generate executable code or file structures opens vectors for injection attacks, path traversal, and sandbox escapes.
3.  **Maintenance Overhead:** Every new document format requires duplicating logic across different libraries and edge cases, leading to technical debt.

The goal of the **MINT (Model-Independent Normalization Toolkit)** project was to decouple content generation from document construction. The model is responsible only for content and structure; everything else—validation, correction, security, and formatting—is deterministic and model-independent.

## Architecture Evolution: From Sandbox to Pure Python

The development of MINT followed a clear evolutionary path, driven by real-world pain points and the need for scalability.

### Phase 1: Foundation and the Node.js Sandbox (v1)

The initial architecture relied on a hybrid approach. The pipeline was designed as follows:

```text
LLM → Skill Prompt → Sandboxed JS → Validation → Auto-Fix → Visual QA → Ready Document
```

In this version, the LLM generated JavaScript code (using libraries like `docx-js`, `PptxGenJS`, or `ExcelJS`) which was executed in a Node.js sandbox. This was chosen because these libraries were considered the de-facto standard for OOXML manipulation in JavaScript.

**Key Features:**
*   **Skill Prompts:** Markdown files defining how the LLM should interact with the document structure.
*   **YAML Rules Engine:** Declarative validation rules for checking document integrity.
*   **Style Fingerprinting:** Extracting design tokens from reference documents to maintain brand consistency.

**Limitations:**
While functional, this approach introduced significant friction. It required installing both Python and Node.js environments, creating a "five-hop pipeline" (Python → JSON → JS code-string → sandbox stdout → Python validator). It also presented two distinct attack surfaces: the Python evaluation guard and the Node sandbox escape.

### Phase 2: GRACE — Governance for Autonomous Agents

As the codebase grew, autonomous agents began to get lost within the complexity. The **GRACE** framework was born from practice, not theory, to provide a navigation map for agents interacting with the codebase.

*   **Knowledge Graph:** A `knowledge-graph.xml` file acts as a navigation map, allowing agents to understand dependencies without reading the entire codebase.
*   **Module Contracts:** A `MODULE_CONTRACT` serves as the source of truth, ensuring contracts do not drift from the actual code.
*   **Verification Plans:** Testing is integrated into the architecture as a verification plan, not an afterthought.
*   **Semantic Markup:** Tags like `START_BLOCK_*` and `END_BLOCK_*` provide load-bearing structure for LLM navigation.

> "GRACE is not a framework for humans. It's a protocol for interaction between autonomous agents and a codebase."

### Phase 3: The Pivot to Pure Python (v0.3.0)

The decision to pivot to a Pure Python architecture (v0.3.0) was driven by the need for a cleaner, safer, and more accessible toolkit. The Node.js sandbox was deemed technical debt for an open toolkit, particularly since the target runtime (Python) already had robust libraries for document manipulation.

**The New Pipeline:**
```text
LLM → mint_python.sdk → python-docx → .docx
```

This shift consolidated the runtime into a single language, eliminating the need for a Node.js sandbox. The architecture now relies on the `python-docx` library, which is stable, well-documented, and requires no external runtime dependencies beyond Python itself.

## Key Architectural Decisions

The following table outlines the critical decisions made during the evolution of MINT and the rationale behind them.

| Decision | Rationale |
| :--- | :--- |
| **Skill Prompts, Not Hardcoded Logic** | Ensures compatibility with any LLM, model, or provider. |
| **Pure Python SDK** | Eliminates install friction and reduces attack surface compared to Node.js sandboxing. |
| **YAML Rules, Not Python** | Allows non-trivial agents to add validation rules without modifying core code. |
| **GRACE Semantic Markup** | Enables LLM navigation through code without reading entire files. |
| **MCP Tools (g1/g2)** | Facilitates integration with Claude Code, Cursor, and other MCP clients. |
| **`json.dumps()` Usage** | Prevents JS injection via crafted input by avoiding string interpolation. |
| **Path Resolution Checks** | Prevents path traversal attacks by validating `..` sequences in file paths. |

## Security and Reliability

Security was a primary driver for the v0.3.0 pivot. While removing the Node.js sandbox reduced the attack surface, pure Python introduced new vectors that required rigorous mitigation.

### Security Audit Findings
A comprehensive audit identified 19 files and over 335 lines of code dedicated to security hardening. Key vulnerabilities addressed include:

1.  **ZIP Bomb on Auto-Fix:** Mitigated by implementing a stream-based rewrite with a 100 MB size guard.
2.  **XPath DoS in Validation:** Addressed by implementing a `signal.alarm` timeout (5 seconds) per check to prevent infinite loops.
3.  **XXE in GRACE Manifest Parse:** Resolved by explicitly using `XMLParser(resolve_entities=False)` across all parsing operations.

### DRY (Don't Repeat Yourself) Pass
To ensure maintainability, a DRY pass was conducted to consolidate shared utilities. Files such as `_security.py`, `paths.py`, and `_xml_ns.py` replaced over eight copies of `Path(__file__)` logic. Configuration is now managed via a thread-safe singleton, and XML parsing is consolidated into a single `_postprocess_docx` function, reducing processing passes from five to two.

## Design System as Data

A major shift in v0.3.0 was treating the design system as data rather than code. Style presets are shipped as JSON files located in `core/presets/*.json`, validated against a documented schema.

**Built-in Presets:**
*   `alga_corporate`
*   `minimal`
*   `compact`

Documents reference palette tokens (e.g., `@primary`) rather than hardcoded hex codes. This allows for a brand swap to be executed by changing a single JSON file, rather than requiring a code change.

## The Open-Source Feedback Loop

The development of MINT is closely tied to the broader open-source ecosystem. While developing skill prompts for code review, the team identified gaps in the `awesome-skills/code-review-skill` repository.

*   **Contributions:** The team wrote six guides (over 4,700 lines) covering Angular, Svelte, NestJS, Django, Kotlin, and C#.
*   **Universal Quality:** A universal code-quality guide and performance patterns were returned via PR #11.
*   **Impact:** Each contribution acts as "ammunition" for MINT's QA pipeline. Better review skills lead to better auto-fix capabilities, creating a positive feedback loop between the toolkit and the community.

## Lessons Learned

Reflecting on the journey from v1 to v0.3.0, several key lessons emerged:

1.  **GRACE from the Beginning:** While GRACE was initially a response to complexity, it proved essential for absorbing the language pivot without a full rewrite.
2.  **Runtime Quality Belongs in Code:** Skill prompts are valid for *content* generation, but *runtime quality* (validation, security, formatting) must reside in code, not in the prompt.
3.  **Runtime Selection:** The assumption that "Node.js sandbox is de-facto for OOXML" was false for open toolkits. The best runtime is the one your users already have.
4.  **Visual QA:** The next step involves a visual QA pipeline (L1 deterministic + L2 LLM-assisted) to achieve full "docx-as-code" capabilities.

## Current State and Future Outlook

**Current Status (v0.3.0):**
*   **Runtime:** Working runtime with validation, auto-fix, fingerprinting, and MCP integration.
*   **Coverage:** 615 tests with 100% coverage on `src/mint_python/`.
*   **Installation:** One-command install via `pip install mint-runtime`.
*   **Showcase:** E2E baseline includes a 260 KB document with cover, TOC, 8 sections, 7 chart types, 3 tables, and images.

**Known Gaps:**
*   Per-run formatting (bold/italic/color/font_size) — currently relies on Style presets.
*   Lists (bulleted/numbered/checklist) as a first-class block.
*   Merged table cells, hyperlinks, bookmarks, and footnotes.
*   Multi-column/landscape sections and per-section headers/footers.

**Future Roadmap:**
The immediate next step is the implementation of a template engine and a visual QA pipeline. This will enable the "docx-as-code" vision, allowing any LLM to generate documents of guaranteed quality with a single `pip install`.

## Epilogue

The journey of MINT began with a simple observation: LLMs are excellent at generating content, but poor at generating valid documents. By building a Model-Independent Normalization Toolkit, we have shifted the burden from the model to the pipeline.

The vision is clear: **Any LLM, any document, guaranteed quality.** With the v0.3.0 release, we have achieved this not just through better prompts, but through a robust, secure, and pure-Python architecture that ensures consistency from the first token to the final byte.