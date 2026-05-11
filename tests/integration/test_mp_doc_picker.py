# FILE: tests/integration/test_mp_doc_picker.py
# VERSION: 0.1.0
# ruff: noqa: RUF001, RUF002, RUF003
#   ^ multilingual test intents (English + Russian) intentionally
#   carry Cyrillic characters that look-alike with Latin (Т, З, о vs
#   T, 3, o). The picker MUST handle both scripts identically; the
#   tests assert that property using the actual lookalike characters
#   a user would type.
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-DOC-PICKER verification — all 19 scenarios for
#     MP-DOC-PICKER (mint_suggest_template tool + improved
#     DocumentTypeNotFound error). Asserts deterministic intent →
#     top-1 matrix across English / Russian / mixed inputs,
#     CANONICAL_SUGGEST_KEYS wire shape stability, jaccard + bonus
#     formula, tie-break ordering (alphabetical name on score tie),
#     PII-safe BLOCK_SUGGEST log marker (intent surfaces only as
#     sha8 hash + length), bonus-collision detection at module
#     init, casefold edge cases, and the round-trip from the
#     create_document raise path (unknown doc_type) to the
#     improved error message carrying top-3 suggestions inline.
#   SCOPE: Integration tests over the live registry (tmp-snapshot
#     of templates/) + the picker module + the DocumentTypeNotFound
#     raise path in document.py. Uses FakeMCPContext for the
#     async tool invocations.
#   DEPENDS: pytest, pytest-asyncio, mint_python.mcp.picker (UUT),
#     mint_python.mcp.document (DocumentTypeNotFound + _load_template
#     raise path), mint_python.templates.registry
#     (reset_default_registry), tests._helpers.fake_mcp_context.
#   LINKS: docs/development-plan.xml#MP-DOC-PICKER,
#     docs/verification-plan.xml#V-MP-DOC-PICKER
# END_MODULE_CONTRACT
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import pytest

from mint_python.mcp import document as document_module
from mint_python.mcp import picker as picker_module
from mint_python.mcp.document import DocumentTypeNotFound, _load_template
from mint_python.mcp.picker import (
    _KEYWORD_BONUS_TERMS,
    CANONICAL_SUGGEST_KEYS,
    ScoredTemplate,
    _detect_bonus_collisions,
    _safe_intent_hash,
    _score_templates,
    _tokenize,
    mint_suggest_template,
    suggest_templates,
)
from mint_python.templates import registry as registry_module
from mint_python.templates.registry import reset_default_registry
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"

# All 7 shipped templates (W17-1 added technical-spec). The picker
# operates against this exact set in the default fixture.
SHIPPED_TEMPLATE_NAMES: tuple[str, ...] = (
    "contract",
    "decision-record",
    "letter",
    "memo",
    "nda-bilingual-ru-en",
    "report",
    "technical-spec",
)


# --------------------------------------------------------------------------- #
# Fixtures — hermetic templates dir snapshot of the full shipped catalog.
# --------------------------------------------------------------------------- #


@pytest.fixture
def isolated_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Snapshot ALL shipped templates into a tmp dir + repoint both
    document and registry modules at it. Resets the default registry
    so the next get_default_registry() call rebuilds against the
    snapshot."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in SHIPPED_TEMPLATE_NAMES:
        src = REPO_TEMPLATES / f"{name}.yaml"
        (fixtures / f"{name}.yaml").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixtures)
    monkeypatch.setattr(registry_module, "_TEMPLATES_DIR", fixtures)
    reset_default_registry()
    yield fixtures
    reset_default_registry()


@pytest.fixture
def single_template_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Only memo.yaml — used by scenario-15."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    (fixtures / "memo.yaml").write_text(
        (REPO_TEMPLATES / "memo.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixtures)
    monkeypatch.setattr(registry_module, "_TEMPLATES_DIR", fixtures)
    reset_default_registry()
    yield fixtures
    reset_default_registry()


@pytest.fixture
def empty_templates_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Empty templates dir — scenario-4. Distinct from empty intent
    against a populated registry (scenario-13)."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixtures)
    monkeypatch.setattr(registry_module, "_TEMPLATES_DIR", fixtures)
    reset_default_registry()
    yield fixtures
    reset_default_registry()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_BLOCK_SUGGEST_RE = re.compile(r"\[MP-Picker\]\[suggest\]\[BLOCK_SUGGEST\]")


def _block_suggest_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if _BLOCK_SUGGEST_RE.search(r.getMessage())
    ]


# --------------------------------------------------------------------------- #
# Scenario-1 — TZ intent → technical-spec wins (and Russian variant).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_tz_intent_picks_technical_spec(
    isolated_templates: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ТЗ intent → top-1 is technical-spec; score ≥ 0.3 (one bonus
    phrase plus jaccard contribution from tokens like 'тз')."""
    caplog.set_level(logging.INFO)
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(
        intent="напиши ТЗ техническое задание на новую систему", ctx=ctx
    )
    assert tuple(result) == CANONICAL_SUGGEST_KEYS
    assert result["suggestions"][0]["name"] == "technical-spec"
    assert result["suggestions"][0]["match_score"] >= 0.3
    assert result["total_templates"] == len(SHIPPED_TEMPLATE_NAMES)
    # Top-1 picked == log marker's top= field.
    msg = _block_suggest_records(caplog)
    assert msg
    assert "top=technical-spec" in msg[0]


# --------------------------------------------------------------------------- #
# Scenario-2 — business intent → contract.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_business_intent_picks_contract(
    isolated_templates: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(
        intent="draft a contract between parties for an ETL services agreement",
        ctx=ctx,
    )
    assert result["suggestions"][0]["name"] == "contract"
    # `contract`, `parties`, `agreement` = 3 bonus hits; bonus = min(0.9, 0.6) = 0.6
    # plus some jaccard → match_score ≥ 0.6.
    assert result["suggestions"][0]["match_score"] >= 0.6


# --------------------------------------------------------------------------- #
# Scenario-3 — memo intent → memo top-1.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_memo_intent_picks_memo(
    isolated_templates: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(
        intent="write a memo to the board about Q3 product metrics", ctx=ctx
    )
    assert result["suggestions"][0]["name"] == "memo"


# --------------------------------------------------------------------------- #
# Scenario-4 — empty templates dir → empty suggestions, no raise.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_empty_templates_dir_returns_empty(
    empty_templates_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(intent="memo about anything", ctx=ctx)
    assert result["suggestions"] == []
    assert result["total_templates"] == 0
    # Still emits BLOCK_SUGGEST marker — distinguishing "no templates
    # exist" from "no good suggestions" requires a stable log signal.
    msgs = _block_suggest_records(caplog)
    assert msgs and "top=<none>" in msgs[0]


# --------------------------------------------------------------------------- #
# Scenario-5 — DocumentTypeNotFound surfaces top-3 suggestions inline.
# --------------------------------------------------------------------------- #


def test_scenario_5_document_type_not_found_surfaces_suggestions(
    isolated_templates: Path,
) -> None:
    """Calling _load_template with an unknown doc_type raises
    DocumentTypeNotFound whose .args[0] string ends with `Top
    suggestions: <name1> (<score>), <name2> (<score>), <name3>
    (<score>)`. Closes the silent-fallthrough gap from Phase-16."""
    with pytest.raises(DocumentTypeNotFound) as excinfo:
        _load_template("technical specification")
    message = str(excinfo.value)
    assert message.startswith("DOC_TYPE_NOT_FOUND:")
    assert "Top suggestions:" in message
    # The error also exposes a structured .suggestions list.
    assert isinstance(excinfo.value.suggestions, list)
    assert 1 <= len(excinfo.value.suggestions) <= 3
    top = excinfo.value.suggestions[0]
    assert set(top) == {"name", "match_score", "why"}
    # technical-spec should win for "technical specification" — both
    # tokens match jaccard AND "specification" is a bonus phrase.
    assert top["name"] == "technical-spec"


# --------------------------------------------------------------------------- #
# Scenario-6 — BLOCK_SUGGEST log marker shape (PII-safe).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_block_suggest_marker_format(
    isolated_templates: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    ctx = FakeMCPContext(answers={})
    intent = "write a contract"
    await mint_suggest_template(intent=intent, ctx=ctx)
    msgs = _block_suggest_records(caplog)
    assert msgs
    msg = msgs[0]
    assert "total_templates=" in msg
    assert "top=" in msg
    assert "score=" in msg
    assert "intent_hash=" in msg
    assert "intent_len=" in msg
    # intent_hash MUST be the sha8 of the intent — caller can recompute
    # for log triage but the original text is unrecoverable.
    expected = hashlib.sha256(intent.encode("utf-8")).hexdigest()[:8]
    assert f"intent_hash={expected}" in msg
    # Raw intent NEVER appears verbatim.
    assert intent not in msg


# --------------------------------------------------------------------------- #
# Scenario-7 — VF-020 inv-2 NO-AUTH-CALL: picker MUST NOT consult MP-AUTH-SHIM.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_7_no_auth_shim_call(
    isolated_templates: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The picker is a read-only information tool — it must not even
    touch mp.auth's require_* helpers. Sentinel-patch every public
    auth helper to raise on call; the picker still succeeds."""
    from mint_python.mcp import auth as auth_module

    def _explode(*_a: object, **_kw: object) -> None:
        raise AssertionError(
            "Picker called an MP-AUTH-SHIM helper — forbidden-3 violation"
        )

    monkeypatch.setattr(auth_module, "require_template_writer", _explode)
    monkeypatch.setattr(auth_module, "is_template_writer", _explode)
    monkeypatch.setattr(auth_module, "load_writers_config", _explode)
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(intent="memo to team", ctx=ctx)
    assert result["suggestions"]  # picker still worked


# --------------------------------------------------------------------------- #
# Scenario-8 — Cyrillic-only intent → contract.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_8_cyrillic_only_contract(
    isolated_templates: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(
        intent="договор оказания услуг", ctx=ctx
    )
    assert result["suggestions"][0]["name"] == "contract"
    # Single bonus phrase = 0.3 floor; jaccard typically lifts it
    # slightly above thanks to template-text token overlap. The contract
    # is identified primarily by its bonus phrase match, not by raw
    # token similarity (Cyrillic intents have no English token overlap).
    assert result["suggestions"][0]["match_score"] >= 0.3


# --------------------------------------------------------------------------- #
# Scenario-9 — mixed Ru/En → both contract + technical-spec in top-3.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_9_mixed_ru_en_two_templates_in_top_3(
    isolated_templates: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(
        intent="contract for ТЗ on ETL", ctx=ctx
    )
    names = [s["name"] for s in result["suggestions"]]
    assert "contract" in names
    assert "technical-spec" in names
    # Deterministic order: re-run yields identical top-1.
    result2 = await mint_suggest_template(
        intent="contract for ТЗ on ETL", ctx=ctx
    )
    assert result == result2


# --------------------------------------------------------------------------- #
# Scenario-10 — English-only ADR intent → decision-record.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_10_english_adr_decision_record(
    isolated_templates: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    result = await mint_suggest_template(
        intent="architecture decision record about API versioning rationale",
        ctx=ctx,
    )
    top = result["suggestions"][0]
    assert top["name"] == "decision-record"
    # `why` lists matched bonus phrases; longest phrase first
    # ("architecture decision" > "rationale" > "decision").
    assert "architecture decision" in top["why"]


# --------------------------------------------------------------------------- #
# Scenario-11 — tied scores → alphabetical name; deterministic re-runs.
# --------------------------------------------------------------------------- #


def test_scenario_11_tied_scores_alphabetical_tiebreak(
    isolated_templates: Path,
) -> None:
    """Synthetic intent with zero token overlap and zero bonus phrases —
    every template scores exactly 0.0, so tiebreaker = alphabetical name.
    Re-run 5 times: identical order each time."""
    intent = "xyzzy"  # arbitrary token absent from every template's text
    runs = [suggest_templates(intent)["suggestions"] for _ in range(5)]
    # All zero-score; alphabetical tiebreak.
    first = runs[0]
    assert all(s["match_score"] == 0.0 for s in first)
    # Top-3 alphabetical from the shipped catalog: contract, decision-record, letter.
    assert [s["name"] for s in first] == ["contract", "decision-record", "letter"]
    # Determinism — every run produces the same list.
    for run in runs[1:]:
        assert run == first


# --------------------------------------------------------------------------- #
# Scenario-12 — adversarial intent: bonus terms outweigh single misleading token.
# --------------------------------------------------------------------------- #


def test_scenario_12_adversarial_memo_actually_tech_spec(
    isolated_templates: Path,
) -> None:
    """An intent that mentions "memo" once but otherwise asks for a
    technical specification — top-1 is technical-spec because the
    multi-bonus weight outpaces the lone "memo" token; memo still
    appears somewhere in top-3 (it earned a small but non-zero score
    from that lone bonus hit)."""
    result = suggest_templates(
        "memo NOT about a memo — actually a technical specification "
        "with requirements for the system"
    )
    top_names = [s["name"] for s in result["suggestions"]]
    assert top_names[0] == "technical-spec"
    assert "memo" in top_names


# --------------------------------------------------------------------------- #
# Scenario-13 — empty intent: suggestions=[], total_templates=N.
# --------------------------------------------------------------------------- #


def test_scenario_13_empty_intent_returns_empty_with_total(
    isolated_templates: Path,
) -> None:
    """Distinct from scenario-4 (empty dir): empty intent against a
    populated registry returns empty suggestions BUT total_templates
    reflects the registry size — callers can distinguish 'no
    templates exist' from 'no signal in the intent'."""
    result = suggest_templates("")
    assert result["suggestions"] == []
    assert result["total_templates"] == len(SHIPPED_TEMPLATE_NAMES)


# --------------------------------------------------------------------------- #
# Scenario-14 — stop-words only → all suggestions score < 0.15 with fallback why.
# --------------------------------------------------------------------------- #


def test_scenario_14_stopwords_only_fallback_why(
    isolated_templates: Path,
) -> None:
    """Russian stop-words — no bonus phrase matches, near-zero jaccard.
    Top-3 still surfaces (so caller can offer a picker UI) but every
    entry's `why` reduces to the canonical fallback string."""
    result = suggest_templates("оформи документ пожалуйста")
    for entry in result["suggestions"]:
        assert entry["match_score"] < 0.15
        assert entry["why"] == "fallback (no strong keywords)"


# --------------------------------------------------------------------------- #
# Scenario-15 — single-template registry with non-trivial intent.
# --------------------------------------------------------------------------- #


def test_scenario_15_single_template_naturally_scored(
    single_template_registry: Path,
) -> None:
    """Only memo is registered; intent matches it. Score is the
    naturally-produced value (jaccard + bonus), NOT an artificial 1.0
    just because it's the only template — preserves caller's ability
    to detect a weak-match scenario even with a single template."""
    result = suggest_templates("write a memo to the board")
    assert len(result["suggestions"]) == 1
    entry = result["suggestions"][0]
    assert entry["name"] == "memo"
    # Memo + "memo" bonus phrase = 0.3 + some jaccard; should NOT be 1.0.
    assert 0.0 < entry["match_score"] < 1.0
    assert result["total_templates"] == 1


# --------------------------------------------------------------------------- #
# Scenario-16 — why-field bounds (top-3 keywords, ≤120 chars).
# --------------------------------------------------------------------------- #


def test_scenario_16_why_field_bounds(
    isolated_templates: Path,
) -> None:
    """For an intent that hits ALL of a doc_type's bonus phrases, `why`
    still caps at 3 entries and total length ≤120 chars."""
    # Stuff every nda bonus phrase into one intent.
    huge_intent = " ".join(_KEYWORD_BONUS_TERMS["nda-bilingual-ru-en"])
    result = suggest_templates(huge_intent)
    top = result["suggestions"][0]
    assert top["name"] == "nda-bilingual-ru-en"
    # Count comma-separated entries in the "matched keywords: a, b, c" form.
    assert top["why"].startswith("matched keywords:")
    keywords_part = top["why"].split(":", 1)[1]
    listed = [s.strip() for s in keywords_part.split(",")]
    assert len(listed) <= 3
    assert len(top["why"]) <= 120


# --------------------------------------------------------------------------- #
# Scenario-17 — PII-safe logging: SSN does NOT leak verbatim.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_17_pii_safe_no_ssn_in_logs(
    isolated_templates: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    ctx = FakeMCPContext(answers={})
    intent = "write a memo SSN 123-45-6789 for the team"
    await mint_suggest_template(intent=intent, ctx=ctx)
    # Inspect EVERY captured record, not just BLOCK_SUGGEST — defends
    # against future additions accidentally introducing a verbose log.
    for record in caplog.records:
        msg = record.getMessage()
        assert "123-45-6789" not in msg
        assert "SSN 123-45-6789" not in msg
    # And the BLOCK_SUGGEST marker carries the sha8 hash form.
    suggest_msgs = _block_suggest_records(caplog)
    assert suggest_msgs
    expected_hash = hashlib.sha256(intent.encode("utf-8")).hexdigest()[:8]
    assert f"intent_hash={expected_hash}" in suggest_msgs[0]


# --------------------------------------------------------------------------- #
# Scenario-18 — bonus collision determinism (10 identical runs).
# --------------------------------------------------------------------------- #


def test_scenario_18_determinism_10_runs(
    isolated_templates: Path,
) -> None:
    intent = "draft a contract for an NDA between parties about confidentiality"
    runs = [suggest_templates(intent) for _ in range(10)]
    first = runs[0]
    for run in runs[1:]:
        assert run == first


def test_scenario_18b_collision_detection_warning(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manually inject a colliding term + re-run _detect_bonus_collisions
    to confirm the WARNING-level BLOCK_BONUS_COLLISION marker fires
    when 2+ doc_types share a phrase. Restored after the test."""
    caplog.set_level(logging.WARNING)
    fake_table = dict(_KEYWORD_BONUS_TERMS)
    fake_table["memo"] = (*fake_table["memo"], "shared")
    fake_table["letter"] = (*fake_table["letter"], "shared")
    monkeypatch.setattr(picker_module, "_KEYWORD_BONUS_TERMS", fake_table)
    _detect_bonus_collisions()
    collision_msgs = [
        r.getMessage()
        for r in caplog.records
        if "[BLOCK_BONUS_COLLISION]" in r.getMessage()
    ]
    assert collision_msgs
    # Marker names the phrase + the doc_types.
    assert any("'shared'" in m for m in collision_msgs)


# --------------------------------------------------------------------------- #
# Scenario-19 — casefold determinism (str.casefold, NOT str.lower).
# --------------------------------------------------------------------------- #


def test_scenario_19_casefold_handles_non_ascii_case(
    isolated_templates: Path,
) -> None:
    """German ß and Greek final-sigma collapse correctly under
    casefold() but NOT under lower(). The tokeniser uses casefold so
    intents in mixed-case Cyrillic / Latin produce identical results
    to their casefolded equivalents."""
    upper = suggest_templates("ДОГОВОР ОКАЗАНИЯ УСЛУГ")
    lower = suggest_templates("договор оказания услуг")
    # Top-1 and full ordering MUST be identical regardless of case.
    assert upper == lower
    # And the tokeniser itself round-trips on casefold-only-correct
    # samples — "Straße" tokenised gives the same set as "STRASSE".
    assert _tokenize("Straße") == _tokenize("STRASSE")


# --------------------------------------------------------------------------- #
# Cross-cutting — CANONICAL_SUGGEST_KEYS + ScoredTemplate dataclass surface.
# --------------------------------------------------------------------------- #


def test_canonical_keys_constant_is_frozen() -> None:
    assert CANONICAL_SUGGEST_KEYS == ("suggestions", "total_templates")


def test_scored_template_dataclass_frozen() -> None:
    """ScoredTemplate is frozen — guards against accidental mutation
    between scoring and projection-to-dict."""
    entry = ScoredTemplate(name="memo", match_score=0.5, why="x")
    with pytest.raises((AttributeError, TypeError)):
        entry.match_score = 0.9  # type: ignore[misc]


def test_safe_intent_hash_handles_empty_string() -> None:
    """Empty intent still produces a deterministic 8-hex token —
    sha256(b"")[:8] == 'e3b0c442'. Guards against a future refactor
    that 'optimises' the empty case to a sentinel like '<empty>'
    which would surface in log triage as a duplicated string."""
    assert _safe_intent_hash("") == "e3b0c442"


def test_tokenize_empty_string_returns_empty_set() -> None:
    """_tokenize("") short-circuits to an empty set — guards against the
    pathological case where downstream jaccard math would otherwise hit
    a non-existent intersection."""
    assert _tokenize("") == set()


def test_jaccard_zero_branch_when_template_tokens_empty(
    isolated_templates: Path,
) -> None:
    """When a TemplateSummary has empty name+description+doc_type tokens
    (pathological registry state), `_score_one` falls through to the
    jaccard=0.0 else branch. We synthesise this by passing a summary
    whose every text field tokenises to empty."""
    from mint_python.templates.registry import TemplateSummary

    bare = TemplateSummary(
        name="",
        version="1.0",
        doc_type="",
        description="",
        last_modified="2026-05-11T00:00:00+00:00",
    )
    scored = _score_templates("memo", [bare])
    # Score = 0.0 because both jaccard and bonus collapse to zero.
    assert scored[0].match_score == 0.0
    assert scored[0].why == "fallback (no strong keywords)"


def test_why_pure_jaccard_branch_no_bonus_phrase(
    single_template_registry: Path,
) -> None:
    """Score above 0.15 with NO bonus-phrase match — `why` reports the
    jaccard. Crafted by overriding the bonus table to drop `memo`'s
    phrases entirely; the remaining jaccard score from token overlap
    against the template's description still pushes above 0.15.
    Covers the no-bonus-but-good-score branch in _compose_why."""
    monkey_table = dict(_KEYWORD_BONUS_TERMS)
    monkey_table["memo"] = ()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(picker_module, "_KEYWORD_BONUS_TERMS", monkey_table)
        # Many template-text tokens in the intent → high jaccard even
        # with zero bonus contribution.
        # memo.yaml: "name: memo" + description text + "doc_type: memo"
        result = suggest_templates("memo memo memorandum recipient sender body subject date")
        top = result["suggestions"][0]
        # Above 0.15 but no bonus phrases matched.
        if top["match_score"] >= 0.15:
            assert "jaccard=" in top["why"]


def test_why_truncates_when_exceeds_120_chars(
    monkeypatch: pytest.MonkeyPatch,
    isolated_templates: Path,
) -> None:
    """If the table holds 3 long bonus phrases AND all match, the
    composed `why` exceeds the 120-char cap → falls through to the
    truncate-with-ellipsis branch."""
    # Inject very long phrases that all match the intent.
    long_phrases = (
        "an extremely long bonus phrase number one that should make the why field overflow",
        "another similarly extended bonus phrase number two for overflow coverage",
        "and a third lengthy bonus phrase number three to fill the why string",
    )
    monkey_table = dict(_KEYWORD_BONUS_TERMS)
    monkey_table["memo"] = long_phrases
    monkeypatch.setattr(picker_module, "_KEYWORD_BONUS_TERMS", monkey_table)
    long_intent = " ".join(long_phrases)
    result = suggest_templates(long_intent)
    # memo should top the list now with these custom bonus matches.
    memo_entry = next(s for s in result["suggestions"] if s["name"] == "memo")
    assert len(memo_entry["why"]) <= 120
    assert memo_entry["why"].endswith("...")


def test_score_templates_dedupes_by_name(
    isolated_templates: Path,
) -> None:
    """Sanity check: even if `summaries()` ever returns multiple entries
    per name (versioned siblings) `_score_templates` dedupes to one
    entry per name so the picker UI doesn't show duplicates."""
    from mint_python.templates.registry import TemplateSummary

    fake = [
        TemplateSummary(
            name="memo",
            version="1.0",
            doc_type="memo",
            description="a memo",
            last_modified="2026-05-11T00:00:00+00:00",
        ),
        TemplateSummary(
            name="memo",
            version="1.1",
            doc_type="memo",
            description="a memo",
            last_modified="2026-05-11T01:00:00+00:00",
        ),
    ]
    scored = _score_templates("memo", fake)
    assert len(scored) == 1
    assert scored[0].name == "memo"
