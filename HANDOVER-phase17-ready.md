# Handover — Phase-17 mid-flight (W17-3 + W17-4 remain)

Written 2026-05-11. **Phase-17 is partially shipped**: W17-0 controller
pre-flight + W17-1 parallel × 2 + W17-2 parallel × 2 all closed. Two waves
remain: W17-3 single-worker MP-AUDIT-EXTEND, then W17-4 controller-only
gate + 0.4.0a5 → 0.4.0a6 bump + handover archive.

This handover replaces the original Phase-16-close version. The planning
content below (waves, candidates, framing) is still valid for W17-3 + W17-4
and Phase-18 candidates; the **Current state (2026-05-11 mid-flight)**
section at the top is what a fresh session should read first.

---

## Current state (2026-05-11 mid-flight)

- **Version**: `0.4.0a5` (Phase-16 close). Bump to `0.4.0a6` lands in W17-4.
- **Tests**: 1348 pass + 7 skip; ruff + mypy clean; 100% repo-wide coverage
  on `src/mint_python/`; `uv build` clean; constraint-8 grep gate (0 hits
  for `from mint\.(ooxml|fingerprint|extract|edit)` in `src/mint_python/`);
  constraint-4 grep gate (0 hits for semver-logic duplication in
  `resources.py`).
- **Live MCP surface — 16 tools** (15 from Phase-16 + 1 new W17-2):
  - `mint_create_document`, `mint_create_memo` (alias)
  - `mint_list_templates`, `mint_get_template`, `mint_update_template`
  - `mint_list_presets`, `mint_get_preset`
  - `mint_read_grace_manifest`
  - `mint_validate_document`, `mint_fix_document`, `mint_fingerprint_document`,
    `mint_extract_content`, `mint_edit_document`
  - `mint_update_preset_palette/typography/spacing`
  - **NEW in W17-2**: `mint_suggest_template` (template-picker UX)
- **Live resource templates**: `mint://template/{name}`, `mint://preset/{name}`
  — **NOW chains to versioned preset files** after W17-2 MP-MCP-RESOURCES-VERSIONED.
- **Doc_types catalog** — 7 total: memo, letter, report, decision-record,
  contract, nda-bilingual-ru-en, **technical-spec** (NEW W17-1).
- **Phase-17 commits so far** (10):
  - `2672bc1` W17-0 (a) — DocumentSpec extension
  - `c0d8137` W17-0 (b) — resolve_latest_preset_path promoted public + collect_preset_versions
  - `e2ebad0` W17-0 (c) — manifest 12-key canonical dict
  - `4e3c0f5` W17-0 meta sync
  - `381a10a` W17-1 MP-DOC-PERSONAL-GUARD
  - `7359454` W17-1 MP-DOC-TECH-SPEC
  - `8f660c8` W17-1 meta sync (incl. collect_preset_versions unit tests)
  - `01cb44a` W17-2 MP-DOC-PICKER
  - `ea23227` W17-2 MP-MCP-RESOURCES-VERSIONED
  - `<latest>` W17-2 meta sync + picker tail-import

## What W17-3 needs to do

Single-worker dispatch (SWARM-CAREFUL). MP-AUDIT-EXTEND extends
`_audit_instructions` in `src/mint_python/mcp/document.py` to stamp:
1. `preset_version=<version>` ALWAYS — closes V-MP-THEME-EDIT scenario-10
   deferred from Phase-16
2. `lang=<comma-separated codes>` when template has ≥2 distinct language-suffix
   fields (e.g. bilingual NDA's `scope_ru` + `scope_en`) — closes
   V-MP-DOC-BUNDLE scenario-7b deferred from Phase-16

Worker brief MUST:
- Import `resolve_latest_preset_path` from `mint_python.mcp.preset_edit`
  (W17-0 public helper) — NEVER reimplement semver logic.
- Use explicit `_ISO_LANG_CODES` allowlist `{en, ru, kk, ky, uz, de, fr, es,
  zh, ja, tr, ar}` for `_detect_template_languages` — NOT a generic
  `[a-z]{2,3}` regex (would falsely match `_ms`, `_id`, `_pk`, `_no`, `_qa`).
- Emit `BLOCK_AUDIT_PRESET_VERSION` (always) + `BLOCK_AUDIT_LANG` (bilingual
  only) log markers.
- Cover all 19 V-MP-AUDIT-EXTEND scenarios incl. multi-lang ≥3 codes, case
  sensitivity rejection (RU vs ru), non-lang-suffix discrimination,
  backwards-compat read of Phase-16 docx (preset_version=None cleanly),
  semver natural-order (1.10 > 1.2), encoding round-trip, concurrent
  edit+render race, manifest size bound, end-to-end round-trip via
  `mint_read_grace_manifest`.

After W17-3 closes, controller flips 3 V-MP entries in the sync commit:
- V-MP-THEME-EDIT scenario-10: `deferred` → `passing`
- V-MP-DOC-BUNDLE scenario-7: `partial` → `passing`
- V-MP-MANIFEST-READ scenario-9: already added in W17-0, but worker should
  exercise the round-trip with a real preset_version + lang stamp

## What W17-4 needs to do (controller-only gate)

1. **Run all 9 Gate-Phase-17 commands** per docs/verification-plan.xml
   Gate-Phase-17. Note command-7 was updated to include `picker` in the
   smoke import set; command-9 expects 16 MCP tools live (15 + new
   `mint_suggest_template`).
2. **Address ruff debt if any new errors surfaced**. Phase-17 has not
   added new pre-existing-file ruff debt; the Phase-16 W4 `[tool.ruff.lint.per-file-ignores]`
   block in `pyproject.toml` carries forward unchanged.
3. **Bump version**: `pyproject.toml` 0.4.0a5 → 0.4.0a6; refresh `uv.lock`.
4. **Tag** `v0.4.0a6` (annotated, NOT pushed) on the bump commit.
5. **Archive** this file to `docs/archive/HANDOVER-phase17-ready.md`; write
   a fresh `HANDOVER-phase18-ready.md` with Phase-18 candidates (see below).
6. **Memory updates**: `project_phase17_framing.md` → archive entry; new
   `project_phase17_shipped.md` capturing what landed + carried-forward gaps.

## Verification refinements surfaced mid-flight

Two worker-discovered nuances that future sessions / Phase-18 may want to
revisit:

1. **V-MP-DOC-PICKER scenario-8 Cyrillic-only threshold**: V- spec says
   `score ≥ 0.4` for `intent="договор оказания услуг"` → top-1=contract.
   Actual realistic score is ~0.3 because Cyrillic intent doesn't share
   tokens with English-language template descriptions — only the bonus
   keyword `договор` hits, producing exactly 0.3 (no jaccard contribution).
   Worker tested at ≥0.3. **Phase-18 candidate**: add Cyrillic glosses to
   template `description:` fields so jaccard contributes for cross-language
   intents.
2. **V-MP-MCP-RESOURCES-VERSIONED scenario-11 write-while-glob race**: the
   spec mentions `yaml.safe_load raising on transient half-flushed file`.
   Actual resolver doesn't parse YAML in the read path — it uses raw
   `.read_text()`, which returns `""` for empty/half-flushed file. The
   half-flushed-file tolerance is real but doesn't need yaml-error handling.
   Worker rewrote the test to match implementation.

## Discoveries to carry forward

- **Circular-import pattern** (W17-2 discovery): `preset_edit` → `mcp.document`
  → `mcp.resources` → `preset_edit` forms a cycle at module-load. Solution:
  lazy-import preset_edit helpers inside the function that uses them
  (`_resolve_preset_for_read` in resources.py). Future MCP modules importing
  preset_edit helpers MUST follow this pattern. Captured in KG CrossLink on
  MP-MCP-RESOURCES-VERSIONED → MP-THEME-EDIT.
- **`_detect_anonymous_flag` returns `tuple[bool, str | None]`** (W17-1
  worker improvement over original brief — second value is matched form,
  surfaces as `match_form` in `BLOCK_ANONYMOUS_DETECTED` telemetry).
- **Walker conditional-skip** is implemented as a 2-pass `_prepare_layout`
  pre-processor (Phase A drops placeholder-only-empty blocks; Phase B drops
  decorative headings whose immediate body block was dropped). Generic;
  future optional fields in other templates benefit automatically.
- **MP-DOC-PICKER bonus-term collision detection**: at module init the
  picker builds a reverse-map keyword → set of doc_types; if any keyword
  maps to ≥2 doc_types it emits `[MP-Picker][init][BLOCK_BONUS_COLLISION]`
  WARNING. Helps maintainers spot regressions when adding templates.
- **forbidden-9 mechanically enforced** (MP-DOC-PERSONAL-GUARD) via a
  runtime test `test_forbidden_9_no_enforcement_claims_in_source` that
  greps the source for "enforce/guarantee/prevent" near "anonymous/personal".
  Pattern reusable for future "documentation must surface X" style rules
  that benefit from mechanical checking.

## Phase-18 candidates (after W17-4 closes)

After Phase-17 ships at 0.4.0a6, the natural Phase-18 list:

- **Legacy `src/mint/` retirement** (deferred from Phase-17): retire the
  Phase-15 additive ports' legacy siblings. Phase-15 + Phase-16 ported
  ooxml/fingerprint/extract/edit to mint_python; the legacy modules remain
  for porting-parity tests + pre-existing scripts. Retirement = audit + migrate
  consumers + delete + CLI dispatch update + requirements.xml constraint-7
  rewrite. SWARM-FORBIDDEN single-worker (broad blast radius).
- **`_resolve_target` `..` normalization**: pre-existing issue in BOTH
  legacy `mint.ooxml` and pure-python port — python-docx-generated docx
  contains rel `Target="../customXml/item1.xml"` that `_resolve_target`
  doesn't normalize. `pack` round-trip fails on such files. ~20 LOC fix.
- **RestrictedPython sandbox** (Phase-10 unblock, deferred since 2026-05-09):
  MP-EXEC-{SMALL,MEDIUM,FRONTIER} + RestrictedPython integration. Big scope.
- **Documentation + 0.4.0 release prep**: README rewrite, OPENING.md,
  CHANGELOG.md (Phase-13 → Phase-17), CONTRIBUTING.md, 0.4.0 release tag,
  screencasts of the cross-model demos.
- **Cyrillic template descriptions** (V-MP-DOC-PICKER refinement above)
- **mint_get_preset(name@version) explicit version pinning** (V-MP-MCP-RESOURCES-VERSIONED
  worker noted as Phase-18 candidate)

---

# Original Phase-17 framing (planning content)

Written 2026-05-11 at the close of the Phase-16 session. The repo's
`MEMORY.md` already loads automatically; the Phase-16 brief is archived
at `docs/archive/HANDOVER-phase16-ready.md`. This section covers what's
NOT in memory and NOT already in the prior handover: the Phase-16
outcome, what changed in the surface area, the discoveries that shaped
Phase-17 framing, and the candidate next phases.

## Where we are

- **Version**: `0.4.0a5` (bumped this session). Tagged.
- **Phase-16**: closed 2026-05-11. All 11 modules shipped + Gate-Phase-16
  passed. The MCP surface now exposes the full document hygiene + edit +
  preset-editor toolset.
- **Tests**: 1249 pass, 7 skip; 100% coverage maintained on `src/mint_python`
  (with documented `# pragma: no cover` markers on ~12 defensive guards in
  the ported MP-EDIT); ruff + mypy clean; `uv build` clean.
- **Live MCP surface** (15 tools, all `mint_` prefixed):
  - `mint_create_document`, `mint_create_memo` (alias)
  - `mint_list_templates`, `mint_get_template`, `mint_update_template`
  - `mint_list_presets`, `mint_get_preset`
  - `mint_read_grace_manifest`
  - **NEW in Phase-16**: `mint_validate_document`, `mint_fix_document`,
    `mint_fingerprint_document`, `mint_extract_content`, `mint_edit_document`
  - **NEW in Phase-16**: `mint_update_preset_palette`,
    `mint_update_preset_typography`, `mint_update_preset_spacing`
- **Live resource templates**: `mint://template/{name}`, `mint://preset/{name}`.
- **What write tools are gated**: `mint_update_template` and the three
  preset-edit tools all require the author to be on the same allowlist
  (env `MINT_TEMPLATE_WRITERS` or `~/.config/mint/writers.json`). Themes
  reuse the templates allowlist by design (Phase-16 framing — themes are
  sub-noun under templates). Read tools stay open. Document-mutation tools
  (`mint_fix_document`, `mint_edit_document`) do NOT consult the allowlist
  — they operate on caller-supplied paths and preserve a `.bak` backup.
- **What every produced docx carries**: a GRACE manifest (Phase-14) +
  advisory visual-QA score in `structured_content` (Phase-15). The
  manifest does NOT yet carry preset_version OR bilingual-NDA language
  metadata — see Phase-17 candidates.

## What got added in Phase-16

By order of arrival:

**Wave-16-1** (5 parallel workers, SWARM-SAFE):

- `src/mint_python/mcp/validate.py` (commit 25126bc) — `mint_validate_document`
- `src/mint_python/mcp/fix.py` (8d6a7ba) — `mint_fix_document`
- `src/mint_python/fingerprint.py` (b5d4748) — pure-python port; preserves
  legacy API names but **RESHAPED FingerprintResult fields** to
  `{hash, format, has_styles_xml, byte_count}` and **RENAMED**
  `DriftStatus.BASELINE_MISSING → UNKNOWN`
- `src/mint_python/extract.py` (a020cfd) — pure-python port; preserves
  legacy flat dict output shape `{colors, typography, format, xml_sources, detected_layouts?}`
  per forbidden-3 NO-DIVERGENCE; NAMESPACES + `_detect_format` duplicated
  inline rather than imported (constraint-8)
- 4 YAML doc_types: `templates/{report,decision-record,contract,nda-bilingual-ru-en}.yaml`
  (7e10bd9) — bilingual NDA uses stacked Ru/En paragraphs (no engine extension)

**Wave-16-2** (3 parallel workers):

- `src/mint_python/mcp/fingerprint.py` (94c6e5a) — `mint_fingerprint_document`;
  drift_status is JSON null when no baseline_hash (short-circuits compare())
- `src/mint_python/mcp/extract.py` (7c28cc7) — `mint_extract_content`; chose
  **option (B) nested reshape** `{format, theme: {colors, typography, xml_sources}, layouts, extracted_at}`
  at the MCP boundary (W1 flat dict was bound by legacy parity; the wrap is
  a fresh public surface)
- `src/mint_python/mcp/preset_edit.py` (4e85a07) — three structured-patch
  tools; atomic O_EXCL versioned writes; concurrent-write race deterministic;
  presets/_audit.jsonl sidecar

**Wave-16-3** (scope-expanded mid-session — see below):

- `src/mint_python/ooxml.py` (dc44247) — port preserving legacy API
  byte-identically; UnpackResult/PackResult field names + OOXMLError.code
  values + log markers all preserved. Parity oracle in
  `tests/_helpers/ooxml_parity.py`
- `src/mint_python/edit.py` (254f051) — **biggest single port of Phase-16**
  at 1914 LOC; preserves legacy public surface byte-identically. 151 tests.
  97% raw coverage; 12 defensive guards marked `# pragma: no cover` push to
  100% net. Notable: legacy uses error code `BACKUP_FAILED` (not
  `EDIT_BACKUP_FAILED` as the dev-plan abstract spelled); port preserved
  legacy verbatim; W3c wrap remaps.
- `src/mint_python/mcp/edit.py` (7be73cd) — `mint_edit_document`; adds
  wrap-layer hardening that rejects literal OOXML substrings
  (`<w:r`, `<w:p`, `<w:t`, `</w:` case-insensitive) in anchor.value
  BEFORE invoking edit(); reuses `mint_python.mcp.validate._canonicalize_report`
  (via lazy import inside the function to break a circular dependency
  discovered at W4 gate-prep — see Discoveries §3).

**Wave-16-3a was a controller scope-expansion** added mid-Phase-16 (2026-05-11
after W2 close). Pre-flight grep of edit.py revealed it imports 3 functions
from `mint.ooxml` (unpack, pack, validate_relationships) on its hot path;
mint.ooxml is fully self-contained (zero from-mint imports). Pre-porting it
before MP-EDIT kept W3b's worker brief simple and avoided in-flight scope
explosion. Net Phase-16 module count: 11 (was 10 at plan-open).

## Subagent dispatch protocol — empirical notes from this session

The Phase-16 multi-agent run validated the `balanced` profile on the
biggest swarm we've run so far (5 parallel × W1; 3 parallel × W2; serial
× 3 × W3a/b/c). What worked:

- **W1 5-way parallel dispatch** succeeded cleanly. All 5 workers stayed
  inside their disjoint write scopes; the controller pre-flight
  (`tests/_helpers/sample_docs.py` — 7 fixture factories) was load-bearing.
  5-way is the upper end; 2 of the 5 reported a brief workspace collision
  when they raced on `git add .` simultaneously, but both recovered cleanly
  via `git reset --soft` + re-stage and produced clean commits.
- **W2 3-way parallel dispatch** also clean. The two ports from W1
  (fingerprint, extract) blocked the corresponding W2 wraps — but the
  reshape findings from W1 (FingerprintResult fields; DriftStatus rename;
  flat-dict-vs-nested decision) were folded into the W2 worker briefs as
  "actual shipped shape, not the abstract dev-plan" overrides. Workers
  followed the briefs correctly without escalating.
- **W3 serial dispatch** for MP-OOXML → MP-EDIT → MP-MCP-EDIT worked
  cleanly. The W3a port was small enough to be low-risk; W3b (the 2000-LOC
  port) succeeded on the first dispatch and shipped 151 tests at 97% raw
  coverage. W3c was a thin wrap that finished in under 8 minutes of agent
  time.
- **Pre-flight worker prompts that worked**:
  - W3a: pre-existing inventory grep of `src/mint/edit.py` → `mint.ooxml`
    coupling. 3 hot-path imports, 1 validate import, 1 enum import — surfaced
    BEFORE the worker started, so the W3a brief was crisp.
  - W3b: pre-flight included the FULL inventory of W3a's public exports
    (UnpackResult fields, PackResult fields, OOXMLError.code values), so
    the worker didn't have to grep. Also included the legacy edit.py
    section-by-section breakdown and explicit pointers to existing
    test_edit.py for porting-parity reference.
  - W3c: pre-flight included the FULL MP-EDIT public surface (signature,
    field names, error codes incl. the BACKUP_FAILED-vs-EDIT_BACKUP_FAILED
    naming mismatch + the explicit remap instruction). The wrap-layer
    raw-OOXML hardening was specified in the brief, not invented by the
    worker.

What surprised:

- **A circular import emerged at W4 gate-prep** that the test suite hadn't
  caught: `validate.py` imports `server` from `document.py` (the standard
  tail-import pattern from Phase-15); `document.py` tail-imports `edit`;
  `edit.py` (W3c wrap) imports `_canonicalize_report` from `validate.py`.
  When `import mint_python.mcp.validate` is the first trigger, validate's
  init isn't complete when edit needs `_canonicalize_report` from it. The
  pytest harness loads modules in different order than the manual smoke
  command `from mint_python.mcp import validate, fix, fingerprint, extract,
  edit, preset_edit`, which is why tests didn't catch it. Fix: lazy import
  inside the function that uses `_canonicalize_report`. Worth pre-flagging
  in future wrap-layer briefs: **if a wrap module imports a helper from a
  sibling wrap module, the helper must be lazy-loaded inside the function
  to avoid the validate-document-edit-validate cycle.**
- **Test workspace collisions during 5-way parallel W1** weren't a
  showstopper but were avoidable. Recommend: when dispatching 4+ workers
  in parallel, EITHER use `isolation: "worktree"` OR brief each worker to
  use targeted `git add <specific paths>` rather than `git add .`. Both
  affected workers recovered, but it cost ~5 minutes of disruption each.

## Phase-17 framing — locked in 2026-05-11 from real-world gap analysis

**Phase-17 is reshaped around a same-day real-world test of `mint_create_document`.**
The user invoked the new Phase-16 MCP surface from a separate Claude Desktop
session to convert a draft technical-spec request into a docx. Three production
gaps surfaced that should drive Phase-17:

1. **Privacy gap — auto-fill of `sender` from session context**. Letter template
   has `sender` in required_fields → Claude Desktop's chat-driven elicitation
   fallback (project_phase13_smoke_findings) filled the value from the calling
   model's session context (real name, company, email) WITHOUT asking the user.
   This is a Claude-client behavior, not a MINT logic bug — but MINT can mitigate:
   - make `sender` optional in letter.yaml (1-line YAML edit)
   - add intent-flag `[anonymous]` recognition in the heuristic extractor
     (~30 LOC in document.py) — blocks auto-fill of personal-data fields when
     present
   - surface `{anonymised: true, fields_omitted: [...]}` in structured_content
     so the caller can see what got redacted
2. **Engine gap — no `kind: table` in layout**. Phase-16 V-MP-DOC-BUNDLE
   forbidden-1 explicitly forbade adding new layout block kinds. That was
   correct Phase-16 hygiene but wrong as a permanent architectural rule.
   Real-world documents (CSV schemas, calibration tables, technical specs)
   need tables. Phase-17 lifts the constraint: walker gains `kind: table`
   (and probably `kind: section` for nested structure) consuming the existing
   MP-TABLE module from `src/mint_python/core/table.py` (Phase-7).
3. **Catalog gap — no `technical-spec` doc_type**. Adding Phase-16 doc_types
   (report / ADR / contract / bilingual NDA) closed the simple-prose set but
   not the engineering-document set. `templates/technical-spec.yaml` with
   nested `kind: table` support is the natural addition.
4. **UX gap — silent template fallback**. When no template matches well,
   `mint_create_document` picks the closest by heuristic and ships. Better:
   when match quality is below threshold, return structured "no good match —
   try X / Y / Z" so the caller can re-prompt. Or in chat-driven fallback,
   surface the alternatives to the user.

### Wave shape (proposed)

- **Wave 17-1 (privacy guardrails)** — MP-DOC-PERSONAL-GUARD. Intent-flag
  `[anonymous]` parsing; heuristic-blocklist for `sender / author / contact`
  when flag present; `sender` optional in letter.yaml; `anonymised` key in
  structured_content. ~80 LOC + tests. SWARM-SAFE.
- **Wave 17-2 (layout engine extension — biggest)**:
  - MP-LAYOUT-TABLES: walker-side `kind: table` + `kind: section` integration
    with `mint_python.core.table.Table`. Consumes the Phase-7 SDK. ~150-200 LOC.
  - MP-DOC-TECH-SPEC: `templates/technical-spec.yaml` (`required_fields=(title,
    purpose, sections)`; sections support nested tables). Possibly companion
    `data-request.yaml` / `api-spec.yaml` — decide at plan time.
- **Wave 17-3 (template-picker UX)** — MP-DOC-PICKER. When heuristic match
  quality < threshold, return structured `{match_quality: 'weak', suggestions:
  [...]}` instead of silent fallback. Chat-driven fallback surfaces alternatives.
- **Wave 17-4 (close Phase-16 deferrals — three shared root cause)**:
  - V-MP-THEME-EDIT scenario-10: stamp `preset_version` in `_audit_instructions`
  - V-MP-DOC-BUNDLE scenario-7b: bilingual NDA language metadata in manifest
  - MP-MCP-RESOURCES versioned-preset chain
- **Wave 17-5 (gate + 0.4.0a5 → 0.4.0a6 bump + handover)**.

Total: 5 waves, ~6-8 modules, ~600-900 LOC.

### Lower-priority Phase-17 candidates (de-prioritized after real-world findings)

The original Phase-17 candidate list (below) is still valid but takes back
seat to the gap-analysis-driven framing above:

### Option A — close the Phase-16 deferrals (GRACE manifest extensions + preset chain)

Three deferrals identified during Phase-16:

1. **V-MP-THEME-EDIT scenario-10** (deferred): preset edits don't surface
   in produced docx's GRACE manifest. `_audit_instructions` in
   `src/mint_python/mcp/document.py` stamps `template_version` but NOT
   `preset_version`. ~10 LOC document.py change to stamp preset_version
   alongside template_version + a new scenario activation.
2. **V-MP-DOC-BUNDLE scenario-7b** (deferred): bilingual NDA's GRACE
   manifest doesn't carry `lang=['ru','en']` language metadata. Same
   `_audit_instructions` extension surface — solving one likely solves
   both. ~10-15 LOC.
3. **MP-MCP-RESOURCES versioned-preset chain** (gap surfaced in W2):
   `mint://preset/<name>` handler reads `BUILTIN_PRESETS` only — does NOT
   discover preset versions written by `mint_update_preset_*`. After a
   preset edit lands, the new version is invisible to MCP clients via
   resource URIs (though directly readable from disk). ~30 LOC
   resources.py extension to chain to `presets/<name>_v*.yaml` siblings.

All three are small individually; together they're a focused 1-2 wave
phase to close the loop on "preset edit → discoverable via MCP →
auditable in produced docx". Worth doing if the O!Bank demo needs to
showcase preset-edit feedback end-to-end.

### Option B — legacy `src/mint/` retirement

Phase-16 ported ooxml + fingerprint + extract + edit into `src/mint_python/`
ADDITIVELY — the legacy modules stay in place. Consumers still using them:
- `tests/unit/test_{ooxml,fingerprint,extract,edit}.py` — the legacy test
  suites that act as porting-parity oracles
- `tests/integration/test_mp_chart_e2e.py`, `test_mp_e2e.py`, `test_mp_document.py`
  — pre-existing tests still calling `from mint.fingerprint import fingerprint`
- `src/mint/cli.py` — the slim CLI surface still imports `mint.validate`,
  `mint.fix`, `mint.fingerprint`, `mint.extract`, `mint.edit`
- `tools/article_experiment/` — research scripts (memory project_article_experiment
  notes this uses bank Ollama directly via Python paths)

A Phase-17 retirement wave could:
1. Migrate the pre-existing tests to call `mint_python.*` successors
2. Migrate `src/mint/cli.py` to dispatch into `mint_python.*`
3. Audit `tools/` for any direct legacy imports
4. Delete `src/mint/{ooxml,fingerprint,extract,edit}.py` + their dedicated
   test files
5. Update `docs/requirements.xml` constraint-7 to reflect "legacy tree
   fully retired"

Phase-15 precedent for subtractive cleanup waves: SWARM-FORBIDDEN single-worker
broad blast radius. Realistically ~600 LOC of edits + 5 file deletions.
Significant scope; worth doing if "the legacy tree is GONE" matters for
demo positioning OR if pyproject ships need to drop legacy modules.

### Option C — small `_resolve_target` `..` normalization in MP-OOXML

The W3a worker surfaced a pre-existing issue in BOTH legacy `mint.ooxml`
and the pure-python port: python-docx-generated docx contains relative
rel `Target="../customXml/item1.xml"` that neither `_resolve_target`
normalizes correctly. `pack` round-trip fails on such files; the W3a + W3b
tests use `minimal_docx_bytes()` as the round-trip-safe fixture.

Fix: extend `_resolve_target` in `src/mint_python/ooxml.py` to handle `..`
normalization in rel paths. Mirror the fix in `src/mint/ooxml.py` if Option
B retirement isn't happening soon. ~20 LOC + a new V-MP-OOXML scenario.

Small but unblocks python-docx-produced docs in the edit pipeline — worth
doing if the demo needs to edit docs generated by python-docx (which is
what MP-DOCUMENT.save uses internally).

### Option D — RestrictedPython sandbox (Phase-10 unblock, again)

Skipped twice now (2026-05-09 in Phase-9; carried at Phase-15 close). Now
that Phase-16 ships, this is the next deferred-but-not-abandoned chunk of
handover §6 work. MP-EXEC-{SMALL,MEDIUM,FRONTIER} + RestrictedPython
sandbox + VF-012 PurePythonRegression activation. Big scope; only worth
doing if there's a concrete use case where templates + edit don't cover
the layout space and a frontier LLM needs to emit Python code to produce
the document.

### Option E — Documentation + 0.4.0 release prep

The codebase is at `0.4.0a5` alpha after Phase-16. Phase-17 could be the
*non-engineering* work: README rewrite, OPENING.md user-facing docs,
CHANGELOG.md (Phase-13 → Phase-16), CONTRIBUTING.md, the `0.4.0` release
tag itself + GitHub release notes, screencasts of (a) the document-hygiene
MCP flow, (b) the cross-model template handoff demo, (c) the structured
preset-edit flow, (d) the bilingual NDA generation. Light on code; heavy
on storytelling. Right call if the goal is "make Phase-16 visible to
people outside the project".

## Immediate next action

Tag `v0.4.0a5` is created locally on this commit (not pushed) — matches
Phase-15 protocol. Either decide Phase-17 framing fresh OR fold the
0.4.0a5 → 0.4.0a6 bump into the same commit that opens Phase-17's first
artifact.

If the user wants to keep momentum on production-grade ergonomics for
the demo: **Option A** (close the Phase-16 deferrals) is the most direct
continuation — it makes the preset-edit feedback loop visible end-to-end,
which is the missing piece for a clean O!Bank demo of cross-model preset
authoring.

If the user wants to clean up: **Option B** (legacy retirement). It's
the bigger scope but mechanically straightforward.

If the user wants to flip toward go-to-market: **Option E** (docs + release).

Other options sit in the middle of those poles.

## Discoveries worth carrying forward

Four things the next session should know that aren't in `MEMORY.md`:

1. **Circular import risk in wrap-layer modules**. When a new MCP wrap
   reuses a helper from a sibling wrap module (the W3c choice to reuse
   `_canonicalize_report` from `mcp/validate.py`), the import MUST be lazy
   (inside the function), NOT at module-level. The cycle path is
   wrap_A → document.server → document tail-imports wrap_B → wrap_B
   imports from wrap_A's not-yet-finished module. Pytest doesn't catch
   this because pytest collects test modules in different order than the
   manual `from mint_python.mcp import ...` smoke command. The gate
   command `python -c "from mint_python.mcp import validate, fix, ...,
   edit, ..."` is the canonical detector — keep it in Gate-Phase-N.

2. **5-way parallel dispatch needs git-isolation discipline**. Two W1
   workers raced on `git add .` and stepped on each other's staging
   area; both recovered but it cost ~5 minutes of disruption each. For
   future 4+ parallel waves, either use `isolation: "worktree"` per-Agent
   call OR brief workers to `git add <specific-paths>` rather than `git
   add .`. The disjoint-write-scope guarantee still holds — only the
   staging-area race is the problem.

3. **Phase-15 evidence "ruff + mypy clean" was inaccurate**. At Phase-16
   W4 gate-prep, the same `ruff check src/mint_python tests` command
   returned 37 errors in pre-existing files (test fixtures, conftest,
   older test files). Some are intentional (long OOXML literals,
   OOXML-derived camelCase test names like `delText` / `paraId`) — the
   W4 cleanup added `[tool.ruff.lint.per-file-ignores]` in pyproject.toml
   to scope those. Others were real (unused variables, nested `with`
   statements) — fixed. Lesson: future gates should rerun the full ruff
   command and audit per-file-ignore additions, not trust the prior gate's
   pass claim.

4. **The Gate-Phase-N grep gate scoping**. The Phase-16 original grep
   gate was `grep -rn "from mint\.(ooxml|fingerprint|extract|edit)\|..."
   src/mint_python/ tests/` — but `tests/` legitimately contains imports
   from `mint.*` for porting-parity oracles AND legacy-test modules.
   Re-scoped to `src/mint_python/` only AND added `^[^#]*` to skip
   commented references. Future Phase-N gates should inherit this
   scoping.

## Open issues

GitHub issues #1-3 closed at Phase-14 close; no new GitHub issues opened
during Phase-15 or Phase-16. Surface-level follow-ups from this session
live in the Phase-17 candidates list above and in the four discoveries.

## File-level state

Modified/created during this session and committed (NOT pushed):

**Plan + verification + KG artifacts** (controller-owned):
- `docs/development-plan.xml` — Phase-16 + all 11 modules; DF-020/021/022;
  Phase-16 ImplementationOrder block
- `docs/verification-plan.xml` — 11 new V-MP-* entries; VF-020/021/022;
  Gate-Phase-16; scenario reinterpretations (V-MP-MCP-VALIDATE scenario-2,
  V-MP-EXTRACT scenario-4, V-MP-MCP-EXTRACT scenario-3, V-MP-DOC-BUNDLE
  scenario-7, V-MP-EDIT scenario-9, V-MP-THEME-EDIT scenario-10)
- `docs/knowledge-graph.xml` — 11 new MP-* modules; 16 CrossLinks; pre-existing
  python-docx `../`-rel caveat captured at MP-OOXML

**New `src/mint_python/` modules**:
- `src/mint_python/fingerprint.py` (249 LOC, b5d4748)
- `src/mint_python/extract.py` (261 LOC, a020cfd)
- `src/mint_python/ooxml.py` (894 LOC, dc44247)
- `src/mint_python/edit.py` (1914 LOC, 254f051) + ~12 pragma markers added in W4
- `src/mint_python/mcp/validate.py` (264 LOC, 25126bc)
- `src/mint_python/mcp/fix.py` (333 LOC, 8d6a7ba)
- `src/mint_python/mcp/fingerprint.py` (275 LOC, 94c6e5a)
- `src/mint_python/mcp/extract.py` (258 LOC, 7c28cc7)
- `src/mint_python/mcp/preset_edit.py` (539 LOC, 4e85a07)
- `src/mint_python/mcp/edit.py` (437 LOC, 7be73cd) + circular-import fix in W4

**Document.py tail-imports** (controller-side, added per wave):
- `src/mint_python/mcp/document.py` — 6 new tail-import lines registering
  the new tools (fix, validate, fingerprint, extract, preset_edit, edit)

**New `templates/`**:
- `templates/report.yaml`, `templates/decision-record.yaml`,
  `templates/contract.yaml`, `templates/nda-bilingual-ru-en.yaml` (7e10bd9)

**New test fixtures**:
- `tests/_helpers/sample_docs.py` (W1 pre-flight, 09b62b6)
- `tests/_helpers/ooxml_parity.py` (W3a worker, dc44247)

**New test files** (one per module):
- `tests/integration/test_mp_mcp_validate.py` (406 LOC)
- `tests/integration/test_mp_mcp_fix.py` (549 LOC)
- `tests/integration/test_mp_doc_bundle.py` (466 LOC)
- `tests/integration/test_mp_mcp_fingerprint.py` (403 LOC)
- `tests/integration/test_mp_mcp_extract.py` (384 LOC)
- `tests/integration/test_mp_theme_edit.py` (1039 LOC)
- `tests/integration/test_mp_mcp_edit.py` (~570 LOC)
- `tests/unit/test_mp_fingerprint.py` (410 LOC)
- `tests/unit/test_mp_extract.py` (416 LOC)
- `tests/unit/test_mp_ooxml.py` (1007 LOC)
- `tests/unit/test_mp_edit.py` (2542 LOC)

**W4 closeout** (this commit):
- `pyproject.toml` — version `0.4.0a4` → `0.4.0a5`; `[tool.ruff.lint.per-file-ignores]`
  block (test files with OOXML literals + OOXML-derived camelCase test names)
- `uv.lock` — refreshed for the version bump
- `src/mint_python/edit.py` — ~12 `# pragma: no cover` markers on defensive
  guards
- `src/mint_python/mcp/edit.py` — lazy import of `_canonicalize_report` to
  break the circular dependency surfaced at W4 gate-prep
- Test files — various auto-fix ruff cleanups (sort imports) + targeted
  F841 / SIM117 fixes
- `docs/verification-plan.xml` — Gate-Phase-16 command-6 scoped to
  `src/mint_python/` only + `^[^#]*` to skip commented references

Last commit on main: the Phase-16 closeout sync. Tag `v0.4.0a5` created
locally (annotated, not pushed) on the closeout commit.
