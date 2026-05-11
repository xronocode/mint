# Handover — Phase-16 ready to plan

Written 2026-05-11 at the close of the Phase-15 session. Next session
picks up here. The repo's `MEMORY.md` already loads automatically; the
Phase-15 brief is now archived at `docs/archive/HANDOVER-phase15-ready.md`.
This file covers only what's NOT in memory and NOT already in the prior
handover: the Phase-15 outcome, what changed in the surface area, the
discoveries that should shape Phase-16 framing, and the candidate next
phases.

## Where we are

- **Version**: `0.4.0a4` (bumped this session). Not yet tagged.
- **Phase-15**: closed 2026-05-10. All 4 modules shipped + Gate-Phase-15
  passed. The MCP server is now exclusively at `src/mint_python/mcp/document.py`.
- **Tests**: 861 pass, 6 skip; 100% coverage maintained on `src/mint_python`;
  ruff + mypy clean; `uv build` clean.
- **Live MCP surface** (8 tools, all `mint_` prefixed):
  - `mint_create_document`, `mint_create_memo` (alias)
  - `mint_list_templates`, `mint_get_template`, `mint_update_template`
  - `mint_list_presets`, `mint_get_preset`
  - `mint_read_grace_manifest` (new in Phase-15)
- **Live resource templates**: `mint://template/{name}`, `mint://preset/{name}`.
- **What write tools are gated**: `mint_update_template` now requires the
  author to be on the allowlist (env `MINT_TEMPLATE_WRITERS` or
  `~/.config/mint/writers.json`). Empty config = open mode with a one-shot
  warning. Read tools stay open.
- **What every produced docx now carries**: a GRACE manifest (since Phase-14)
  plus an advisory visual-QA score in the tool result's `structured_content`
  (since Phase-15). The visual-QA hook is off via `MINT_SKIP_VISUAL_QA=1`.

## What got deleted in Phase-15 W3

The legacy `src/mint/` JS-tier surface is gone:

- `src/mint/mcp_g1.py` + `src/mint/mcp_g2.py` (the legacy `MINT-G1` /
  `MINT-G2` MCP server registrations — 7 tools that registered on a
  side-mounted FastMCP instance)
- `src/mint/sandbox/` (recursive — Node.js vm2/isolated-vm execution path)
- `src/mint/create.py` (~120 LOC — legacy `M-CREATE` orchestrator)
- `src/mint/assemble.py` (~700 LOC — legacy `M-ASSEMBLE` modular generator)
- 6 test files targeting the deleted modules + `tests/unit/test_engine_flag.py`
  (V-MP-FLAG retired since the verified feature no longer exists)
- `src/mint/cli.py` lost `cmd_serve` + `cmd_create` + `--engine` flag +
  `_select_engine` chokepoint
- `src/mint/config.py` lost the `Engine` StrEnum + `MintConfig.engine`
  field + `MINT_ENGINE` env parsing

What's still in `src/mint/` (the legacy Python-side modules that NOT all
got JS-tier-deleted, and remain in active use): `validate.py`, `fix.py`,
`fingerprint.py`, `extract.py`, `edit.py`, `_security.py`, `_xml_ns.py`,
`paths.py`, `theme.py`, `theme_extract.py`, `llm.py`, `skills.py`,
`templates.py`, `qa.py`, `grace.py`, `rules*.py`, `section.py`, `plan.py`,
`ooxml*.py`, `cli.py`, `config.py`. These remain because mint_python
doesn't have full MCP-tool parity for all of them yet.

## Subagent dispatch protocol — empirical notes from this session

The Phase-15 multi-agent run validated the controller-pre-flight pattern
documented in `docs/verification-plan.xml` `SwarmExecutionReadiness/target-15`.
What worked:

- **Wave-15-1** parallel-dispatch of MP-AUTH-SHIM ‖ MP-MANIFEST-READ
  succeeded cleanly. Disjoint write scopes + a controller pre-flight
  commit that provisioned the shared fixtures (`clean_writers_config`,
  `zip_byte_snapshot`, `tempdir_snapshot`, `backend_probe_patcher`) +
  re-exported them through the integration conftest meant both workers
  could run cold without redefining fixtures locally. **Worth repeating**.
- **Wave-15-2** single-worker MP-VISUAL-QA-HOOK succeeded. The
  `tests/_helpers/fake_vlm.py` pre-flight artifact (4 response variants
  + recorder for caller-input introspection) was load-bearing; without it
  the worker would have invented its own VLM mocking and likely missed
  the `inv-7 NO-CALLER-INPUT-IN-VLM-PAYLOAD` security invariant.
- **Wave-15-3** single-worker SWARM-FORBIDDEN deletion succeeded after
  one rate-limit interruption (resumed in-place with no state loss). The
  worker made the right calls on every contract clarification (drop
  `--engine` arg entirely, retire Engine enum wholesale, delete
  `test_engine_flag.py` rather than migrate). Bigger destructive waves
  benefit from the controller writing the gate-test FIRST so the worker
  builds against an executable target.

What surprised:

- The W3 worker made an out-of-brief edit to `tests/unit/conftest.py`
  (`mp_minimal_config` had to drop `engine=Engine.PYTHON`). Mechanically
  unavoidable — without it the test gate fails at collection. Worth
  pre-flagging in future destructive-deletion briefs: enumerate every
  controller-owned fixture that imports a symbol slated for deletion,
  and either edit those upfront or explicitly bless the worker to do so.
- The `BLOCK_INJECT_MANIFEST` vs `BLOCK_INJECT_GRACE` marker name
  discrepancy in VF-019 / DF-019 was a planning-side bug, not a worker-side
  bug. Worker correctly used the real marker (`BLOCK_INJECT_GRACE` from
  `document.py:910`) and flagged the discrepancy in the result packet so
  the controller could fix verification artifacts post-wave. The lesson:
  **when planning trace contracts, grep the actual log line first**;
  don't compose marker names from intent.

## Phase-16 candidates — pick one path

The Phase-15 closeout exposed several roads. None are pre-committed.
The user should pick what to plan next.

### Option A — MCP-tool parity for the remaining legacy Python surface

Migrate `mint.validate`, `mint.fix`, `mint.fingerprint`, `mint.extract`,
`mint.edit` into the `mint_python.mcp` namespace as proper `mint_*`
tools. Today the pure-python successors exist (`mint_python.validate`,
`mint_python.fix`) but only `mint_create_document` / `mint_read_grace_manifest`
are exposed via MCP. The legacy `src/mint/cli.py` still exposes them as
CLI subcommands, but no MCP client reaches them. Closing this gap means
external clients (Claude Desktop, Cursor) can validate/fix/fingerprint
foreign documents through the same governed surface that produces them.
Scope: ~5 new `@server.tool` wrappers + tests; medium effort.

### Option B — More doc_types (zero-Python)

Phase-14 W2 made adding a doc_type a YAML-only operation (`templates/letter.yaml`
landed there). Phase-16 could add `report.yaml`, `contract.yaml`,
`invoice.yaml`, `decision-record.yaml`, etc. — each is a YAML file with
no Python changes. Pure content work; ships fast; populates the "MINT
generates real document types" story for demo. Worth doing if the
O!Bank demo needs a specific doc_type that memo + letter don't cover.

### Option C — GRACE manifest append semantics (Phase-14 W3 follow-up)

Surfaced during Wave-15-1: `MP-GRACE.bootstrap` overwrites the manifest
on second call. The Phase-14 W3 design contemplated *appending* manifest
parts for cross-model handoff lineage — that's why `_read_all_manifests`
in `MP-MANIFEST-READ` walks multiple parts and picks the most recent.
Today the multi-manifest test fixture had to plant the second part
manually via raw zipfile append. Real cross-model handoff in production
needs append semantics. Scope: edit `MP-GRACE.bootstrap` to detect
existing manifest parts + add (not replace) + bump scenario coverage.
Small (~50 LOC) but touches a security-sensitive path (GRACE is the
audit-trail mechanism).

### Option D — Phase-10 unblock (RestrictedPython sandbox)

Skipped in 2026-05-09 because Phase-11 (GRACE) was prioritized. Now that
Phase-15 ships, the deferred MP-EXEC-{SMALL,MEDIUM,FRONTIER} +
RestrictedPython sandbox + VF-012 PurePythonRegression are unblocked.
The motivation: today `create_document` can only build documents from
templates + heuristic extraction. A sandboxed Python execution tier
would let a frontier LLM emit Python code that produces the document
(parallel to the deleted JS-tier path, but with RestrictedPython instead
of Node.js vm2). Big scope; only worth doing if there's a concrete use
case where templates don't cover the layout space.

### Option E — Security review pass on LLM call sites

VF-019 `inv-7 NO-CALLER-INPUT-IN-VLM-PAYLOAD` caught a real leak in the
original `e2e_qa_visual.py` (doc_title from core.xml interpolated into
the prompt — derived from caller intent). The library refactor removed
it, but other LLM call sites in the repo (e.g. `src/mint/llm.py` legacy
client; any future `create_document` paths that call out) deserve the
same audit. Scope: small if narrow audit; medium if it expands into a
broader prompt-injection / data-exfiltration review.

### Option F — Documentation + CHANGELOG + 0.4.0 release prep

The codebase is at `0.4.0a4` alpha. If the strategic story is "the
MCP-first product shape is shipped and demoable", Phase-16 could be the
*non-engineering* work: README rewrite, OPENING.md user-facing docs,
CHANGELOG.md (Phase-13 onward), CONTRIBUTING.md, the `0.4.0` release
tag itself + GitHub release notes, a screencast of the cross-model
handoff demo. Light on code; heavy on storytelling. Right call if the
goal is "make Phase-15 visible to people outside the project".

## Immediate next action

Either tag `0.4.0a4` now (it's already bumped in `pyproject.toml` but
not committed in a tag) and decide Phase-16 framing fresh, OR roll the
0.4.0a4 bump into the same commit that opens Phase-16's first artifact.

If the user wants to keep momentum on production-hardening lessons:
**Option A** (MCP-tool parity) is the most direct continuation — it
completes the "MCP-first surface" promise that Phase-13 opened and
Phase-14/15 partially shipped. Suggest it as the default unless the
user has a stronger preference.

If the user wants to flip toward go-to-market: **Option F** (docs +
release).

Other options sit in the middle of those poles.

## Discoveries worth carrying forward

Three things the next session should know that aren't in `MEMORY.md`:

1. **`MP-GRACE.bootstrap` overwrites, doesn't append**. Wave-15-1
   `MP-MANIFEST-READ` worker discovered this. Multi-manifest test fixtures
   currently use raw `zipfile.ZipFile(..., 'a')` to plant additional parts.
   Production cross-model handoff scenarios (Phase-14 W3 demo) work only
   because the worked-around-by-test path matches the production single-bootstrap
   path. If real cross-model handoff with lineage tracking is wanted, append
   semantics need to land — see Option C above.

2. **VF planning should grep before composing marker names**. The VF-019
   `BLOCK_INJECT_MANIFEST` reference was wrong; the real marker the hook
   integration follows is `BLOCK_INJECT_GRACE` in `document.py:910`.
   `BLOCK_INJECT_MANIFEST` exists too, but at an inner layer
   (`mint_python/grace/__init__.py:118`). Both fire in caplog, in
   sequence. Future VF authoring should grep the source for the exact
   marker the integration point uses, not compose from intent.

3. **Controller-owned fixture edits during destructive waves**. When a
   wave deletes a symbol that controller-owned fixtures import (W3's
   Engine retirement broke `mp_minimal_config`), the worker has no
   in-bounds way to ship without touching the controller fixture. Future
   destructive-wave briefs should either pre-edit the controller fixture
   in pre-flight (controller-side) OR explicitly bless the worker to
   make the mechanical edit in-place. The W3 packet got this wrong by
   forbidding the fixture edit; the worker correctly broke the rule and
   reported. Re-write the relevant section of the dispatch template.

## Open issues

GitHub issues #1-3 closed at Phase-14 close; no new GitHub issues opened
during Phase-15. Surface-level follow-ups from this session live in the
Phase-16 candidates list above and in the three discoveries section.

## File-level state

Modified during this session and committed (NOT pushed):

- `pyproject.toml` — version `0.4.0a3` → `0.4.0a4` (this commit)
- `docs/development-plan.xml` — Phase-15 + all 4 modules marked done;
  DF-017 / DF-018 / DF-019 + Phase-15 ImplementationOrder block
- `docs/verification-plan.xml` — 4 new V-MP-* entries promoted to passing;
  VF-017 / VF-018 / VF-019 added + activated; V-MP-FLAG retired;
  Gate-Phase-15 done; 10 new FailurePacket examples
- `docs/knowledge-graph.xml` — 4 new MP-* modules + 7 CrossLinks
- `src/mint_python/mcp/auth.py` — NEW (MP-AUTH-SHIM, ec44ef6)
- `src/mint_python/mcp/manifest.py` — NEW (MP-MANIFEST-READ, 674a873)
- `src/mint_python/mcp/document.py` — hook integration + module-tail
  manifest import (Wave-15-2 + Wave-15-1 respectively)
- `src/mint_python/templates/registry.py` — `update_template` now calls
  `require_template_writer` before disk I/O
- `src/mint_python/qa/__init__.py` + `src/mint_python/qa/visual.py` —
  NEW (MP-VISUAL-QA-HOOK, 653896a)
- `e2e_qa_visual.py` — rewritten as 56-LOC CLI shim
- `tests/_helpers/fake_vlm.py` — NEW (controller pre-flight artifact)
- `tests/unit/conftest.py` + `tests/integration/conftest.py` — 4 new
  fixtures + Engine import removal
- `tests/integration/test_mp_{auth_shim,manifest_read,visual_qa_hook}.py`
  — NEW (V-MP-* tests)
- `tests/unit/test_legacy_deprecation.py` — NEW (V-MP-LEGACY-DEPRECATION)
- DELETED: `src/mint/mcp_g1.py`, `mcp_g2.py`, `sandbox/`, `create.py`,
  `assemble.py`; `tests/unit/test_{sandbox,assemble,postprocess,engine_flag}.py`;
  `tests/integration/test_{mcp_g1,mcp_g2,create}.py`
- EDITED (subtractive): `src/mint/cli.py`, `src/mint/config.py`,
  `tests/integration/test_cli.py`, `docs/technology.xml`,
  `docs/requirements.xml`, `.env.example`

Last commit on main: the Phase-15 closeout sync. Tag `v0.4.0a4` NOT
yet created.
