# GRACE Framework - Project Engineering Protocol

## Keywords
docx, pptx, xlsx, OOXML, docx-js, pptxgenjs, exceljs, validation, auto-fix, QA, GRACE, MCP, template-engine, style-extraction, design-tokens, model-agnostic, local-first

## Annotation
MINT Runtime — Model-Independent Normalization Toolkit. Claude-quality document generation for any LLM via skill prompts, sandboxed execution, deterministic validation, visual QA, and GRACE metadata injection.

## Core Principles

### 1. Never Write Code Without a Contract
Before generating or editing any module, create or update its MODULE_CONTRACT with PURPOSE, SCOPE, INPUTS, and OUTPUTS. The contract is the source of truth. Code implements the contract, not the other way around.

### 2. Semantic Markup Is Load-Bearing Structure
Markers like `// START_BLOCK_<NAME>` and `// END_BLOCK_<NAME>` are navigation anchors, not documentation. They must be:
- uniquely named
- paired
- proportionally sized so one block fits inside an LLM working window

### 3. Knowledge Graph Is Always Current
`docs/knowledge-graph.xml` is the project map. When you add a module, move a module, rename exports, or add dependencies, update the graph so future agents can navigate deterministically.

### 4. Verification Is a First-Class Artifact
Testing, traces, and log anchors are designed before large execution waves. `docs/verification-plan.xml` is part of the architecture, not an afterthought. Logs are evidence. Tests are executable contracts.

### 5. Top-Down Synthesis
Code generation follows:
`RequirementsAnalysis -> TechnologyStack -> DevelopmentPlan -> VerificationPlan -> Code + Tests`

Never jump straight to code when requirements, architecture, or verification intent are still unclear.

### 6. Governed Autonomy
Agents have freedom in HOW to implement, but not in WHAT to build. Contracts, plans, graph references, and verification requirements define the allowed space.

## Semantic Markup Reference

### Module Level
```
# FILE: path/to/file.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: [What this module does - one sentence]
#   SCOPE: [What operations are included]
#   DEPENDS: [List of module dependencies]
#   LINKS: [Knowledge graph references]
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   exported_symbol - one-line description
# END_MODULE_MAP
```

### Function or Component Level
```
# START_CONTRACT: function_name
#   PURPOSE: [What it does]
#   INPUTS: { param_name: Type - description }
#   OUTPUTS: { ReturnType - description }
#   SIDE_EFFECTS: [External state changes or "none"]
#   LINKS: [Related modules/functions]
# END_CONTRACT: function_name
```

### Code Block Level
```
# START_BLOCK_VALIDATE_INPUT
# ... code ...
# END_BLOCK_VALIDATE_INPUT
```

### Change Tracking
```
# START_CHANGE_SUMMARY
#   LAST_CHANGE: [v1.2.0 - What changed and why]
# END_CHANGE_SUMMARY
```

## Logging and Trace Convention

All important logs must point back to semantic blocks:
```python
logger.info(f"[{module}][{function}][{block}] message", extra={
    "correlation_id": cid,
    "stable_field": value,
})
```

Rules:
- prefer structured fields over prose-heavy log lines
- redact secrets and high-risk payloads
- treat missing log anchors on critical branches as a verification defect
- update tests when log markers change intentionally

## Verification Conventions

`docs/verification-plan.xml` is the project-wide verification contract. Keep it current when module scope, test files, commands, critical log markers, or gate expectations change.

Testing rules:
- deterministic assertions first
- trace or log assertions when trajectory matters
- test files may also carry MODULE_CONTRACT, MODULE_MAP, semantic blocks, and CHANGE_SUMMARY when they are substantial
- module-local tests should stay close to the module they verify
- wave-level and phase-level checks should be explicit in the verification plan

## File Structure
```
docs/
  requirements.xml       - Product requirements and use cases
  technology.xml         - Stack decisions, tooling, observability, testing
  development-plan.xml   - Modules, phases, data flows, ownership, write scopes
  verification-plan.xml  - Test strategy, trace expectations, module and phase gates
  knowledge-graph.xml    - Project-wide navigation graph
src/
  mint/
    config.py            - Configuration
    sandbox/             - Node.js execution sandbox
    rules/               - Validation rule definitions
    validate.py          - Validation engine
    fix.py               - Auto-fix engine
    fingerprint.py       - StyleFingerprint hash
    skills/              - Skill prompts per tier/format
    templates/           - Template engine
    extract.py           - Style extraction
    create.py            - Create orchestrator
    qa/                  - QA pipeline (L1 + L2)
    grace/               - GRACE metadata injection
    mcp_g1.py            - MCP tools: validate, fix, fingerprint
    mcp_g2.py            - MCP tools: create, extract_style, list_templates
tests/
  unit/                  - Unit tests per module
  integration/           - Integration and end-to-end tests
skills/                  - Skill prompt Markdown files
templates/               - Builtin, extracted, custom document templates
rules/                   - YAML rule definitions
```

## Documentation Artifacts - Unique Tag Convention

In `docs/*.xml`, repeated entities must use their unique ID as the XML tag name instead of a generic tag with an `ID` attribute. This reduces closing-tag ambiguity and gives LLMs stronger anchors.

### Tag naming conventions

| Entity type | Anti-pattern | Correct (unique tags) |
|---|---|---|
| Module | `<Module ID="M-CONFIG">...</Module>` | `<M-CONFIG NAME="Config" TYPE="UTILITY">...</M-CONFIG>` |
| Verification module | `<Verification ID="V-M-AUTH">...</Verification>` | `<V-M-AUTH MODULE="M-AUTH">...</V-M-AUTH>` |
| Phase | `<Phase number="1">...</Phase>` | `<Phase-1 name="Foundation">...</Phase-1>` |
| Flow | `<Flow ID="DF-SEARCH">...</Flow>` | `<DF-SEARCH NAME="...">...</DF-SEARCH>` |
| Use case | `<UseCase ID="UC-001">...</UseCase>` | `<UC-001>...</UC-001>` |
| Step | `<step order="1">...</step>` | `<step-1>...</step-1>` |
| Export | `<export name="config" .../>` | `<export-config .../>` |
| Function | `<function name="search" .../>` | `<fn-search .../>` |
| Type | `<type name="SearchResult" .../>` | `<type-SearchResult .../>` |
| Class | `<class name="Error" .../>` | `<class-Error .../>` |

### What NOT to change
- `CrossLink` tags stay self-closing
- single-use structural wrappers like `<contract>`, `<inputs>`, `<outputs>`, `<annotations>`, `<test-files>`, `<module-checks>`, and `<phase-gates>` stay generic
- code-level markup already uses unique names and stays as-is

## Rules for Modifications

1. Read the MODULE_CONTRACT before editing any file.
2. After editing source or test files, update MODULE_MAP if exports or helper surfaces changed.
3. After adding or removing modules, update `docs/knowledge-graph.xml`.
4. After changing test files, commands, critical scenarios, or log markers, update `docs/verification-plan.xml`.
5. After fixing bugs, add a CHANGE_SUMMARY entry and strengthen nearby verification if the old evidence was weak.
6. Never remove semantic markup anchors unless the structure is intentionally replaced with better anchors.

## Engineering Workflow (from ai-standards 1.6.0)

### Reasoning Hygiene
- For complex or ambiguous tasks, structure the work step by step instead of jumping straight to the answer.
- Make assumptions, edge cases, and verification points explicit when they affect correctness.
- Prefer self-review in the form of gaps, risks, and missing evidence over vague confidence claims.
- Concentrate extra reasoning at points of local uncertainty or correctness risk.
- Treat index math, assignments, returns, state transitions, error paths, and cross-boundary calls as default high-attention zones.

### Autonomy Boundaries
- Treat long autonomous execution as an exception, not the default mode.
- Allow long autonomous execution only when design is chosen, scope is bounded, verification is strong, and rollback is cheap.
- Stop and request review when correctness depends on hidden reasoning that cannot be summarized compactly.

### Agent Usage Hygiene
- Prefer targeted discovery through search, diffs, logs, and focused file reads before loading broad context.
- Keep task scope narrow enough that the next patch remains reviewable and verifiable.
- Use the most targeted verification that still proves the change; do not skip required verification to save usage.
- If economy conflicts with correctness, safety, or required verification, prioritize correctness.

### Session Hygiene
- Warn when a long thread increases the risk of context drift, stale assumptions, or lost constraints.
- Before continuing a long session, produce a compact handoff summary with current goal, decisions, touched files, risks, and next slice.
- Do not rely on transient chat memory for critical constraints; move them into project artifacts.

### Error Handling
- Functions must either return a valid result or raise an exception with actionable context.
- Never swallow exceptions. Never return None/empty/magic to hide an error.
- Use `Optional[T]` only for legitimate absence of a value, not for error signaling.

### Architecture & Layering
- Service layers contain business logic only.
- Database access must stay behind repository-style abstractions.
- Service layers must not call external protocols directly.

### Python Preferences
- Use type hints everywhere (params + returns).
- Prefer dataclasses or typed models for structured return values.
- Use module-level constants for repeated thresholds and stable literals.
- Prefer composition over inheritance.
- Keep side effects at the edges; prefer pure, testable functions.

### Git Workflow
- Never create commits on protected branches without explicit user authorization.
- Always ask the user to approve the commit message text before committing.
- Commit messages: `task_id. (commit_type) message.`
