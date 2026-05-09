# MINT Pure Python Edition — Session Handoff

**Дата:** 2026-05-09
**Branch:** `main`
**Status:** Phase-7 + Phase-8 + Phase-9 shipped; ready for Phase-11 dispatch (Phase-10 skipped per roadmap)

## Project context

MINT — Model-Independent Normalization Toolkit. Document generation для DOCX/PPTX/XLSX любым LLM. Стратегия с v0.3 — **Pure Python Edition** (`MINT_ENGINE=python`, default), parallel пакет `src/mint_python/` замещает legacy Node.js sandbox + docx-js пайплайн (`MINT_ENGINE=js`, fallback).

**Living spec:** `docs/mint-pure-python-handover-v1.md` (canonical v1.0, 142 строки) + `docs/pure-python/handover-v1.md` (копия).

## GRACE methodology in use

3 канонических XML артефакта в `docs/`:

- `development-plan.xml` — модули M-* (legacy js) и MP-* (pure python), Phase-N ImplementationOrder, DF-* data flows
- `knowledge-graph.xml` — модули + public-interface annotations + CrossLinks
- `verification-plan.xml` — V-M-*/V-MP-* per-module + VF-* cross-cutting flows + Wave-N + Gate-Phase-N + SwarmFixtures + SubagentExecutionPackets + FailurePackets

**Naming convention:**

- `M-*` = legacy js engine (e.g. M-CONFIG, M-CLI, M-VALIDATE, M-CREATE, M-EDIT, M-SANDBOX)
- `MP-*` = pure python (e.g. MP-STYLE, MP-CONTENT, MP-TABLE, MP-SECTION, MP-DOCUMENT, MP-CHART, MP-SDK)
- `V-M-*` / `V-MP-*` mirror the prefixes for verification entries
- `VF-*` = cross-cutting flow verifications regardless of engine
- **Repo convention:** `verification-ref` → V-M-* / V-MP-* (module entry), NOT VF-*

## Phases shipped

| Phase | Scope | Status |
|---|---|---|
| Phase-6 | Dual-engine flag (`MINT_ENGINE=python\|js` + `--engine`); MP-PKG empty skeleton; M-CONFIG Engine StrEnum; M-CLI `_select_engine` chokepoint + BLOCK_LOAD_CONFIG/BLOCK_SELECT_ENGINE/BLOCK_DISPATCH markers | ✓ done (2026-05-09) |
| Phase-7 | Pure Python Core+SDK: MP-STYLE / MP-CONTENT / MP-TABLE / MP-SECTION / MP-DOCUMENT / MP-SDK (handover §3.1-3.3, §3.5). Idempotent save() via core.xml dcterms pin. VF-013 e2e baseline. | ✓ done (2026-05-09) |
| Phase-8 | MP-CHART (handover §3.4): 7 chart factories (bar/line/stacked_bar/pie/heatmap/waterfall/gantt) + from_matplotlib + from_seaborn lazy + from_plotly stub. VF-014 e2e. matplotlib hard dep. Section.add_chart unstubbed. | ✓ done (2026-05-09) |
| Phase-9 | MP-RULES + MP-VALIDATE + MP-FIX (handover §6 Phase 3): pure-python successors to M-RULES/M-VALIDATE/M-FIX. 53+42+23=118 unit tests + 7 cov-gap tests + 9 e2e. Document.validate/fix unstubbed via temp-file delegation. VF-015 + VF-016 e2e. Gate-Phase-9: 643 tests passed, 1 skipped, 100% coverage, ruff/mypy clean. Full-integrity review PASS. | ✓ done (2026-05-09) |

## Current state (post-Phase-9 + full-integrity review fixes + cov-gap closure)

- **643 tests passed, 1 skipped** (1 = pre-existing M-EDIT latency stub from Phase-5 — unrelated)
- **100% coverage on `src/mint_python/`** (1251 stmts, 0 miss) — gate enforced via `pytest --cov-fail-under=100`
- **ruff + mypy clean** (18 mint_python source files including MP-RULES/VALIDATE/FIX; mypy `--strict`)
- **uv build wheel:** `mint_runtime-0.2.0-py3-none-any.whl` ships 12 mint_python core+sdk entries (+ rules + validate + fix as flat-package files)
- **`from mint_python.sdk import Document, Section, Table, Style, Image, TOC, Pt, ColorPalette, Chart, presets`** — 10 §3 types + presets registry, all functional

### Quick-look user surface

```python
from mint_python.sdk import Document, Section, Table, Chart, Style, presets

doc = Document(format="docx", title="Q2").with_style_preset("alga_corporate")
doc.add_cover(title="Q2 Memo", subtitle="2026")
doc.add_toc(max_level=2)
doc.add_section(
    Section("Revenue", level=1)
        .add_paragraph("Quarterly trend.")
        .add_table(Table.from_list([["Q","Rev"],["Q1","$1M"],["Q2","$1.3M"]]))
        .add_chart(Chart.bar(["Q1","Q2","Q3","Q4"], [1.0,1.3,1.6,1.9],
                              title="Revenue ($M)", width_inches=5.0))
)
doc.save("memo.docx")
# → 506-test suite green; M-VALIDATE lenient PASSES; chart inline_shape EMU exact
```

## Active stubs (NotImplementedError + BLOCK_PHASE_GUARD)

These are the surfaces that future phases unblock:

| Stub | Target Phase | Notes |
|---|---|---|---|
| `Document.inject_grace` | Phase-11 (handover §6 Phase 5) | MP-GRACE planned in knowledge-graph; delegation pattern documented |
| `Document.to_pdf` | Phase-11 | Gotenberg integration |
| `Chart.from_plotly` | Phase-12+ | separate render pipeline (HTML interactive) |

## Pre-Phase-9 reading list (in order)

1. **`docs/mint-pure-python-handover-v1.md`** §6 Phase 3 + §7 acceptance criteria — canonical scope spec
2. **`docs/development-plan.xml`** Phase-8 + ImplementationOrder/Phase-7 — convention reference (module structure, step descriptions, status flips)
3. **`docs/verification-plan.xml`**: SwarmFixtures + SubagentExecutionPackets + Gate-Phase-8 — wave dispatch pattern reference
4. **`docs/knowledge-graph.xml`** MP-DOCUMENT.fn-validate + fn-fix annotations — current stub state (these are what Phase-9 unstubs)
5. **Legacy js-engine modules** (reference for porting):
   - `src/mint/rules/` (D-H/P-H YAML rule definitions)
   - `src/mint/validate.py` (run_checks engine; severity modes)
   - `src/mint/fix.py` (auto-fix categories: safe/visual/destructive; max-3 iter cascade)
6. **`tests/fixtures/mp_e2e_baseline.json`** + `tests/fixtures/mp_chart_e2e_baseline.json` — audit baselines (must remain fingerprint-stable across Phase-9)

## Phase-9 (handover §6 Phase 3): MP-RULES + MP-VALIDATE + MP-FIX

**Цель:** Pure-python successors to M-RULES + M-VALIDATE + M-FIX. Unstubs `Document.validate(level=...)` и `Document.fix(strategy=...)`. Самая большая оставшаяся phase (3 модуля + 2 stub retires). Открывает acceptance bar tightening: lenient → strict mode на golden docs.

**Vorausschau wave structure:**

- **MP-RULES** — D-H/P-H rule definitions parsed from YAML; pure-python successor to M-RULES — likely solo wave, no other MP-* deps
- **MP-VALIDATE** — run_checks engine; severity modes — depends on MP-RULES
- **MP-FIX** — auto-fix categories: safe/visual/destructive; max-3 iter cascade detect — depends on MP-VALIDATE
- **Integration wave** — Document.validate/fix unstub + VF-015 e2e + baseline; non-regression VF-013 + VF-014

**Risks:**

- D-H/P-H rules calibrated for docx-js output may flag false positives on python-docx; need rule-tuning during port (already known soft baseline = 0 from VF-014, but strict mode untested)
- VF-013 + VF-014 non-regression critical (validate/fix MUST NOT break chart-free or chart-bearing existing baselines)
- Coverage 100% gate must hold across new MP-RULES/VALIDATE/FIX modules
- D-H rules registry shape: YAML parsing + dict-of-rule-fn dispatch — likely the pattern, but verify against legacy

## Workflow conventions (для нового агента)

- **Profile `balanced` по умолчанию:** один upfront approval per phase, scoped review per worker, controller-batched shared-artifact sync per wave
- **Workers commit свою работу** after green verification (НЕ controller commits source code)
- **Controller commits** ТОЛЬКО docs/*.xml status flips и shared-artifact deltas (`grace(meta): sync ... after Wave-N`)
- **Pre-wave controller setup**: provision new fixtures + helpers + new hard deps via `uv add` if matplotlib-style "known dep, fail-on-import" constraint applies
- **100% coverage обязательно** — `tests/unit/test_mp_coverage_gaps.py` pattern для error path coverage; `# pragma: no cover` для unreachable defensive guards (used sparingly with rationale comment)
- **Critical invariants** enforced via grep:
  - `forbidden-2` (no `from mint.*` в src/mint_python)
  - `constraint-8` (no Node calls — `subprocess.*node|npm|vm2|isolated-vm|src/mint/sandbox`)
  - `forbidden-3` (no module-level seaborn/optional deps; AST-scan pattern from `assert_seaborn_not_imported`)
- **VF-N non-regression**: every new VF must re-run prior VF e2e baselines (VF-013 + VF-014 patterns) to guard against in-place edits breaking earlier surfaces

## Slash skills available

- **`$grace-plan`** — architectural planning (modules + waves + verification surface draft); ends with Phase-N ready in 3 XML artifacts
- **`$grace-verification swarm`** — deepen tests/fixtures для multi-agent dispatch (SwarmFixtures + SubagentExecutionPackets + FailurePackets); usually run after $grace-plan, before dispatch
- **`$grace-multiagent-execute`** — parallel-safe wave dispatch; balanced default profile
- **`$grace-reviewer full-integrity`** — full GRACE coherence audit at phase boundaries; catches XML-vs-source drift

## Loose ends / known issues

- `pyproject.toml [project].version = 0.2.0` — not bumped after Phase-8; follow-up to 0.3.0 at next cleanup pass
- `src/mint/cli.py` internal CHANGE_SUMMARY at v0.3.0 (file-level versioning, not tracked package version) — стилевая неконсистентность; not blocking
- Legacy js-engine modules (M-PLAN, M-SECTION, M-CREATE, M-ASSEMBLE, M-MCP-G1, M-MCP-G2) — function under `MINT_ENGINE=js`; pure-python siblings still pending (Phase-12+ rollout)
- `tests/integration/conftest.py` is a thin re-export shim — controller-owned per SwarmFixtures/integration-conftest-shim doc; extends only when integration test needs a fixture not yet shimmed

## Suggested kickoff prompt for new session

```
Continuing MINT Pure Python Edition. Phase-7 (Core+SDK), Phase-8 (Chart+matplotlib),
and Phase-9 (Validation+Fix) shipped; 643 tests passed + 1 skipped, 100% coverage
on src/mint_python/, ruff/mypy clean. Active stubs: Document.inject_grace + to_pdf
(both Phase-11 = handover §6 Phase 5 = MP-GRACE + Gotenberg) and Chart.from_plotly
(Phase-12+). Phase-10 (handover §6 Phase 4 — Execution tiers + RestrictedPython
sandbox) skipped per roadmap; next is Phase-11.

Read docs/SESSION_HANDOFF_2026-05-09.md for the full state, then:
1. docs/mint-pure-python-handover-v1.md §6 Phase 5 + §7
2. docs/development-plan.xml Phase-9 (convention reference)
3. docs/verification-plan.xml SwarmFixtures + Gate-Phase-9 (dispatch pattern)
4. src/mint/grace.py + src/mint/qa.py (legacy GRACE injector + Gotenberg adapter
   reference for porting)

Then $grace-plan with Phase-9 scope (3 modules + 2 stub retires).
```

## Commit timeline (Phase-7 + Phase-8 highlights)

```
Phase-8:
  9dbf307  grace(meta): Phase-8 closeout
  caf30ab  grace(MP-SECTION,MP-SDK,VF-014): unstub + export Chart + e2e
  666868c  grace(meta): sync after Wave-8-1
  ccfff03  grace(MP-CHART): add Chart factories + render
  42a5e85  grace(meta): pre-Wave-8-1 (matplotlib + fixtures)
  d3f8bc7  grace(Phase-8 peer-review): close C1+C2+M1
  a4fdf6e  grace(Phase-8 verification): SwarmFixtures + SubagentPackets
  c7e38a5  grace(Phase-8): plan + verification surface

Phase-7:
  7685166  grace(Phase-7): drive coverage to 100%
  7df3d90  grace(meta): Phase-7 review minor fixes
  988599a  grace(meta): Phase-7 closeout
  9793ff4  grace(MP-SDK,VF-013): public SDK + e2e + baseline
  c2d31bd  grace(meta): unstub build_golden_document
  7763a83  grace(meta): sync after Wave-7-4
  5b9dc6c  grace(MP-DOCUMENT): facade + 4 stubs
  0a95235  grace(meta): sync after Wave-7-3
  0bf53d2  grace(MP-SECTION): fluent + add_chart Phase-2 stub
  09ab05f  grace(meta): sync after Wave-7-2
  20de500  grace(MP-TABLE): model + 5 factories + render
  00f04a6  grace(MP-CONTENT): Paragraph + Run + Image emitters
  9c381f2  grace(meta): sync after Wave-7-1
  94af06c  grace(MP-STYLE): + Pt + ColorPalette + load_preset
  769a59d  grace(meta): provision conftest.py + _mp_helpers.py
  8163785  grace(Phase-7 verification): SwarmFixtures + Subagent
  84b8609  grace(Phase-7 peer-review)
  d10cf9e  grace(Phase-7): plan + verification surface
```
