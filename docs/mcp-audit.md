# MINT MCP Servers — Inventory, Gap Analysis, Migration Plan

**Phase**: 13 / Step 2 — design-only deliverable
**Date**: 2026-05-10
**Scope**: `src/mint/mcp_g1.py`, `src/mint/mcp_g2.py`, `src/mint/llm.py` (read-only inputs)
**Status**: brief for Phase-14 migration; not a runtime artifact

This document is the audit of the existing legacy MCP surfaces against the
post-alpha product shape settled in Phase-13 planning (MCP-first, design-tokens
+ governed templates + GRACE audit + elicitation-driven planning dialogs as
the differentiating bundle). Migration code lives in Phase-14, not here.

---

## 1. Tool Inventory

Two FastMCP server instances are registered today, mounted side-by-side in
`src/mint/cli.py::cmd_serve` via `FastMCP("MINT").mount(g1).mount(g2)`. Together
they expose **7 tools**.

| # | Server | Tool | Signature | Behavior | State | Elicitation | Disposition |
|---|---|---|---|---|---|---|---|
| 1 | `MINT-G1` | `mint_validate` | `(document_path: str, severity_mode: str="audit") -> str` | Run M-VALIDATE rule checks; return JSON ValidationReport (passed, total, hard/soft counts, mode, violations array). | stateless | none | **migrate** — keep one-shot semantics; expose severity options via elicitation when caller didn't specify (drop the implicit "audit" default in the new tool) |
| 2 | `MINT-G1` | `mint_fix` | `(document_path: str) -> str` | Run M-FIX auto-fix safe+visual violations; backup; cap at 3 iterations; return JSON FixReport (fixed_path, backup_path, iterations, applied_fixes, remaining_violations, diff). | stateless | none | **migrate** — same shape, but emit a destructive-fix warning via `ctx.elicit` for confirmation before applying any visual fix; keep the one-shot signature |
| 3 | `MINT-G1` | `mint_fingerprint` | `(document_path: str) -> str` | Compute style fingerprint (M-FINGERPRINT) on a docx/pptx; return JSON `{hash, format, xml_sources, drift_status}`. drift_status is currently always `null`. | stateless | none | **migrate** — strictly read-only diagnostic; one-shot remains correct; finish the drift_status implementation as part of the migration |
| 4 | `MINT-G2` | `mint_create` | `(format: str, prompt: str, tier: str="frontier", model_response_override: str\|None=None, template_name: str\|None=None, design_tokens_json: str\|None=None) -> str` | Generate a document end-to-end through M-CREATE; route by tier (frontier/medium = JS sandbox via M-SANDBOX; small = template fill via M-TEMPLATES); validate; return JSON CreateResult (success, output_path, execution_mode, duration_ms, error, validation summary). | stateless **but multi-stage** internally (LLM call → sandbox → validate) | none | **migrate-and-rethink** — the new product shape replaces tier-routing with: (a) elicit missing `template_name` from a curated list when not specified; (b) elicit `design_tokens_json` from preset names; (c) deprecate `model_response_override` (legacy debug knob, not user-facing); (d) deprecate the JS sandbox path entirely (already gated by MINT_ENGINE) |
| 5 | `MINT-G2` | `mint_extract_style` | `(document_path: str) -> str` | M-EXTRACT runs over a source docx/pptx, returns design-tokens.json content. | stateless | none | **kept** — pure read-only; signature stable; will become the seed source for the new `update_template` tool in Phase-14 (extracted tokens become a versioned preset resource) |
| 6 | `MINT-G2` | `mint_list_templates` | `() -> str` | Lists templates from builtin/extracted/custom dirs (M-TEMPLATES). Returns JSON list. | stateless | none | **migrate** — re-shape as MCP `resources/list` (templates exposed as resources, not as a tool's return value). The new shape lets clients enumerate templates without a tool call, and lets the server expose mutation through `update_template` as a separate tool |
| 7 | `MINT-G2` | `mint_edit` | `(document_path: str, edit_plan_json: str, author: str="MINT") -> str` | Apply a typed EditPlan to an existing DOCX (M-EDIT). LLM never sees raw OOXML. PPTX rejected with EDIT_OP_UNSUPPORTED. Returns JSON EditResult (success/ops/diff/validation/duration). | stateless | none | **kept** — strong typed-plan boundary already aligned with the no-OOXML-to-LLM rule. Future work: elicit confirmation before applying destructive ops (delete_paragraph, replace_text on legal documents) |

### Source files referenced

- **`src/mint/mcp_g1.py`** (119 LOC) — registers 3 tools on `FastMCP("MINT-G1")`. Imports `M-VALIDATE`, `M-FIX`, `M-FINGERPRINT`, `M-CONFIG` (via paths.RULES_DIR), `M-SECURITY` (`safe_doc`).
- **`src/mint/mcp_g2.py`** (218 LOC) — registers 4 tools on `FastMCP("MINT-G2")`. Imports `M-CREATE`, `M-EDIT`, `M-EXTRACT`, `M-TEMPLATES`, plus `M-CONFIG` and `M-SECURITY` shared helpers.
- **`src/mint/llm.py`** (159 LOC) — `LLMClient.call(prompt, system) -> LLMResponse`. OpenAI-compatible `/chat/completions` POST via httpx. Used by `M-CREATE` (and by Phase-13 `tools/article_experiment`); not directly reachable as an MCP tool. Important reference because it shows the MCP server already speaks LLM endpoints — but **as a client, not as a server-driven flow**. The new product shape inverts this: the MCP server is the source of templates / presets / plans, and the connected MCP client (Claude Desktop / OpenWebUI) drives the LLM.

---

## 2. State Analysis

All 7 tools are **stateless one-shot endpoints**. Each invocation:
- Receives all required parameters in the call.
- Performs its computation against a file or in-memory result.
- Returns a JSON-encoded string. No session state. No mid-execution prompts. No elicitation.

There is **no shared mutable runtime state** across tools today. The on-disk filesystem (rules dir, templates dir, source documents) is the only shared substrate, and reads are well-isolated by `safe_doc()`.

The closest thing to a multi-stage flow is `mint_create`'s internal pipeline (LLM call → sandbox execute → validate → maybe-fix). That sequencing happens **inside one tool call** — the model and the user don't see the intermediate states. The current design assumes the caller already has the prompt + tier + tokens decided. There's no **planning-mode dialog** anywhere in the surface.

The legacy MCP servers were designed for the M-CREATE/M-EDIT pipeline circa 0.2.0, when the product shape was "give me a document, no questions asked." The Phase-13 product shape is "let's plan a document together, then I'll generate it deterministically" — that's **not what this surface does**.

---

## 3. Gap Analysis

The four differentiators settled in Phase-13 planning, vs current state:

### (a) Design-tokens-as-resources

**Status**: absent.

**Why it matters**: presets (`klawd`, `claret_serif`, `alga_corporate`, `minimal`, `compact`) currently live as YAML/JSON files in `src/mint_python/core/presets/`. They are loadable through `mint_python.core.style.load_preset(name)` in-process, but the MCP surface exposes none of them as resources. A connected client cannot enumerate, fetch, or compare presets — it can only pass `design_tokens_json` as a serialized blob in `mint_create`. That makes "swap brand without regenerating content" impossible across clients: each client has to ship its own copy of the preset list.

**What closing it requires**:
- Expose `presets/` directory entries as MCP `resources` (URI scheme: `mint://preset/{name}`)
- New tool `list_presets() -> list[PresetSummary]` for clients that prefer tools over resources
- New tool `get_preset(name) -> PresetData` returning the full YAML/JSON content
- Integrate with the new MEMO-POC tool: `create_memo` accepts `preset: str = "klawd"` and resolves through this resource layer

**Rough effort**: small — 1-2 new tools + 1 resource handler. Plumbing only; the data is already present in `mint_python.core.style`.

### (b) Versioned templates with cross-model handoff

**Status**: absent.

**Why it matters**: the user's lawyer-on-OpenWebUI scenario depends on this. Frontier model (Claude / GPT-4o) edits a template skeleton in a low-sensitivity context (no client data); local model (qwen / gemma) consumes the improved template in a high-sensitivity context (full client data). Templates need to be a **shared mutable artifact** the MCP server governs — not a static asset bundled with each client. Today there's `mint_list_templates` returning a flat list with no versioning, no edit path, no audit of who-changed-what.

**What closing it requires**:
- Refactor `templates/` into a tracked directory with explicit version semantics (semver in filename, e.g. `memo_v1.yaml`, `memo_v1.1.yaml`, or sidecar `*.version.json`)
- New tool `update_template(name, content, author) -> TemplateVersion` — writes a new version, never overwrites
- New tool `get_template(name, version="latest") -> TemplateContent`
- Authorization shim: who can write vs who can only read (config-driven, not runtime-elaborate)
- Audit-trail integration: every `update_template` call writes a row into the audit log that future `inject_grace` calls embed into produced docs

**Rough effort**: medium. Storage layer + versioning + audit are real engineering. ~3-5 days dev + 1-2 days verification.

### (c) GRACE-as-audit-trail

**Status**: partially shipped.

**Why it matters**: GRACE manifest injection (urn:mint:grace:2026:manifest custom XML part) is already implemented in MP-GRACE; the `Document.inject_grace()` method works in-process. But the MCP tools do NOT currently call it on outputs. `mint_create` produces a docx without a GRACE manifest. `mint_edit` skips it too. The audit-trail story we sell is therefore aspirational, not enforced.

**What closing it requires**:
- `mint_create` and the new `create_memo` tool always inject a GRACE manifest before returning
- Manifest content: caller info (model identity from `ctx` if available, else "anonymous"), elicited fields, source document hashes, timestamp, audit_id (UUID4)
- New tool `read_grace_manifest(document_path) -> Manifest` for clients that want to audit a doc they didn't generate
- Phase-14 also enables manifest *append* on `mint_edit` (current edits should record an "edited via" entry, not overwrite the original audit)

**Rough effort**: small for the inject side (already built); medium for read+append (new code paths in MP-GRACE for non-destructive update).

### (d) Elicitation-driven planning dialogs

**Status**: absent — the entire reason MEMO-POC exists.

**Why it matters**: this is the canonical "wow demo" for the product shape. Today every tool is one-shot — caller fills all params or the tool fails. Real lawyer / clinician / executive workflows are dialogue-driven: "draft a memo about X" → "to whom?" → "Board" → "by when?" → "tomorrow" → here's the docx. MCP's spec-native `elicitation` capability (2025-06-18) is ready and FastMCP supports it. We just don't use it.

**What closing it requires**:
- All new tools (Phase-14 generation tools) use `await ctx.elicit(...)` for missing required fields
- Existing tools that have a clear "missing param → ask" shape (`mint_create` for `template_name` / `tier`) get migrated
- Document the elicitation pattern in the cookbook — including the rejection path (caller declines to provide a value → tool returns a structured error with the rejected field name, no half-built doc emitted)

**Rough effort**: small per tool — `await ctx.elicit` is one line per missing field. Big across the full migration when combined with the new schemas (Memo / Letter / Report / Contract specs).

---

## 4. Migration Plan

Prioritized list with acceptance criteria. Sequencing is **bottom-up**: foundations first, user-facing tools second.

### Priority 1 — GRACE audit-trail enforcement (~2 days)

**Acceptance criteria**:
- All `create_*` tools inject GRACE manifest before returning
- New tool `read_grace_manifest(document_path)` exists and round-trips a manifest written by any other tool
- V-MP-MEMO-POC scenario-7 passes against this layer

**Why first**: it's the cheapest of the four gaps and the one that makes the "audit-ready" pitch real. Until this lands, the rest of the differentiators are story without proof.

### Priority 2 — Design-tokens-as-resources (~1 day)

**Acceptance criteria**:
- `mint://preset/{name}` resources resolvable by Claude Desktop and verified through MCP `resources/list`
- `list_presets()` and `get_preset(name)` tools exist and pass V-MP-MEMO-POC scenario-coverage
- The 5 built-in presets (klawd, claret_serif, alga_corporate, minimal, compact) all enumerable

**Why second**: blocks Memo / Letter / Report doc-types from offering brand-swap UX. Small engineering surface; gates Priority 4.

### Priority 3 — Elicitation migration of existing tools (~2 days)

**Acceptance criteria**:
- `mint_create` — when `template_name` is None, elicit from `list_presets()` enumeration; when `tier` is None, elicit from {small, medium, frontier}; deprecate `model_response_override`
- `mint_fix` — elicit confirmation before applying visual fixes (one toggle per call)
- All tools deprecate `*_override` debug knobs from the public surface

**Why third**: improves existing tools without changing their contract too much. Low-risk migration of behavior, not interfaces.

### Priority 4 — Versioned templates with cross-model handoff (~5 days)

**Acceptance criteria**:
- `templates/` directory layout supports `<name>_v<semver>.yaml` files
- New tools: `list_templates()` (now returns versioned entries), `get_template(name, version)`, `update_template(name, content, author)`
- Audit-log writes captured in a sidecar log; `read_grace_manifest` surfaces the version+author of the template used
- Authorization shim: read = open; write = config-gated allowlist
- Cross-model demo: frontier model edits `memo_v1.0` → `memo_v1.1`; local model uses `memo_v1.1` for sensitive memo; audit shows the lineage

**Why fourth**: most engineering, most ambitious, and the **whole point** of the product shape. Lands once Priorities 1-3 are in.

### Breaking changes

- `mint_create.model_response_override` removed (was a legacy debug input; never user-facing)
- `mint_create.tier` becomes optional (was effectively required for non-trivial output; now elicited)
- `mint_list_templates` may be deprecated in favor of MCP `resources/list` after Priority 2; the tool remains as a redirect for clients that prefer tool calls

### Phase-14 sequencing

```
Phase-14, Wave 1 — Priority 1 (GRACE) + Priority 2 (presets-as-resources)
Phase-14, Wave 2 — Priority 3 (elicitation migration of mint_create / mint_fix)
Phase-14, Wave 3 — Priority 4 (versioned templates)
Phase-14, closeout — manual cross-model handoff demo (frontier → local) recorded as evidence
```

---

## 5. New-Tool Roadmap

Concrete signatures matching the FastMCP + elicitation pattern (`async def`, `ctx: Context`, `await ctx.elicit(...)`). All new tools follow the MEMO-POC template.

```python
@mcp.tool
async def list_presets(ctx: Context) -> list[dict]:
    """Return enumerated presets with name, description, primary color."""

@mcp.tool
async def get_preset(name: str, ctx: Context) -> dict:
    """Return the full preset YAML as a dict; elicit `name` from list_presets if missing."""

@mcp.tool
async def list_templates(ctx: Context) -> list[dict]:
    """Return enumerated templates with name, version, last_modified, author."""

@mcp.tool
async def get_template(name: str, version: str = "latest", ctx: Context = ...) -> dict:
    """Return the template YAML as a dict; elicit `name` if absent."""

@mcp.tool
async def update_template(name: str, content: str, author: str | None = None, ctx: Context = ...) -> dict:
    """Write a new version of a template; elicit `author` if not provided.
    Returns: {name, version, audit_id, predecessor_version}."""

@mcp.tool
async def read_grace_manifest(document_path: str, ctx: Context) -> dict:
    """Read the urn:mint:grace:2026:manifest part from a document."""

@mcp.tool
async def create_memo(intent: str, source_md: str | None = None, ctx: Context = ...) -> dict:
    """Generate a Memo via planning dialog. Canonical example. (Implemented in Phase-13 step-3 — MP-MEMO-POC.)
    Returns: {path, audit_id, fields_elicited}."""
```

The MEMO-POC `create_memo` is the **canonical example** Phase-14 tools follow. Every doc-type generator (`create_letter`, `create_report`, `create_contract`) inherits the same shape: parse intent → fill heuristic → elicit missing → assemble through MP-DOCUMENT + selected preset → inject GRACE → return path + audit_id + fields_elicited.

---

## 6. Honest Assessment

### Tools to deprecate

- **`mint_create.model_response_override`** — legacy debug input. Used only in test fixtures; never reached by an MCP client in practice. Remove in Phase-14 Wave 1; tests migrate to direct in-process calls.
- **JS sandbox path inside `mint_create`** (tier=frontier|medium routing through M-SANDBOX) — already gated by `MINT_ENGINE` env var. The pure-python successor (Phase-7..12) is the strategic default. Phase-14 Wave 1 also writes a deprecation note in the M-CREATE module contract; Wave 3 removes the JS path entirely once cross-model templates make a Python-only generator viable for all tiers.

### Tools to keep as-is

- **`mint_extract_style`** — the cleanest tool in the inventory. Pure read-only, well-bounded, becomes the seed for `update_template` workflow.
- **`mint_edit`** — the typed-plan boundary already aligns with the no-OOXML-to-LLM rule. Future enhancements (per-op confirmation via elicit) are additive, not breaking.
- **`mint_fingerprint`** — strictly diagnostic; finishing the `drift_status` field is the only outstanding work, not a re-design.

### What cannot be salvaged

Nothing. The legacy 7-tool surface is structurally healthy — every tool maps cleanly to a Phase-14 destination either as-is, with elicitation added, or as a renamed-and-rescoped successor. There is **no tool whose semantics fundamentally conflict with the new product shape** that requires deletion. The migration is additive + refactor, not a rewrite.

---

## Appendix: Audit checklist (V-MP-MCP-AUDIT scenarios)

- [x] **scenario-1** — Inventory has one row per @mcp.tool found in mcp_g1 + mcp_g2 (7 rows).
- [x] **scenario-2** — Gap analysis covers the four differentiators (a/b/c/d above).
- [x] **scenario-3** — Migration plan has 4 priorities with acceptance criteria + Phase-14 sequencing.
- [x] **scenario-4** — Roadmap signatures match FastMCP elicitation pattern; create_memo is the canonical example.
- [x] **scenario-5** — Honest assessment lists deprecations with rationale; explicitly notes nothing is fully unsalvageable.
- [x] **All sections present** — 6 H2 headings + this appendix.

This brief is now the input to Phase-14 planning. No code in this audit; the migration is its own phase.
