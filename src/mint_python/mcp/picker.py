# FILE: src/mint_python/mcp/picker.py
# VERSION: 0.1.0
# ruff: noqa: RUF001, RUF002, RUF003
#   ^ multilingual bonus-term table (English + Russian) intentionally
#   carries Cyrillic characters that look-alike with Latin (о vs o,
#   Т vs T, З vs 3). Ambiguity is REAL but intentional — the table
#   matches whichever script the user typed; we accept lookalikes.
# START_MODULE_CONTRACT
#   PURPOSE: Phase-17 Wave-17-2 (MP-DOC-PICKER) — ship the
#     `mint_suggest_template` MCP tool that returns the top-3
#     keyword-scored template alternatives for a free-text intent.
#     Closes the Phase-16 production gap where MINT silently fell
#     through to the closest available template instead of asking
#     ("MINT helps Claude pick well — but Claude still picks").
#     Pure read-only INTEGRATION wrap over the existing template
#     registry: combines a jaccard similarity over tokenised
#     intent+template text with a per-doc_type keyword-bonus table
#     to surface the most relevant doc_type and a human-readable
#     `why` string. Same scoring also drives the improved
#     DocumentTypeNotFound message in mcp.document so connected
#     models see suggestions inline at the create_document boundary
#     when they pass an unknown doc_type.
#   SCOPE: Public surface = `mint_suggest_template` (FastMCP tool),
#     `suggest_templates` (sync helper consumed by document.py's
#     DocumentTypeNotFound improvement), `ScoredTemplate` frozen
#     dataclass, `CANONICAL_SUGGEST_KEYS` tuple (stable wire shape).
#   DEPENDS: fastmcp (Context), mint_python.templates.registry
#     (get_default_registry + TemplateSummary), mint_python.mcp
#     .document (server — shared FastMCP instance).
#   LINKS: docs/development-plan.xml#MP-DOC-PICKER,
#     docs/verification-plan.xml#V-MP-DOC-PICKER,
#     docs/knowledge-graph.xml#MP-DOC-PICKER
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   _KEYWORD_BONUS_TERMS         - frozen dict[doc_type -> tuple[phrase]];
#                                  per-doc_type bonus terms applied as
#                                  substring matches against the
#                                  normalised intent.
#   CANONICAL_SUGGEST_KEYS       - tuple of the two top-level keys in the
#                                  tool's return dict; stable wire shape.
#   ScoredTemplate               - frozen dataclass (name, match_score,
#                                  why); the in-memory shape before the
#                                  tool projects out to plain dicts.
#   _detect_bonus_collisions     - module-init check; warns when any
#                                  bonus phrase maps to >=2 doc_types
#                                  (forbidden-7 collision determinism).
#   _tokenize                    - casefold + split on [\s\W]+ + drop
#                                  empties; the tokeniser shared by
#                                  intent and template text.
#   _score_templates             - per-template (jaccard + bonus) score
#                                  computation, returns a sorted list of
#                                  ScoredTemplate (desc score, asc name
#                                  tiebreak).
#   _safe_intent_hash            - PII-safe log token: sha8(intent) so
#                                  the BLOCK_SUGGEST marker never carries
#                                  the raw intent.
#   suggest_templates            - sync helper; the test-friendly core
#                                  consumed by the improved
#                                  DocumentTypeNotFound message in
#                                  document.py.
#   mint_suggest_template        - @server.tool async fn; the production
#                                  MCP entry registered on the shared
#                                  `server` from mcp.document.
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — initial Phase-17 Wave-17-2 implementation per
#     V-MP-DOC-PICKER scenarios 1-19 (multilingual intent matrix, tied-
#     score determinism, PII-safe logs, bonus-collision detection at
#     module init, casefold edge cases). Improves document.py's
#     DocumentTypeNotFound to surface the same top-3 suggestions inline.
#     review-fix: 7 mechanical simplifications from code-simplifier agent.
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from fastmcp import Context

from mint_python.mcp.document import server
from mint_python.mcp.telemetry import track_call
from mint_python.templates.registry import (
    TemplateSummary,
    get_default_registry,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Frozen bonus-term table (V-MP-DOC-PICKER keyword-bonus-terms block)
# --------------------------------------------------------------------------- #
#
# Per-doc_type substring phrases that trigger the bonus contribution to
# raw_score (see scoring formula below). Matched as casefolded substrings
# against the normalised intent — multi-word phrases like "architecture
# decision" do NOT have to align to token boundaries. The table is
# intentionally small + curated; expansion is a docs-driven change (touch
# this constant + add a regression scenario in V-MP-DOC-PICKER).
#
# Languages covered: English + Russian. The bilingual NDA template uses
# the same Latin "nda" + Cyrillic "соглашение о неразглашении" pair so
# either-language intents pick it cleanly. The "technical-spec" bucket
# carries both "ТЗ"/"tz" and "техническое задание"/"technical
# specification" so engineering intents in either language hit the right
# template (closes the Phase-16 production gap where "напиши ТЗ" fell
# through to the closest available memo).

_KEYWORD_BONUS_TERMS: dict[str, tuple[str, ...]] = {
    "memo": ("memo", "memorandum", "служебная записка", "докладная", "записка"),
    "letter": ("letter", "письмо", "обращение", "запрос", "request"),
    "report": (
        "report",
        "отчёт",
        "отчет",
        "summary",
        "результаты",
        "findings",
    ),
    "decision-record": (
        "adr",
        "decision",
        "решение",
        "architecture decision",
        "архитектурное решение",
        "rationale",
    ),
    "contract": (
        "contract",
        "договор",
        "agreement",
        "соглашение",
        "parties",
        "стороны",
    ),
    "nda-bilingual-ru-en": (
        "nda",
        "non-disclosure",
        "соглашение о неразглашении",
        "конфиденциальность",
        "confidentiality",
        "bilingual",
    ),
    "technical-spec": (
        "тз",
        "tz",
        "technical spec",
        "technical specification",
        "requirements",
        "техническое задание",
        "требования",
        "specification",
    ),
}


# Stable wire shape — the two top-level keys in the tool's return dict.
# Tests assert against this constant rather than literal strings so a
# rename of a top-level key would surface at the constant declaration
# AND ripple through every consumer at type-check time.
CANONICAL_SUGGEST_KEYS: tuple[str, ...] = ("suggestions", "total_templates")


# --------------------------------------------------------------------------- #
# Collision detection at module init (forbidden-7)
# --------------------------------------------------------------------------- #


def _detect_bonus_collisions() -> None:
    """Warn at module-import time if any bonus phrase maps to ≥2 doc_types.

    Build the inverse map `phrase -> set[doc_type]`; any phrase with
    cardinality ≥2 is a collision and emits a BLOCK_BONUS_COLLISION marker
    at WARNING level. The function does NOT raise — collisions are
    intentionally tolerated for now (e.g. a future bilingual letter
    might share "запрос" with the canonical letter bucket) — but
    they ARE surfaced so operators see them in their server-startup
    logs. Forbidden-7 / V-MP-DOC-PICKER scenario-18 requirement.
    """
    inverse: dict[str, set[str]] = {}
    for doc_type, phrases in _KEYWORD_BONUS_TERMS.items():
        for phrase in phrases:
            inverse.setdefault(phrase, set()).add(doc_type)
    for phrase, doc_types in inverse.items():
        if len(doc_types) >= 2:
            logger.warning(
                "[MP-Picker][init][BLOCK_BONUS_COLLISION] "
                "keyword=%r doc_types=%s",
                phrase,
                sorted(doc_types),
            )


_detect_bonus_collisions()


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoredTemplate:
    """One template's score against an intent.

    Attributes:
        name: Template name as registered (matches TemplateSummary.name).
        match_score: Clamped to [0, 1], rounded to 3 decimals so wire
            equality + caplog assertions are deterministic.
        why: One-line explanation — names matched bonus phrases sorted
            by their contribution length OR "fallback (no strong
            keywords)" when match_score < 0.15.
    """

    name: str
    match_score: float
    why: str


# Token splitter — non-word characters (incl. Cyrillic apostrophes,
# CJK punctuation, ASCII hyphens, em-dashes) all collapse to splits.
# `re.UNICODE` is the default on str patterns in Python 3 but spelt
# explicitly here for the human reader.
_TOKEN_SPLIT_RE = re.compile(r"[\s\W]+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """Casefold + split + drop empties.

    Uses `str.casefold()` (NOT `str.lower()`) so the German ß, Greek
    final-sigma, and Cyrillic edge cases case-fold correctly per
    V-MP-DOC-PICKER scenario-19. Returns a set so downstream
    jaccard math doesn't have to deduplicate.

    Empty / whitespace-only input returns an empty set rather than a
    set containing the empty string — the bonus-only path in
    `_score_templates` relies on this to deterministically score
    stop-word-only intents (scenario-14) below the 0.15 fallback
    threshold.
    """
    if not text:
        return set()
    folded = text.casefold()
    tokens = _TOKEN_SPLIT_RE.split(folded)
    return {tok for tok in tokens if tok}


def _summary_text_tokens(summary: TemplateSummary) -> set[str]:
    """Union of tokens from name + description + doc_type.

    Forbidden-6: NEVER load the template's `layout` field — list_summaries
    surfaces only the lightweight projection (no layout), so this stays
    structurally honest. Forbidden-8: defensively coerce non-string
    descriptions via `str()` rather than raising; a corrupted template
    upstream is the registry's problem, not the picker's.
    """
    try:
        desc = str(summary.description) if summary.description else ""
    except Exception:  # pragma: no cover — defensive against pathological __str__
        logger.debug("[MP-Picker] description coercion failed", exc_info=True)
        desc = ""
    return _tokenize(summary.name) | _tokenize(summary.doc_type) | _tokenize(desc)


def _bonus_hits(intent_normalised: str, doc_type: str) -> tuple[int, list[str]]:
    """Substring scan over the normalised intent for one doc_type's
    bonus phrases. Multi-word phrases ARE matched as substrings, not
    tokenwise, so "architecture decision" fires on "an architecture
    decision about..." even though the intervening "an" would split a
    pure tokenwise match.

    Returns:
        (hit_count, matched_phrases) — matched_phrases preserves the
        canonical (table-order) form, NOT the user's spelling. Used by
        `_compose_why` to produce a stable `why` field.
    """
    phrases = _KEYWORD_BONUS_TERMS.get(doc_type, ())
    matched = [p for p in phrases if p.casefold() in intent_normalised]
    return len(matched), matched


def _compose_why(
    score: float,
    matched_phrases: list[str],
    jaccard: float,
) -> str:
    """Build the `why` string for a ScoredTemplate.

    Below the 0.15 fallback threshold: a single canonical
    "fallback (no strong keywords)" string. Above the threshold: list
    the top bonus phrases sorted by contribution (longer phrases first
    so multi-word matches surface above single-word ones), capped at 3
    entries, with a jaccard hint. Length capped at 120 chars per
    V-MP-DOC-PICKER scenario-16.
    """
    if score < 0.15:
        return "fallback (no strong keywords)"
    if not matched_phrases:
        # Score >= 0.15 with no bonus phrases — pure jaccard match
        # (single-template registry can produce this when intent shares
        # tokens with the template's name/description but no bonus
        # phrase appears).
        return f"matched on name/description (jaccard={jaccard:.2f})"
    # Sort by phrase length desc (longest phrase contributes most
    # information), tiebreak by alphabetical for determinism.
    ordered = sorted(matched_phrases, key=lambda p: (-len(p), p))[:3]
    joined = ", ".join(ordered)
    why = f"matched keywords: {joined}"
    if len(why) > 120:
        return why[:117] + "..."
    return why


def _score_one(
    intent_tokens: set[str],
    intent_normalised: str,
    summary: TemplateSummary,
) -> ScoredTemplate:
    """Compute one template's score against an intent.

    Scoring formula (frozen — V-MP-DOC-PICKER scoring-formula block):

        jaccard       = |intent ∩ template| / |intent ∪ template|
        bonus         = min(0.3 * bonus_hits, 0.6)
        raw_score     = 0.4 * jaccard + bonus
        match_score   = round(clamp(raw_score, 0, 1), 3)

    Below 0.15 the `why` collapses to the canonical fallback string;
    above 0.15 it lists the matched phrases sorted by length descending.
    """
    template_tokens = _summary_text_tokens(summary)
    if intent_tokens and template_tokens:
        intersection = len(intent_tokens & template_tokens)
        union = len(intent_tokens | template_tokens)
        jaccard = intersection / union if union else 0.0
    else:
        jaccard = 0.0
    hit_count, matched_phrases = _bonus_hits(intent_normalised, summary.doc_type)
    bonus = min(0.3 * hit_count, 0.6)
    raw = 0.4 * jaccard + bonus
    clamped = max(0.0, min(1.0, raw))
    rounded = round(clamped, 3)
    why = _compose_why(rounded, matched_phrases, jaccard)
    return ScoredTemplate(name=summary.name, match_score=rounded, why=why)


def _score_templates(
    intent: str,
    summaries: list[TemplateSummary],
) -> list[ScoredTemplate]:
    """Score every template, sort desc by score with alphabetical name
    tiebreak (V-MP-DOC-PICKER scenario-11 determinism).

    Empty intent / empty summaries — return an empty list. The
    public `suggest_templates` wrapper preserves the `total_templates`
    count even when `suggestions` is empty so callers can distinguish
    "no templates exist" (scenario-4: total_templates=0) from "no
    signal in the intent" (scenario-13: total_templates=N, but
    suggestions=[] because we refuse to surface an arbitrary
    alphabetical top-3 with all-zero scores when the caller gave
    us nothing to work with).
    """
    if not summaries:
        return []
    if not intent or not intent.strip():
        # Empty intent — non-failing per forbidden-1, but we explicitly
        # decline to surface an alphabetical top-3 of all-zero-score
        # entries (which would be confusing UX: the picker would
        # appear to "recommend" contract for every empty call). The
        # caller still sees total_templates=N so they can detect the
        # registry is populated.
        return []
    # The intent gets casefolded ONCE before both the tokenwise jaccard
    # path and the substring bonus path — the bonus phrases in the
    # table are already in their canonical (lowercase) form, and
    # `_bonus_hits` calls `.casefold()` on each phrase too as
    # belt-and-braces.
    intent_normalised = intent.casefold()
    intent_tokens = _tokenize(intent)
    scored = [
        _score_one(intent_tokens, intent_normalised, summary)
        for summary in summaries
    ]
    # Dedupe by name (a template with versioned siblings shows up
    # multiple times in `summaries()` — see resources.py line 247-252
    # which does the same dedup at the picker UI boundary). Keep the
    # FIRST occurrence which, because `summaries()` is sorted by name
    # then ascending semver, is the lowest-version baseline. That's
    # the canonical version a fresh picker UI should surface; explicit
    # version pinning goes through get_template.
    seen: set[str] = set()
    unique: list[ScoredTemplate] = []
    for entry in scored:
        if entry.name in seen:
            continue
        seen.add(entry.name)
        unique.append(entry)
    unique.sort(key=lambda s: (-s.match_score, s.name))
    return unique


# --------------------------------------------------------------------------- #
# PII-safe logging (forbidden-4)
# --------------------------------------------------------------------------- #


def _safe_intent_hash(intent: str) -> str:
    """Produce a privacy-safe log token for the intent.

    Uses sha256(intent)[:8] — 8 hex chars = 32 bits, plenty for
    log-triage de-duplication but cryptographically unrecoverable.
    The full intent NEVER lands in a log marker (forbidden-4 +
    V-MP-DOC-PICKER scenario-17 security-critical). For an empty
    intent the hash is still well-defined (sha256(b"")[:8]); the
    intent_len pairs with it in the BLOCK_SUGGEST marker so empty
    vs filled is distinguishable at log triage without leaking
    content.
    """
    return hashlib.sha256((intent or "").encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #


def suggest_templates(intent: str) -> dict[str, Any]:
    """Sync helper — score templates and return the canonical top-3 dict.

    Used by:
      - `mint_suggest_template` (the MCP tool wrapper below)
      - `DocumentTypeNotFound.__init__` in mcp.document (so the
        error message surfaces the same top-3 inline when the caller
        passes an unknown doc_type to create_document).

    Returns a dict with exactly the keys in CANONICAL_SUGGEST_KEYS.
    Non-failing: empty intent or empty registry → suggestions=[], with
    total_templates reflecting the (possibly zero) registry size
    (forbidden-1 — never raise from the picker; let the caller decide
    what to do with an empty result).
    """
    summaries = get_default_registry().summaries()
    scored = _score_templates(intent, summaries)
    top3 = scored[:3]
    # PII-safe BLOCK_SUGGEST marker — intent appears only as its sha8
    # hash + a length signal. The `top` field is the top-1 template
    # name (or "<none>" for empty result), which is bounded vocabulary
    # (a doc_type from the templates dir, never user-controlled text).
    top_name = top3[0].name if top3 else "<none>"
    top_score = top3[0].match_score if top3 else 0.0
    logger.info(
        "[MP-Picker][suggest][BLOCK_SUGGEST] "
        "total_templates=%d top=%s score=%.3f "
        "intent_hash=%s intent_len=%d",
        len(summaries),
        top_name,
        top_score,
        _safe_intent_hash(intent),
        len(intent or ""),
    )
    # Count unique-name templates so total_templates matches what the
    # picker actually scored (versioned siblings dedupe upstream).
    total = len({s.name for s in summaries})
    return {
        "suggestions": [
            {"name": t.name, "match_score": t.match_score, "why": t.why}
            for t in top3
        ],
        "total_templates": total,
    }


@server.tool(name="mint_suggest_template")
async def mint_suggest_template(
    intent: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Return the top-3 keyword-scored template alternatives for `intent`.

    Pure information tool — does NOT call create_document, does NOT
    consult MP-AUTH-SHIM, does NOT raise on empty/missing inputs.
    Connected models call this BEFORE committing to a doc_type so the
    user gets to confirm "is this what you wanted?" rather than
    receiving the wrong document via silent fall-through.

    Returns:
        Dict with the canonical two keys:
            - `suggestions`: list of up to 3 entries, each
              `{name: str, match_score: float ∈ [0,1], why: str}`,
              sorted descending by score (alphabetical name on tie).
            - `total_templates`: int — count of unique-name templates
              currently in the registry (distinct from len(suggestions)
              when the registry has more than 3 templates).
    """
    with track_call("mint_suggest_template"):
        del ctx  # reserved for future progress reporting; registry is
        # stateless per call so no context-bound state to thread.
        return suggest_templates(intent)


__all__ = [
    "CANONICAL_SUGGEST_KEYS",
    "ScoredTemplate",
    "mint_suggest_template",
    "suggest_templates",
]
