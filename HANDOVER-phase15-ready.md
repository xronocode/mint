# Handover — Phase-15 ready to plan

Written 2026-05-10 at the close of the Phase-14 session. Next session
picks up here. The repo's `MEMORY.md` already loads automatically — this
file covers only what's NOT in memory: the in-flight Phase-15 framing,
the discoveries made this session, and the concrete first action.

## Where we are

- **Version**: `0.4.0a3` (tag `v0.4.0a3` pushed 2026-05-10), CI green.
- **Phase-14**: closed. All four waves shipped + cross-model handoff
  smoke verified live (V-MP-TEMPLATES-WRITE scenario-5). All four
  `mcp-audit.md` differentiators (Priority-1 through Priority-4)
  flipped from "absent" to "shipped".
- **Branding**: every public MCP tool carries `mint_` prefix
  (`mint_create_document`, `mint_list_templates`, etc.); FastMCP
  server identity is `MINT`.
- **Subagent Delegation Protocol**: codified in `AGENTS.md`. Triggers
  when to delegate vs. inline; audit trail = git + GRACE artifacts;
  no `docs/subagent/` directories.
- **Tests**: 881 pass, 100% coverage on `src/mint_python`, ruff +
  mypy clean.
- **Live state**: 7 MCP tools registered; `mint://template/{name}` +
  `mint://preset/{name}` resource templates active; all wired into
  the shared FastMCP server via deferred side-effect imports at the
  tail of `src/mint_python/mcp/document.py`.

## Phase-15 brief — production hardening (Option A)

Selected by the user 2026-05-10 over scope-expansion (Option B). The
goal: "можно показать клиенту O!Bank, не страшно". Phase-14 made the
product MCP-first; Phase-15 makes it safe to demo externally.

### Modules in priority order

1. **MP-AUTH-SHIM** (HIGH — production blocker)
   - `update_template` is currently writable by any connected MCP
     client. After Phase-14, that includes any local model attached
     via Cursor / OpenWebUI / etc. — a stray sidecar can clobber the
     governed templates.
   - Fix: read = open, write = config-gated allowlist. Allowlist
     source: env var `MINT_TEMPLATE_WRITERS` (comma-separated author
     identities) and/or `~/.config/mint/writers.json`. On
     `update_template` invocation, if the author isn't on the
     allowlist → raise `TEMPLATE_WRITE_FORBIDDEN`.
   - Same shim should also gate any future write tools.
   - Estimate: ~80 LOC + tests + config docs.

2. **MP-MANIFEST-READ** (MEDIUM — audit P1 acceptance closure)
   - Symmetric counterpart to GRACE manifest writing. We currently
     inject `urn:mint:grace:2026:manifest` into every produced docx
     but don't expose a tool to read it back. The audit's P1
     acceptance criteria explicitly named `read_grace_manifest`.
   - New `@server.tool mint_read_grace_manifest(document_path)` that
     opens the docx zip, locates `grace/manifest_*.xml`, parses, and
     returns the instructions list as a dict.
   - Useful for "verify provenance of this docx I received" UX —
     plus closes the acceptance criteria gap.
   - Estimate: ~50 LOC + tests.

3. **MP-LEGACY-DEPRECATION** (MEDIUM — tech debt)
   - `mcp_g1.py` + `mcp_g2.py` legacy tools (`mint_create`,
     `mint_fix`, `mint_validate`, `mint_extract_style`,
     `mint_fingerprint`) still exist with the JS sandbox path gated
     by `MINT_ENGINE`. The audit's honest assessment promised
     deprecation notes in Wave 1 + removal in Wave 3 — we did
     neither during Phase-14.
   - Decide: **either** deprecate the JS path entirely (memory:
     pure-Python is strategic default; v2-rewrite memory is the
     authoritative direction) **or** unify mcp_g1/g2 onto the MINT
     server with `mint_` prefix and elicitation per audit P3.
   - Likely path: deprecate JS sandbox + drop `mcp_g1`/`mcp_g2`
     tools entirely; `mint_create` semantics replaced by
     `mint_create_document`. The legacy tools never had production
     consumers (only test fixtures).
   - Estimate: ~200-300 LOC removal + test migration.

4. **MP-VISUAL-QA-HOOK** (MEDIUM — quality gate)
   - After every successful `create_document`, run produced docx
     through the existing klawd visual-QA checker. Currently we
     validate structure (MP-VALIDATE) but don't visually compare
     against preset's reference.
   - Optional flag (defaults on); `MINT_SKIP_VISUAL_QA=1` to skip.
   - Estimate: ~100 LOC + tests if reused; more if a new comparator
     is needed.

### Out of scope for Phase-15

- More doc_types (report.yaml, contract.yaml, invoice.yaml) — Option
  B territory. Zero Python required when added; can ship anytime as
  one-line PRs.
- CHANGELOG.md — small, can land outside the phase if convenient.
- Resource `list_changed` notifications — UX polish, not hardening.

## Immediate next action

Invoke the `grace-plan` skill to formalize Phase-15 in artifacts.
Suggested skill arguments:

```
Add Phase-15 (production hardening) to the development plan with four
modules in priority order: MP-AUTH-SHIM (write-path allowlist for
update_template), MP-MANIFEST-READ (mint_read_grace_manifest tool
closing audit P1 acceptance), MP-LEGACY-DEPRECATION (deprecate or
remove mcp_g1/mcp_g2 surface; specifically the JS sandbox path in
mint_create), MP-VISUAL-QA-HOOK (klawd visual comparison after
create_document). Brief is in HANDOVER-phase15-ready.md. Cross-link
each module to the originating mcp-audit.md priority where applicable.
```

That writes Phase-15 entries into `docs/development-plan.xml`,
`docs/verification-plan.xml`, `docs/knowledge-graph.xml`. After the
plan lands the user approves and the next session can immediately
`grace-execute` step-by-step.

## Discoveries from this session worth carrying forward

These are NOT in MEMORY.md yet because they came up at the end. The
next session should consider whether to add them as feedback memories.

1. **Tool registrations need explicit imports.** FastMCP's
   `@server.tool` only fires when the module containing it is
   imported. The Phase-13 `claude_desktop_config.json` loads
   `mint_python.mcp.memo` directly; the module-tail
   `from mint_python.templates import registry as _registry` and
   `from mint_python.mcp import resources as _resources` in
   `document.py` are load-bearing — they trigger Phase-14 W2/W3/W4
   surface registration. **Don't remove those imports.** A future
   refactor that reorders or extracts them will silently break the
   live MCP surface (tests still pass because tests import the
   modules directly).

2. **Heuristic accepts inline labelled keys.** As of this session
   (commit fixing #1/#2/#3), `_heuristic_extract` handles intents
   where labels are separated by `". "` not just newlines. Smoke
   2026-05-10 surfaced this — Claude naturally writes inline. The
   `_INLINE_LABEL_SPLIT_RE` pre-normalize is the relevant code.

3. **Template `kind: callout` works with both vocab forms.** The
   comment-documented vocab (`kind_of`/`body`/`title`) and Claude's
   natural output (`type`/`text`/`label`) both render. Field-name
   leniency is intentional — don't tighten without good reason.

4. **`{{ name | default: "..." }}` filter is supported.** Both
   `"..."` and `'...'` delimiters. Used by Claude's authored memo
   v1.1+.

5. **Tool result format is imperative two-line.** `**Open:**` line
   with markdown link + `**File path:**` line with raw URI. Plus
   `create_document` docstring contains a verbatim-relay directive.
   Tested with the assertion that the docstring contains "verbatim".
   If you change the format, keep the URI on its own line and keep
   the directive — Claude Desktop's model paraphrases otherwise.

6. **Tests path-isolate `_TEMPLATES_DIR`.** `test_mp_doc_generic.py`
   has an autouse `_isolate_templates_dir` fixture that snapshots the
   canonical baselines into tmp_path so tests are robust to local
   `update_template`-authored siblings. Apply the same pattern in any
   future tests that assert on template content.

## Open issues

GitHub issues #1, #2, #3 all closed. No open issues at the time of
this writing. Phase-15 modules will create their own issues as the
work proceeds, per the protocol.

## File-level state

Modified during this session and now committed/pushed:

- `pyproject.toml` — version 0.4.0a3
- `AGENTS.md` — Subagent Delegation Protocol section
- `src/mint_python/mcp/document.py` — heuristic fix, callout handler,
  default-filter, tool-result format, deferred surface imports,
  `mint_*` decorator names, server name `MINT`
- `src/mint_python/mcp/memo.py` — `mint_create_memo` decorator name
- `src/mint_python/mcp/resources.py` — `mint_list_presets`,
  `mint_get_preset` decorator names + brand guard tests
- `src/mint_python/templates/registry.py` — `mint_list_templates`,
  `mint_get_template`, `mint_update_template` decorator names
- `tests/integration/test_mp_*.py` — corresponding test additions and
  the `_isolate_templates_dir` fixture in `test_mp_doc_generic.py`
- `docs/cross-model-handoff-smoke.md` — manual smoke procedure (W3)
- `docs/development-plan.xml`, `docs/verification-plan.xml`,
  `docs/knowledge-graph.xml` — Phase-14 marked done; all four
  V-stubs closed; V-MP-TEMPLATES-WRITE scenario-5 marked done with
  the live smoke evidence inline.

Local artifacts NOT in git (gitignored, regenerable):

- `templates/_audit.jsonl` — was cleaned 2026-05-10 after smoke.
  Will regenerate on next `update_template` call.
- `templates/memo_v*.yaml` siblings — also cleaned. Repo has only
  the canonical `memo.yaml` and `letter.yaml`.

Last commit on main: the `0.4.0a3` version bump (tag `v0.4.0a3`).
