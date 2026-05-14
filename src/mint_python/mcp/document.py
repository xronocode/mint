# FILE: src/mint_python/mcp/document.py
# VERSION: 0.3.0
# START_MODULE_CONTRACT
#   PURPOSE: Generic FastMCP create_document tool — lifts the Phase-13
#     MEMO-POC pipeline out of its hardcoded "memo" shape into a doc-type-
#     agnostic generator. Caller passes intent + doc_type; the runtime
#     looks up templates/<doc_type>.yaml, runs the same heuristic + elicit
#     + build + GRACE flow that MEMO-POC validated. Adding Letter / Report
#     / Contract / Invoice becomes a templates/<name>.yaml file with no
#     Python changes. Memo continues to work via doc_type="memo"; the
#     create_memo tool (in mcp/memo.py) is a thin alias preserved for
#     existing Claude Desktop sessions.
#   SCOPE: Public surface = create_document (FastMCP tool), DocumentSpec
#     dataclass, DocumentTemplate dataclass, DocumentError /
#     DocumentElicitationRejected / DocumentTemplateNotFound /
#     DocumentTypeNotFound / DocumentGenerationFailed errors,
#     _run_pipeline (testable internal core), server (shared FastMCP
#     instance — mcp/memo.py reuses it for create_memo).
#   DEPENDS: fastmcp (Context, FastMCP), mint_python.core.document
#     (Document), mint_python.core.section (Section), mint_python.core
#     .table (Table), mint_python.core.content (Paragraph), mint_python
#     .adapters.markdown (markdown_to_spec for source_md and body
#     extraction), mint_python.grace (bootstrap for audit-trail
#     injection), pyyaml (templates loader).
#   LINKS: docs/development-plan.xml#MP-DOC-GENERIC,
#     docs/verification-plan.xml#V-MP-DOC-GENERIC,
#     docs/knowledge-graph.xml#MP-DOC-GENERIC
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   create_document              - @server.tool async fn; the production entry
#   DocumentSpec                 - frozen dataclass with the union of known
#                                  required fields (memo's 5 today;
#                                  W2 expands as Letter etc. land)
#   DocumentTemplate             - loaded templates/<doc_type>.yaml
#   DocumentError                - base exception
#   DocumentElicitationRejected  - user declined elicit
#   DocumentTemplateNotFound     - templates/<doc_type>.yaml missing
#   DocumentTypeNotFound         - doc_type unknown to the registry
#   DocumentGenerationFailed     - builder error after fields collected
#   MEMO_REQUIRED_FIELDS         - retained constant; memo's field order
#                                  for the heuristic's labelled-key map
#   _run_pipeline                - testable async core; takes log_prefix
#                                  so create_memo alias keeps MP-Memo
#                                  log markers (V-MP-MEMO-POC parity)
#   _detect_template_languages   - detect ≥2 lang suffixes in required
#                                  fields via _ISO_LANG_CODES allowlist
#   _resolve_active_preset_version - resolve latest preset version string;
#                                  lazy-imports preset_edit to avoid cycle
#   server                       - FastMCP server instance (shared with
#                                  mcp/memo.py's create_memo)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.3.0 — Phase-17 W17-3 (MP-AUDIT-EXTEND). Added
#     `preset_version` stamp (ALWAYS) and `lang` stamp (when ≥2 distinct
#     language-suffix fields) to _audit_instructions output. Closes
#     V-MP-THEME-EDIT scenario-10 + V-MP-DOC-BUNDLE scenario-7b.
#     New helpers: _detect_template_languages (_ISO_LANG_CODES allowlist),
#     _resolve_active_preset_version (lazy-imports resolve_latest_preset_path
#     from preset_edit.py). BLOCK_AUDIT_PRESET_VERSION + BLOCK_AUDIT_LANG
#     log markers. _audit_instructions gains keyword-only preset_name +
#     required_fields params; backwards-compat for existing callers.
#     review-fix: 7 mechanical simplifications from code-simplifier agent.
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mint_python.core.document import Document

import yaml
from fastmcp import Context, FastMCP
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.tools.tool import ToolResult
from mcp.shared.exceptions import McpError
from mcp.types import ResourceLink, TextContent
from pydantic import AnyUrl

from mint_python.adapters.markdown import markdown_to_spec
from mint_python.core.content import Paragraph
from mint_python.core.section import Section
from mint_python.core.table import Table
from mint_python.grace import bootstrap as grace_bootstrap
from mint_python.qa.visual import score_document as _score_document

logger = logging.getLogger(__name__)

_ISO_LANG_CODES: frozenset[str] = frozenset(
    {"en", "ru", "kk", "ky", "uz", "de", "fr", "es", "zh", "ja", "tr", "ar"}
)

_LANG_SUFFIX_RE = re.compile(
    r"^(?P<base>[a-z][a-z0-9_]*?)_(?P<lang>[a-z]{2,3})$"
)


MEMO_REQUIRED_FIELDS: tuple[str, ...] = (
    "sender",
    "recipient",
    "date",
    "subject",
    "body",
)


# --------------------------------------------------------------------------- #
# Privacy mitigations (MP-DOC-PERSONAL-GUARD — Phase-17 W17-1)
# --------------------------------------------------------------------------- #
#
# These constants + helpers implement MITIGATION (not enforcement) of privacy
# leaks through MCP create_document. The calling client model can still fill
# elicit-fallback prompts from its own session context — MINT cannot stop
# that — but we (a) detect anonymity hints in the user's intent, (b) clear
# heuristically-extracted personal data, (c) hint personal_data=high to the
# client, (d) report what got anonymised in structured_content, and (e) skip
# elicit entirely for personal+optional fields under anonymity.
#
# CONTRACT NOTE: MITIGATION ONLY — see V-MP-DOC-PERSONAL-GUARD forbidden-4
# + forbidden-9. Do not use stronger language ("force", "guard", "block")
# in user-facing strings; the contract is hint + clear-extracted-fields,
# not a guarantee. Story is "MINT respects anonymity hints".


_PERSONAL_FIELDS: frozenset[str] = frozenset({
    "sender",
    "author",
    "contact",
    "from_",
    "to_",
    "signature",
    "signer",
    "recipient",
    "cc",
    "bcc",
    "phone",
    "email",
    "address",
    "signer_name",
    "signer_email",
})


# Anchored anonymous-flag regex. Four match forms, each captured in its own
# named group so callers can report which form fired:
#   - bracket   : `[anonymous]` (any case) — the canonical English flag form
#   - word_en   : (unused at present; English bare "anonymous" is ambiguous
#                 with prose describing anonymous data — only bracket form
#                 fires for English. Group kept named for future explicit
#                 word forms like "anonymous-mode" if a future scenario
#                 requires.)
#   - word_ru   : standalone "анонимно" word (Cyrillic — no ambiguous prose
#                 usage in scope; safe to fire on standalone matches)
#   - phrase_ru : "без личных данных" phrase
# The boundaries use a non-word lookahead/lookbehind so "анонимной",
# "анонимность" don't fire on word_ru, and "non-anonymous" / "anonymously"
# don't fire on the bracket form's neighbours. Quoted forms like
# '"[anonymous]"' STILL trigger — that's the safe default per scenario-9
# (false-positive cost is just an extra hint; false-negative cost leaks PII).
_ANONYMOUS_FLAG_RE = re.compile(
    r"(?P<bracket>\[anonymous\])"
    r"|(?<!\w)(?P<word_ru>анонимно)(?!\w)"
    r"|(?<!\w)(?P<phrase_ru>без\s+личных\s+данных)(?!\w)",
    re.IGNORECASE,
)


def _detect_anonymous_flag(intent: str) -> tuple[bool, str | None]:
    """Detect anonymity hint in user intent. Pure regex; no I/O.

    Returns:
        (matched, form) — form is one of "bracket", "word_en", "word_ru",
        "phrase_ru" when matched=True; None when matched=False.

    Anchored so accidental substrings don't trip the flag — "non-anonymous"
    / "anonymously" do NOT trigger. "[anonymous]" inside quotes DOES still
    trigger (scenario-9 safe-default: false-positive cost is one extra
    hint + blocklist run; false-negative cost is real PII leak).
    """
    match = _ANONYMOUS_FLAG_RE.search(intent)
    if match is None:
        return False, None
    return True, match.lastgroup


def _apply_personal_blocklist(
    spec: DocumentSpec,
    blocklist: frozenset[str],
    *,
    log_prefix: str = "MP-Doc",
) -> tuple[DocumentSpec, list[str]]:
    """Clear any heuristically-set values whose field name is in the blocklist.

    Emits one BLOCK_ANONYMISE log marker per cleared field; the marker
    carries only field name, reason token, value length, and an 8-hex
    sha256 prefix — NEVER the raw cleared value (forbidden-6 +
    scenario-18 security-critical).

    Idempotent: running twice over an already-cleared spec yields an empty
    cleared-list and no additional log emissions (scenario-15).

    Args:
        spec: The DocumentSpec from _heuristic_extract.
        blocklist: Set of field names to clear (typically _PERSONAL_FIELDS).
        log_prefix: Routed through for create_memo log-marker parity.

    Returns:
        (spec, fields_cleared) — spec is the same instance, mutated;
        fields_cleared is sorted for stable test ordering.
    """
    cleared: list[str] = []
    for field_name in sorted(blocklist):
        value = getattr(spec, field_name, None)
        if not value:
            continue
        # Capture value BEFORE clearing so we can hash it for the audit
        # log without ever re-reading it post-clear.
        value_str = str(value)
        sha8 = hashlib.sha256(value_str.encode("utf-8")).hexdigest()[:8]
        value_len = len(value_str)
        setattr(spec, field_name, None)
        cleared.append(field_name)
        logger.info(
            "[%s][heuristic][BLOCK_ANONYMISE] "
            "field=%s reason=anonymous_intent_flag value_len=%d value_sha8=%s",
            log_prefix,
            field_name,
            value_len,
            sha8,
        )
    return spec, cleared


def _render_anonymisation_report(
    structured_content: dict[str, Any],
    anonymised: bool,
    fields_omitted: list[str],
) -> dict[str, Any]:
    """Merge anonymised flag + fields_omitted into the existing structured
    content dict. Preserves every other key (status, path, doc_type, etc.).

    fields_omitted is sorted (caller passes a sorted list from
    _apply_personal_blocklist); we keep it that way for stable test
    assertions and human readability.
    """
    structured_content["anonymised"] = anonymised
    structured_content["fields_omitted"] = list(fields_omitted)
    return structured_content


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class DocumentError(Exception):
    """Base for document-generation errors."""


class DocumentElicitationRejected(DocumentError):  # noqa: N818 — error code DOC_ELICITATION_REJECTED mirrors class name; suffix omitted intentionally
    """User declined / cancelled an elicitation request for a required field.

    Carries the field name so callers can surface a useful message; no docx
    is produced when this fires.
    """

    def __init__(self, field_name: str, reason: str = "declined") -> None:
        self.field_name = field_name
        self.reason = reason
        super().__init__(
            f"DOC_ELICITATION_REJECTED: user {reason} elicitation for "
            f"required field {field_name!r}"
        )


class DocumentTemplateNotFound(DocumentError):  # noqa: N818 — error code DOC_TEMPLATE_NOT_FOUND mirrors class name; suffix omitted intentionally
    """templates/<doc_type>.yaml missing on disk."""


class DocumentTypeNotFound(DocumentError):  # noqa: N818 — error code DOC_TYPE_NOT_FOUND mirrors class name; suffix omitted intentionally
    """doc_type does not resolve to a known template in templates/.

    Distinct from DocumentTemplateNotFound: this fires at the registry
    boundary (no YAML file with that name exists), so the message lists
    the doc_types that ARE available — gives the connected model a
    chance to retry with a valid name.

    Phase-17 Wave-17-2 (MP-DOC-PICKER): the message now ALSO surfaces
    the top-3 keyword-scored suggestions when caller passes them via
    `suggestions=`. The picker tool (mcp.picker.suggest_templates)
    computes them; the raise site lazy-imports the helper so this
    module's import graph stays unchanged (picker imports `server`
    from us, so a top-level import would cycle).
    """

    def __init__(
        self,
        message: str,
        *,
        suggestions: list[dict[str, Any]] | None = None,
    ) -> None:
        # Keep the `DOC_TYPE_NOT_FOUND:` prefix intact so callers'
        # substring asserts on the error code don't regress (legacy
        # callers in older tests parse the prefix off the message).
        # Append the top-3 picker output AFTER the available-types
        # list so the existing "Available: ..." parsing path is
        # untouched.
        if suggestions:
            picks = ", ".join(
                f"{entry['name']} ({entry['match_score']:.2f})"
                for entry in suggestions[:3]
            )
            message = f"{message} Top suggestions: {picks}"
        super().__init__(message)
        # Stash the structured suggestions on the instance so callers
        # who want machine-readable form (e.g. an MCP wrapper that
        # surfaces them as structured_content) can read them without
        # re-parsing the prose message.
        self.suggestions: list[dict[str, Any]] = list(suggestions or [])


class DocumentGenerationFailed(DocumentError):  # noqa: N818 — error code DOC_GENERATION_FAILED mirrors class name; suffix omitted intentionally
    """Builder failed at render time after all required fields were collected."""


# --------------------------------------------------------------------------- #
# DocumentSpec dataclass + heuristic field extractor
# --------------------------------------------------------------------------- #


@dataclass
class DocumentSpec:
    """Generic content fields covering every known doc_type's union.

    Phase-13 memo had 5 fields. Phase-14/16 expanded the catalog (letter,
    report, ADR, contract, NDA, technical-spec) — each new doc_type adds
    its required_fields to this union shape. The template's required_fields
    list determines which subset is actively elicited for a given call;
    fields not in required_fields simply stay None.

    Phase-17 W17-0 added engineering-doc fields (purpose, sections,
    requirement_*, priority_*, scope_warning, notes) so MP-DOC-TECH-SPEC
    can elicit them through the existing pipeline without dynamic-setattr
    fragility (which would break if the dataclass ever becomes frozen).
    """

    # Phase-13 memo + Phase-14 letter fields
    sender: str | None = None
    recipient: str | None = None
    date: str | None = None
    subject: str | None = None
    body: str | None = None

    # Phase-16 report + contract fields
    author: str | None = None
    summary: str | None = None
    conclusions: str | None = None

    # Phase-17 technical-spec fields (W17-0 pre-flight extension)
    title: str | None = None
    purpose: str | None = None
    sections: str | None = None
    requirement_1: str | None = None
    requirement_2: str | None = None
    priority_1: str | None = None
    priority_2: str | None = None
    scope_warning: str | None = None
    notes: str | None = None

    def filled(self, required_fields: tuple[str, ...]) -> list[str]:
        return [name for name in required_fields if getattr(self, name, None)]


# Date pattern: e.g. "2026-05-15", "May 15, 2026", "15 May 2026"
_DATE_RE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}"  # ISO
    r"|\d{1,2}\s+\w{3,}\s+\d{4}"  # 15 May 2026
    r"|\w{3,}\s+\d{1,2},?\s+\d{4}"  # May 15, 2026
    r")\b"
)
# Joint pattern: "from X to Y" with both halves.
_FROM_TO_RE = re.compile(
    r"\bfrom\s+([^,;]+?)\s+to\s+([^,;:.]+?)"
    r"(?:[,.:;]|\s+(?:on|about|re|regarding)\b|$)",
    re.IGNORECASE,
)
# Fallback for sender-only: "from X" terminated by punctuation, " on ", or
# " about " / " regarding " / " re: " before any "to" keyword.
_FROM_RE = re.compile(
    r"\bfrom\s+([^,;]+?)"
    r"(?:[,.:;]|\s+(?:on|about|re|regarding)\b|$)",
    re.IGNORECASE,
)
# Fallback for recipient-only: "to X" or "Recipient: X". The capture stops
# at common subject/body lead-ins so "Memo to Board about strategy" doesn't
# yield "Board about strategy" as recipient.
_RECIPIENT_RE = re.compile(
    r"\b(?:Recipient:|to)\s+([^,.;\n]+?)"
    r"(?:[,.:;\n]|\s+(?:about|regarding|re:|on)\b|$)",
    re.IGNORECASE,
)
# Subject prose patterns: "about X" / "regarding X" / "re: X". Labelled
# `subject: X` form is handled by _LABEL_RE; intentionally NOT included here
# to avoid double-matching against an empty `subject:\n` line followed by
# unrelated prose.
_SUBJECT_RE = re.compile(
    r"\b(?:about|regarding|re:)\s+([^,.;\n]+)",
    re.IGNORECASE,
)
# Labelled-field pattern: "<label>:" anywhere on a line, value to end-of-line
# OR end-of-string. LLMs frequently emit memos as labelled key-value blobs
# when asked to be explicit; we match the conventional 5 labels plus
# technical-spec labels (title, purpose, sections, etc.).
_LABEL_RE = re.compile(
    r"^[ \t]*(sender|from|recipient|to|date|subject|body"
    r"|author|summary|conclusions"
    r"|title|purpose|sections|notes|scope_warning)"
    r"[ \t]*:[ \t]*(.+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
# key=value format — common fallback when LLMs emit compact single-line blobs.
_KV_RE = re.compile(
    r"(?:^|[\s,])"
    r"(sender|from|recipient|to|date|subject|body"
    r"|author|summary|conclusions"
    r"|title|purpose|sections|notes|scope_warning)"
    r"[ \t]*=[ \t]*([^\s,]+(?:\s+(?!\w+=)[^\s,]+)*)",
    re.IGNORECASE,
)
# Body specifically — multi-line: "Body:\n\n<everything until EOF or another
# top-level label>". When present this overrides the labelled-line form.
_BODY_BLOCK_RE = re.compile(
    r"^\s*body\s*:\s*\n+(.+?)\Z",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
# Pre-normalize transition between inline labelled keys: "Sender: X.
# Recipient: Y. Date: Z." appears as one line; this splits each
# `<label>:` after a period+whitespace onto its own line so the
# line-mode _LABEL_RE matches them all. Conservative — only fires when
# the next token genuinely is one of our known labels followed by a
# colon, so prose containing periods doesn't get clobbered.
_INLINE_LABEL_SPLIT_RE = re.compile(
    r"\.\s+(?=(?:sender|from|recipient|to|date|subject|body"
    r"|author|summary|conclusions"
    r"|title|purpose|sections|notes|scope_warning)\s*:)",
    re.IGNORECASE,
)


@dataclass
class _AnonymisationOutcome:
    """Tracked alongside the heuristic spec — what got cleared by the
    privacy mitigation pass, so the pipeline can both report it in
    structured_content and feed it to the elicit-skip logic.
    """

    anonymous_flag: bool = False
    match_form: str | None = None
    fields_omitted: list[str] = field(default_factory=list)


def _heuristic_extract(
    intent: str,
    source_md: str | None,
) -> DocumentSpec:
    """Best-effort field extractor over free-text intent + optional source.

    Conservative — when uncertain, leaves the field as None so elicitation
    kicks in. False positives here silently produce wrong content; false
    negatives just mean an extra elicit call.

    The extractor populates every field it can recognize on the spec; the
    pipeline downstream consults the active template's required_fields to
    decide which ones must be present and which are ignored. So this stays
    doc-type agnostic — patterns matched here that aren't in a given
    template's required_fields list have no effect on output.

    Strategy is layered:
      1. Try the labelled-key form ("sender: X\\nrecipient: Y\\n...") which
         LLMs emit naturally when asked to be explicit.
      2. Fall back to prose patterns ("Memo from X to Y about Z").
      3. Body extraction tries (in order): labelled "Body:\\n\\n…" block,
         single-line "body: …", source_md content.
    """
    spec = DocumentSpec()

    # ---- Pre-normalize: inline labelled-key form -----------------------
    # LLMs sometimes emit labelled keys on a single line separated by
    # ". " — e.g. "Sender: X. Recipient: Y. Date: Z.". The line-mode
    # _LABEL_RE only sees the first label that way (its lazy value
    # match consumes the rest of the line). Detect these inline
    # transitions and inject newlines so each label lands on its own
    # line for the regex below.
    intent = _INLINE_LABEL_SPLIT_RE.sub(".\n", intent)

    # ---- Layer 1: labelled-line form -----------------------------------
    label_to_field = {
        "sender": "sender", "from": "sender",
        "recipient": "recipient", "to": "recipient",
        "date": "date", "subject": "subject", "body": "body",
        "author": "author", "summary": "summary", "conclusions": "conclusions",
        "title": "title", "purpose": "purpose", "sections": "sections",
        "notes": "notes", "scope_warning": "scope_warning",
    }
    # Multi-line body block first — captures the "Body:\n\n..." shape.
    m = _BODY_BLOCK_RE.search(intent)
    if m:
        body_text = m.group(1).strip()
        if body_text:
            spec.body = body_text

    # Single-line labelled fields. The `not getattr(spec, target)` guard
    # is the precedence rule: once a field is set (by the body block
    # earlier or by an earlier matching label), subsequent matches for
    # the same target are ignored.
    for match in _LABEL_RE.finditer(intent):
        label, value = match.group(1).lower(), match.group(2).strip()
        target = label_to_field.get(label)
        if target and value and not getattr(spec, target):
            # Strip a trailing sentence-period if present — values like
            # "M. Yevdokimov (CPO)." come from inline-label splitting
            # and the trailing period is a sentence terminator, not
            # part of the name.
            setattr(spec, target, value.rstrip("."))

    # ---- Layer 2: prose patterns ---------------------------------------
    if not spec.date:
        m = _DATE_RE.search(intent)
        if m:
            spec.date = m.group(1).strip()

    # From / To prose: try joint, fall back to halves.
    if not spec.sender or not spec.recipient:
        m = _FROM_TO_RE.search(intent)
        if m:
            if not spec.sender:
                spec.sender = m.group(1).strip()
            if not spec.recipient:
                spec.recipient = m.group(2).strip()
    if not spec.sender:
        m = _FROM_RE.search(intent)
        if m:
            spec.sender = m.group(1).strip()
    if not spec.recipient:
        m = _RECIPIENT_RE.search(intent)
        if m:
            spec.recipient = m.group(1).strip()

    if not spec.subject:
        m = _SUBJECT_RE.search(intent)
        if m:
            spec.subject = m.group(1).strip().rstrip(".")

    # ---- Layer 2.5: key=value extraction --------------------------------
    for match in _KV_RE.finditer(intent):
        label, value = match.group(1).lower(), match.group(2).strip()
        target = label_to_field.get(label)
        if target and value and not getattr(spec, target):
            setattr(spec, target, value.rstrip("."))

    # ---- Layer 3: body from source_md ----------------------------------
    if not spec.body and source_md and source_md.strip():
        try:
            article_spec = markdown_to_spec(source_md)
            chunks: list[str] = []
            for section in article_spec.sections:
                for block in section.blocks:
                    text = getattr(block, "text", None)
                    if text:
                        chunks.append(text)
            if chunks:
                spec.body = "\n\n".join(chunks)
        except Exception:  # pragma: no cover — adapter errors fall through to elicit
            logger.debug(
                "[MP-Doc][heuristic] markdown_to_spec failed, "
                "falling back to None",
                exc_info=True,
            )
            spec.body = None

    return spec


def _extract_with_anonymisation(
    intent: str,
    source_md: str | None,
    *,
    log_prefix: str = "MP-Doc",
) -> tuple[DocumentSpec, _AnonymisationOutcome]:
    """Heuristic extract + privacy-mitigation pass.

    Wraps `_heuristic_extract` and applies the MP-DOC-PERSONAL-GUARD
    blocklist when the anonymity flag fires. Kept as a separate entry
    so the public `_heuristic_extract` contract (DocumentSpec return) is
    preserved for existing MP-Memo tests that call it directly.

    Forbidden-5 boundary: spec.body / source_md-derived content is
    NEVER scrubbed — only fields the heuristic populated on the spec.
    One BLOCK_ANONYMOUS_DETECTED marker per call when the flag matches;
    one BLOCK_ANONYMISE per cleared field (in `_apply_personal_blocklist`).
    """
    spec = _heuristic_extract(intent, source_md)
    outcome = _AnonymisationOutcome()
    matched, form = _detect_anonymous_flag(intent)
    if matched:
        outcome.anonymous_flag = True
        outcome.match_form = form
        logger.info(
            "[%s][heuristic][BLOCK_ANONYMOUS_DETECTED] "
            "match_form=%s intent_len=%d",
            log_prefix,
            form,
            len(intent),
        )
        _, cleared = _apply_personal_blocklist(
            spec, _PERSONAL_FIELDS, log_prefix=log_prefix
        )
        outcome.fields_omitted = cleared
    return spec, outcome


# --------------------------------------------------------------------------- #
# Template loader
# --------------------------------------------------------------------------- #


_TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "templates"
)


_VERSIONED_TEMPLATE_RE = re.compile(
    r"^(?P<name>[a-zA-Z0-9_-]+?)_v(?P<version>\d+\.\d+)\.yaml$"
)


def _template_paths(doc_type: str) -> list[Path]:
    """All YAML files that contribute to `doc_type` — the canonical
    `<doc_type>.yaml` baseline plus any `<doc_type>_v<semver>.yaml`
    siblings authored via update_template (Phase-14 W3). Sorted by
    ascending semver so callers can pick the latest with paths[-1] (or
    pick a specific version by inspecting each file's `version` field).
    """
    if not _TEMPLATES_DIR.exists():  # pragma: no cover — repo layout invariant
        return []
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for path in _TEMPLATES_DIR.glob("*.yaml"):
        # _audit.jsonl is jsonl not yaml so this is defensive only.
        if path.name.startswith("_"):  # pragma: no cover
            continue
        if path.stem == doc_type:
            # Canonical baseline; version comes from inside YAML — read
            # it once cheaply to compare against sibling versions.
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:  # pragma: no cover — registry validates load earlier
                continue
            v = str(raw.get("version", "0.0"))
            candidates.append((_semver_tuple(v), path))
            continue
        m = _VERSIONED_TEMPLATE_RE.match(path.name)
        if m and m.group("name") == doc_type:
            candidates.append((_semver_tuple(m.group("version")), path))
    candidates.sort(key=lambda item: item[0])
    return [p for _v, p in candidates]


def _semver_tuple(version: str) -> tuple[int, ...]:
    """Parse "1.10" → (1, 10) for ordered comparison. Values that fail
    to parse fall back to (0, 0) so a malformed version sorts to the
    bottom rather than crashing template resolution — registry catches
    real schema problems via TemplateInvalidSchema at its own load
    boundary."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:  # pragma: no cover — registry validates version syntax upstream
        return (0, 0)


def _available_doc_types() -> list[str]:
    """List doc_types discoverable in templates/ — derived from the union
    of canonical `<name>.yaml` stems and `<name>_v<semver>.yaml` name
    prefixes. Used to compose DOC_TYPE_NOT_FOUND messages so the
    connected model can suggest a valid name."""
    if not _TEMPLATES_DIR.exists():  # pragma: no cover — repo layout invariant
        return []
    types: set[str] = set()
    for path in _TEMPLATES_DIR.glob("*.yaml"):
        if path.name.startswith("_"):  # pragma: no cover — defensive
            continue
        m = _VERSIONED_TEMPLATE_RE.match(path.name)
        if m:
            types.add(m.group("name"))
        else:
            types.add(path.stem)
    return sorted(types)


@dataclass
class DocumentTemplate:
    """Loaded templates/<doc_type>.yaml — the data-driven document layout.

    The `author` field carries the identity recorded by update_template
    (Phase-14 W3) when this version was authored. Empty string for the
    canonical baselines that pre-date the audit trail."""

    name: str
    version: str
    required_fields: tuple[str, ...]
    layout: list[dict[str, Any]] = field(default_factory=list)
    author: str = ""


def _load_template(doc_type: str) -> DocumentTemplate:
    """Look up the LATEST version of `doc_type` (canonical baseline or
    highest-semver sibling, whichever is newer) and parse it.

    Raises:
        DocumentTypeNotFound: when no template file exists for this doc_type
            (lists the available doc_types so the caller can retry).
        DocumentTemplateNotFound: reserved for the inverse — file existed at
            registry-walk time but disappeared by load-time. Kept distinct
            from DOC_TYPE_NOT_FOUND so future log triage can tell them apart.
    """
    paths = _template_paths(doc_type)
    if not paths:
        available = _available_doc_types()
        # Phase-17 W17-2 (MP-DOC-PICKER): lazy-import the picker so the
        # error message surfaces top-3 scored suggestions inline. The
        # import is deferred to keep document.py free of an upward
        # picker reference (picker imports `server` from us — top-level
        # picker import here would cycle at module load).
        suggestions: list[dict[str, Any]] = []
        try:
            from mint_python.mcp.picker import suggest_templates

            suggestions = list(
                suggest_templates(doc_type).get("suggestions", [])
            )
        except Exception:  # pragma: no cover — defensive; picker is non-failing by contract
            logger.debug("[MP-Doc][load] picker suggestion failed", exc_info=True)
            suggestions = []
        raise DocumentTypeNotFound(
            f"DOC_TYPE_NOT_FOUND: no template for doc_type={doc_type!r}. "
            f"Available: {', '.join(available) if available else '(none)'}",
            suggestions=suggestions,
        )
    latest_path = paths[-1]
    raw = yaml.safe_load(latest_path.read_text(encoding="utf-8"))
    return DocumentTemplate(
        name=str(raw.get("name", doc_type)),
        version=str(raw.get("version", "1.0")),
        required_fields=tuple(raw.get("required_fields", ())),
        layout=list(raw.get("layout", [])),
        author=str(raw.get("_authored_by", "")),
    )


# --------------------------------------------------------------------------- #
# Builder — assemble Document from MemoSpec via templates/memo.yaml
# --------------------------------------------------------------------------- #


def _substitute(text: str, spec: DocumentSpec) -> str:
    """Replace `{{ field }}` placeholders with the spec's field values.

    Supports an optional Jinja-style `default:` filter:
      `{{ name | default: "fallback" }}` — uses the field value when set,
      else the literal between the quotes. Both `"..."` and `'...'`
      delimiters accepted. Closes #2.
    """

    def _resolve(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        # group(2) is the double-quoted default; group(3) the single-
        # quoted variant. At most one is non-None for any single match.
        default_dq = match.group(2)
        default_sq = match.group(3)
        default = default_dq if default_dq is not None else default_sq
        value = getattr(spec, name, None)
        if value:
            return str(value)
        return default if default is not None else ""

    return _SUBSTITUTE_RE.sub(_resolve, text)


# {{ name }}  OR  {{ name | default: "..." }}  OR  {{ name | default: '...' }}
# Whitespace around the pipe and the colon is generous (Jinja-2 conventions);
# the inner string is non-greedy so adjacent placeholders don't merge.
_SUBSTITUTE_RE = re.compile(
    r'\{\{\s*(\w+)'
    r'(?:\s*\|\s*default\s*:\s*(?:"([^"]*)"|\'([^\']*)\'))?'
    r'\s*\}\}'
)


_PLACEHOLDER_ONLY_RE = re.compile(
    r"^\s*\{\{\s*(\w+)\s*\}\}\s*$"
)


def _entry_resolves_empty(entry: dict[str, Any], spec: DocumentSpec) -> bool:
    """True iff the entry is a single-placeholder block with no default
    fallback whose field resolves to empty in `spec`. Used by the walker
    conditional-skip pass (MP-DOC-PERSONAL-GUARD scenario-16).

    Only considers `kind: paragraph` and `kind: heading` entries with a
    text that is exactly `{{ field }}` (no surrounding prose) — a partial
    placeholder like `By {{ author }}` is NOT eligible because dropping
    it would leave a meaningless "By " stub; the substitute handles that
    via empty-string substitution.
    """
    if entry.get("kind") not in ("paragraph", "heading"):
        return False
    text = str(entry.get("text", ""))
    m = _PLACEHOLDER_ONLY_RE.match(text)
    if m is None:
        return False
    # If the YAML entry has an explicit `default:` key, the author told
    # us they want a fallback string instead of a skip. Honor that.
    # No shipped template uses YAML-level `default:` at present — the
    # branch is defensive against future templates that opt out of
    # the placeholder-skip behavior; coverage will land when such a
    # template ships.
    if entry.get("default"):  # pragma: no cover — defensive; see comment
        return False
    # The `_substitute` function ALSO supports inline Jinja-style
    # default filters like `{{ name | default: "..." }}` — but those
    # take the form "{{ name | default: '...' }}" with extra content
    # AFTER the field name, so they wouldn't match _PLACEHOLDER_ONLY_RE
    # in the first place. No additional guard needed.
    field_name = m.group(1)
    value = getattr(spec, field_name, None)
    return not value


def _prepare_layout(
    layout: list[dict[str, Any]],
    spec: DocumentSpec,
) -> list[dict[str, Any]]:
    """Walker conditional-skip pre-pass (MP-DOC-PERSONAL-GUARD scenario-16).

    Two rules:
      1. Drop blocks whose single `{{ field }}` placeholder resolves to
         empty AND have no `default:` fallback (placeholder-only-empty).
      2. Drop a "decorative" heading (literal text, no placeholder)
         when its IMMEDIATELY-FOLLOWING content block in the original
         layout was a placeholder-only-empty paragraph. Closes the
         "Signed" + empty `{{ sender }}` pattern in letter.yaml: the
         heading is semantically tied to the field beneath it, so
         losing the field means losing the heading.

    Spacers between a heading and its first content block are
    transparent — they neither count as content nor block the
    heading-decoration check. Headings whose text contains a
    placeholder (e.g. letter's H1 `{{ recipient }}`) are NEVER
    decorative — their text IS the primary content.

    Side-effect-free: returns a new list; original layout unchanged.
    """
    # Phase A — mark each entry: keep | drop_empty_placeholder.
    marks: list[str] = []
    for entry in layout:
        if _entry_resolves_empty(entry, spec):
            marks.append("drop")
        else:
            marks.append("keep")

    # Phase B — mark decorative headings whose immediate next non-spacer
    # block was dropped in Phase A.
    for i, entry in enumerate(layout):
        if marks[i] != "keep":
            continue
        if entry.get("kind") != "heading":
            continue
        heading_text = str(entry.get("text", ""))
        if "{{" in heading_text:
            # Headings with placeholders are content, not decoration.
            continue
        # Find the immediately-following content (skip spacers).
        for j in range(i + 1, len(layout)):
            next_entry = layout[j]
            if next_entry.get("kind") == "spacer":
                continue
            if marks[j] == "drop":
                marks[i] = "drop"
            break

    return [entry for entry, mark in zip(layout, marks, strict=False) if mark == "keep"]


def _build_document(spec: DocumentSpec, template: DocumentTemplate) -> Document:
    """Walk template.layout and assemble a Document with klawd preset.

    Returns the configured Document instance — caller saves and (optionally)
    injects GRACE manifest after.

    Walker conditional-skip (MP-DOC-PERSONAL-GUARD scenario-16): a pre-pass
    prunes layout entries whose single `{{ field }}` resolves to empty AND
    have no default, INCLUDING parent decorative headings ("Signed" pattern)
    whose body emptied out. See `_prepare_layout`.
    """
    # Local import to keep the module top-level light and avoid a circular
    # import surface during type-checking. Document is the SDK facade.
    from mint_python.core.document import Document

    doc = Document(
        format="docx",
        title=spec.subject or template.name.title() or "Document",
    ).with_style_preset("klawd")

    layout = _prepare_layout(template.layout, spec)

    section: Section | None = None
    for entry in layout:
        kind = entry.get("kind")
        if kind == "heading":
            level = int(entry.get("level", 1))
            text = _substitute(str(entry.get("text", "")), spec)
            section = Section(title=text, level=level)
            doc.add_section(section)
            continue
        if section is None:  # pragma: no cover — canonical templates start with a heading
            section = Section(title=template.name.title() or "Document", level=1)
            doc.add_section(section)
        if kind == "paragraph":
            template_text = str(entry.get("text", "")).strip()
            if template_text in ("{{ body }}", "{{body}}"):
                _render_body(section, spec.body or "")
            else:
                # Defensive: canonical memo template only has `{{ body }}`
                # paragraph; this branch handles future template variants
                # that emit raw paragraph text without placeholders.
                text = _substitute(template_text, spec)  # pragma: no cover
                section.add_paragraph(Paragraph(text))  # pragma: no cover
        elif kind == "table":
            header_cells = [str(h) for h in entry.get("header", [])]
            raw_rows = entry.get("rows", [])
            rows = [
                [str(_substitute(c, spec)) for c in row] for row in raw_rows
            ]
            section.add_table(
                Table.from_list([header_cells, *rows], header=True)
            )
        elif kind == "spacer":
            section.add_paragraph(Paragraph(""))
        elif kind == "callout":
            # Field name leniency — the comment-documented vocab uses
            # `kind_of` / `body` / `title`; Claude's natural output
            # (smoke 2026-05-10) used `type` / `text` / `label`. Accept
            # both shapes so template authors aren't punished for the
            # historical naming inconsistency. Closes #1.
            from mint_python.core.callout import Callout, CalloutKind

            kind_str = str(
                entry.get("kind_of") or entry.get("type") or "info"
            ).lower()
            callout_kind = {
                "info": CalloutKind.INFO,
                "warning": CalloutKind.WARNING,
                "code": CalloutKind.CODE,
            }.get(kind_str, CalloutKind.INFO)
            body_text = _substitute(
                str(entry.get("body") or entry.get("text") or ""), spec
            )
            title_text = entry.get("title") or entry.get("label")
            title_str = (
                _substitute(str(title_text), spec) if title_text else None
            )
            section.add_callout(
                Callout(body_text, kind=callout_kind, title=title_str)
            )
        # Unknown layout kinds are silently skipped — defensive against
        # template-yaml drift; tests cover the supported set.
    return doc



# Markdown signal characters — if any appear in body text, we route through
# MP-MD-ADAPTER for proper block extraction. Plain text without any markup
# falls through to a single Paragraph for the empty-overhead case.
_MD_SIGNAL_CHARS = ("**", "__", "_", "*", "#", "`", "> ", "\n- ", "\n* ", "\n1. ")


def _normalize_body_markdown(body_text: str) -> str:
    """Insert blank lines around bold-only lines so they parse as section
    separators, not soft-breaks merged into the next paragraph.

    Chat-driven fallback flow lets Claude write things like:
        **Heading**
        Paragraph text...
    CommonMark treats the single newline as a soft break (rendered as a
    space); the intent of the LLM was a section separator. We inject the
    blank line so markdown-it-py separates the bold-only line as its own
    paragraph and the next text becomes a standalone paragraph below.
    """
    lines = body_text.split("\n")
    out: list[str] = []
    bold_only = re.compile(r"^\s*\*\*[^*]+\*\*\s*$")
    for i, line in enumerate(lines):
        out.append(line)
        if not bold_only.match(line):
            continue
        # Inject blank line between a bold-only line and the next non-blank
        # line (when not already separated by a blank).
        next_idx = i + 1
        if next_idx < len(lines) and lines[next_idx].strip():
            out.append("")
    return "\n".join(out)


def _render_body(section: Section, body_text: str) -> None:
    """Emit body content into a Section, parsing markdown when present.

    Chat-driven fallback flow naturally produces markdown: Claude formats
    the body as `**Heading**\\n\\nbody text...` blobs. Without this helper,
    the entire body lands in a single Paragraph with literal asterisks.
    With it, MP-MD-ADAPTER extracts the structure (paragraphs with bold
    runs, lists, tables, callouts) and we re-emit through the SDK.
    """
    if not body_text:  # pragma: no cover — caller passes spec.body which is non-empty by contract
        return
    has_markdown = any(sig in body_text for sig in _MD_SIGNAL_CHARS)
    if not has_markdown:
        # Plain text — split on blank lines for paragraph breaks.
        for chunk in body_text.split("\n\n"):
            chunk_stripped = chunk.strip()
            if chunk_stripped:
                section.add_paragraph(Paragraph(chunk_stripped))
        return

    normalized = _normalize_body_markdown(body_text)
    try:
        body_spec = markdown_to_spec(normalized)
    except Exception:  # pragma: no cover — adapter rarely fails on body content; fallback to raw
        logger.debug("[MP-Doc][render] body adapter failed", exc_info=True)
        section.add_paragraph(Paragraph(body_text))
        return

    # Walk the adapter's block output. Body-level headings get flattened
    # to bold paragraphs (we already have a Body H2 above; nesting more
    # headings would visually compete). Sub-blocks render via the SDK.
    for body_section in body_spec.sections:
        title = body_section.title.strip()
        if title and title not in ("Untitled", "Introduction"):
            section.add_paragraph(Paragraph().add_run(title, bold=True))
        for block in body_section.blocks:
            _emit_body_block(section, block)


def _emit_body_block(section: Section, block: Any) -> None:
    """Map a body-markdown Block onto the appropriate SDK section call."""
    from tools.article_experiment.spec import (
        CalloutBlock,
        CodeBlock,
        ListBlock,
        ParagraphBlock,
        TableBlock,
    )

    from mint_python.core.callout import Callout, CalloutKind
    from mint_python.core.list_block import List, ListKind

    if isinstance(block, ParagraphBlock):
        if not block.emphasis:
            section.add_paragraph(Paragraph(block.text))
            return
        # Build a Paragraph with bold runs around emphasis substrings.
        para = Paragraph()
        text = block.text
        cursor = 0
        for phrase in block.emphasis:
            idx = text.find(phrase, cursor)
            # Defensive: markdown-it-py's emphasis substrings are present in
            # block.text by construction; -1 only happens with hand-crafted
            # ParagraphBlock instances or after upstream normalization edits.
            if idx == -1:  # pragma: no cover
                continue
            if idx > cursor:
                para.add_run(text[cursor:idx])
            para.add_run(phrase, bold=True)
            cursor = idx + len(phrase)
        if cursor < len(text):
            para.add_run(text[cursor:])
        section.add_paragraph(para)
        return
    if isinstance(block, ListBlock):
        list_kind = {
            "bullet": ListKind.BULLET,
            "numbered": ListKind.NUMBERED,
            "checklist": ListKind.CHECKLIST,
        }[block.kind]
        section.add_list(List(items=list(block.items), kind=list_kind))
        return
    if isinstance(block, TableBlock):
        rows: list[list[str]] = []
        if block.header:
            rows.append(list(block.header))
        rows.extend(list(r) for r in block.rows)
        if rows:
            width = len(rows[0])
            normalized = [(row + [""] * (width - len(row)))[:width] for row in rows]
            section.add_table(
                Table.from_list(normalized, header=bool(block.header))
            )
        return
    if isinstance(block, CalloutBlock):
        callout_kind = {
            "info": CalloutKind.INFO,
            "warning": CalloutKind.WARNING,
            "code": CalloutKind.CODE,
        }[block.kind]
        section.add_callout(Callout(block.body, kind=callout_kind, title=block.title))
        return
    if isinstance(block, CodeBlock):
        section.add_callout(
            Callout(block.content, kind=CalloutKind.CODE, title=block.language or None)
        )
        return


# --------------------------------------------------------------------------- #
# create_document — the FastMCP tool + shared server
# --------------------------------------------------------------------------- #


# FastMCP server name promotes the MINT brand. The user's
# claude_desktop_config.json keys ("mint-memo" or whatever they chose)
# are independent of this — that's the user's local alias for the
# server entry, not a discovered identity.
server = FastMCP(
    "MINT",
    instructions="MINT document generator: governed templates + GRACE audit trail",
)


async def _run_pipeline(
    intent: str,
    doc_type: str,
    source_md: str | None,
    ctx: Context,
    *,
    log_prefix: str = "MP-Doc",
) -> dict[str, Any]:
    """Internal pipeline — the testable, dict-returning core.

    The MCP-facing wrappers (`create_document`, `create_memo`) call this and
    rewrap the result with rich content blocks for cross-client artifact
    surfacing. `log_prefix` lets the create_memo alias keep the [MP-Memo]
    log marker family intact for V-MP-MEMO-POC scenarios.

    On the success path:
        {"status": "complete", "path": str, "audit_id": str,
         "fields_elicited": list[str], "doc_type": str,
         "template_version": str}

    On the chat-driven fallback path (client doesn't support
    elicitation/create — verified against Claude Desktop):
        {"status": "needs_more_info", "missing_fields": list[str],
         "extracted_so_far": dict, "guidance": str, "doc_type": str}
    """
    template = _load_template(doc_type)
    spec, anon = _extract_with_anonymisation(intent, source_md, log_prefix=log_prefix)

    # START_BLOCK_PARSE_INTENT
    logger.info(
        "[%s][create][BLOCK_PARSE_INTENT] "
        "doc_type=%s template_version=%s "
        "source_md_present=%s fields_extracted_heuristically=%s",
        log_prefix,
        doc_type,
        template.version,
        source_md is not None,
        spec.filled(template.required_fields),
    )
    # END_BLOCK_PARSE_INTENT

    fields_elicited: list[str] = []
    fields_pending: list[str] = []  # filled when elicitation isn't supported
    fields_heuristic: list[str] = list(spec.filled(template.required_fields))
    elicitation_supported = True

    for field_name in template.required_fields:
        if getattr(spec, field_name, None):
            continue

        if not elicitation_supported:
            # Client doesn't support server→client elicitation/create
            # (verified in this session against Claude Desktop's MCP impl).
            # Skip this field; we'll surface the full missing list to the
            # caller as a "needs_more_info" response.
            #
            # SECURITY (scenario-13 / forbidden-8): under anonymous flag,
            # do NOT echo blocklisted field names through missing_fields —
            # that would push the client to re-ask the user for the very
            # thing we're suppressing.
            if anon.anonymous_flag and field_name in _PERSONAL_FIELDS:
                continue
            fields_pending.append(field_name)
            continue

        # Anonymous flag + personal field: skip the elicit entirely
        # (scenario-12). The field is "optional under anonymity": we
        # neither raise DocumentElicitationRejected nor block the build;
        # the walker will skip the placeholder block downstream.
        if anon.anonymous_flag and field_name in _PERSONAL_FIELDS:
            continue

        prompt = _elicit_prompt(field_name, doc_type)
        # Emit personal_data=high hint marker for fields in the blocklist
        # (scenarios 7 + 17). This is a TRACE-side signal only — the
        # actual prompt text is unchanged; clients that respect MCP
        # elicit metadata could read this from log triage. The contract
        # is hint-only (forbidden-9).
        if field_name in _PERSONAL_FIELDS:
            logger.info(
                "[%s][elicit][BLOCK_PERSONAL_ELICIT_HINT] field=%s",
                log_prefix,
                field_name,
            )
        try:
            result = await ctx.elicit(
                message=prompt,
                response_type=str,  # type: ignore[arg-type]  # fastmcp overload picks str via response_type=str
                response_title=field_name,
            )
        except McpError as exc:
            # -32601 "Method not found" → client doesn't implement the
            # elicitation primitive. Switch to chat-driven mode for the rest
            # of this call: collect all remaining missing fields, return a
            # structured needs_more_info response.
            if getattr(exc.error, "code", None) == -32601:
                elicitation_supported = False
                fields_pending.append(field_name)

                # START_BLOCK_ELICIT_FIELD
                logger.info(
                    "[%s][elicit][BLOCK_ELICIT_FIELD] "
                    "field_name=%s action=unsupported attempt=1",
                    log_prefix,
                    field_name,
                )
                # END_BLOCK_ELICIT_FIELD
                continue
            raise  # other MCP errors propagate

        action = "accept" if isinstance(result, AcceptedElicitation) else "reject"

        # START_BLOCK_ELICIT_FIELD
        logger.info(
            "[%s][elicit][BLOCK_ELICIT_FIELD] "
            "field_name=%s action=%s attempt=1",
            log_prefix,
            field_name,
            action,
        )
        # END_BLOCK_ELICIT_FIELD

        if not isinstance(result, AcceptedElicitation):
            raise DocumentElicitationRejected(
                field_name=field_name,
                reason=getattr(result, "action", "decline"),
            )
        # AcceptedElicitation[T] carries .data
        setattr(spec, field_name, str(result.data))
        fields_elicited.append(field_name)

    # If the client couldn't elicit, return a structured needs_more_info
    # response. The connected model is expected to ask the user in chat
    # for the missing fields and re-invoke with a richer intent.
    if fields_pending:
        # SECURITY (forbidden-8): when under anonymous flag, suppress
        # blocklisted entries from extracted_so_far. The blocklist run
        # in _heuristic_extract already cleared the spec values, so this
        # filter is belt-and-braces against future regressions where the
        # spec might still carry a blocklisted value at this point.
        extracted_so_far = {
            name: getattr(spec, name, None)
            for name in template.required_fields
            if getattr(spec, name, None)
            and not (anon.anonymous_flag and name in _PERSONAL_FIELDS)
        }
        return _render_anonymisation_report(
            {
                "status": "needs_more_info",
                "missing_fields": fields_pending,
                "extracted_so_far": extracted_so_far,
                "doc_type": doc_type,
                "guidance": (
                    "Your MCP client doesn't support server-driven elicitation "
                    "forms. Ask the user in chat for the missing fields listed "
                    "in `missing_fields`, then call this tool again with a "
                    "richer intent that contains those values inline."
                ),
            },
            anonymised=anon.anonymous_flag,
            fields_omitted=anon.fields_omitted,
        )

    # All required fields filled; assemble.
    output_dir = _resolve_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_id = str(uuid.uuid4())
    output_path = output_dir / _document_filename(spec, audit_id, doc_type)

    try:
        doc = _build_document(spec, template)
        doc.save(output_path)
    except Exception as exc:
        raise DocumentGenerationFailed(
            f"DOC_GENERATION_FAILED: builder raised {type(exc).__name__}: {exc}"
        ) from exc

    docx_size = output_path.stat().st_size
    section_count = len(getattr(doc, "_sections", []))

    # START_BLOCK_BUILD_DOCX
    logger.info(
        "[%s][build][BLOCK_BUILD_DOCX] "
        "output_path=%s docx_size_bytes=%d sections_count=%d",
        log_prefix,
        output_path,
        docx_size,
        section_count,
    )
    # END_BLOCK_BUILD_DOCX

    # GRACE audit injection — embeds metadata into the saved docx as a
    # custom XML part. The audit_id (UUID4) is the cross-reference back
    # to this generation; fields_elicited records what came from the user
    # vs the heuristic. Under anonymous flag we also stamp anonymised=true
    # (scenario-19); the cleared values are NEVER written into the
    # manifest (forbidden-6 extended to audit trail).
    audit_instructions = list(
        _audit_instructions(
            audit_id,
            fields_elicited,
            doc_type,
            template.version,
            template_author=template.author,
            anonymised=anon.anonymous_flag,
            preset_name="klawd",
            required_fields=template.required_fields,
            fields_heuristic=fields_heuristic,
            log_prefix=log_prefix,
        )
    )
    manifest = grace_bootstrap(
        document_path=output_path,
        rules=audit_instructions,
        output_path=output_path,
    )

    # START_BLOCK_INJECT_GRACE
    logger.info(
        "[%s][grace][BLOCK_INJECT_GRACE] "
        "audit_id=%s instructions_count=%d manifest_size_bytes=%d",
        log_prefix,
        audit_id,
        len(manifest.instructions),
        output_path.stat().st_size - docx_size,
    )
    # END_BLOCK_INJECT_GRACE

    # MP-VISUAL-QA-HOOK — advisory post-create_document visual quality gate.
    # The hook NEVER fails the tool: every error path collapses to a logged
    # WARNING (score_document handles its own try/except internally; this
    # outer try/except is defense-in-depth per VF-019 inv-1 ADVISORY-ONLY).
    # When MINT_SKIP_VISUAL_QA=1, score_document returns None and the
    # `visual_qa` key is omitted from structured_content entirely (env-skip
    # is "user opted out, don't show anything"; backend-skip is "we tried
    # but couldn't, ops should know" — different signals, different shapes).
    result_dict: dict[str, Any] = {
        "status": "complete",
        "path": str(output_path),
        "audit_id": audit_id,
        "fields_elicited": fields_elicited,
        "fields_heuristic": fields_heuristic,
        "doc_type": doc_type,
        "template_version": template.version,
    }
    _render_anonymisation_report(
        result_dict,
        anonymised=anon.anonymous_flag,
        fields_omitted=anon.fields_omitted,
    )
    try:
        qa_report = _score_document(output_path, preset_name="klawd")
    except Exception as exc:
        logger.warning(
            "[MP-VisualQA][score][BLOCK_QA_BACKEND_UNAVAILABLE] reason=%s",
            type(exc).__name__,
        )
        qa_report = None
    if qa_report is not None:
        result_dict["visual_qa"] = qa_report.to_dict()
    return result_dict


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "MINT"


def _resolve_output_dir() -> Path:
    """Resolve the directory generated documents land in. Default
    ~/Documents/MINT; override via MINT_MEMO_DIR env var (name retained
    for backwards-compat with Phase-13 smoke configs; W2+ may add
    MINT_OUTPUT_DIR). Avoids /tmp because Claude Desktop's sandbox hides
    /tmp from the user (verified during 2026-05-10 smoke), and macOS reaps
    /tmp aggressively."""
    import os

    override = os.environ.get("MINT_MEMO_DIR")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_OUTPUT_DIR


def _document_filename(spec: DocumentSpec, audit_id: str, doc_type: str) -> str:
    """Produce a stable, human-readable filename. Format:
    `<doc_type>_<YYYY-MM-DD>_<subject-slug>_<audit-short>.docx`. The audit
    short suffix prevents collisions when two documents share doc_type +
    date + subject."""
    date_part = (spec.date or datetime.now(tz=UTC).strftime("%Y-%m-%d")).strip()
    # Sanitize: keep ISO-shape only; otherwise fall back to today.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_part):
        date_part = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    subject_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", spec.subject or doc_type).strip("_")[:40]
    short_id = audit_id.split("-")[0]
    return f"{doc_type}_{date_part}_{subject_slug}_{short_id}.docx"


def _elicit_prompt(field_name: str, doc_type: str) -> str:
    """User-facing prompt for each required field. Concise; the connected
    MCP client renders it in the form's label.

    The prompt set is the union of known field names across doc_types — when
    a doc_type asks for a field we don't have a custom prompt for, the
    fallback is generic ("Provide a value for <field>:"). doc_type is
    threaded through so future tweaks can scope phrasing per doc_type
    without per-doc_type if/else branches in the pipeline (the prompt set
    moves into templates/<doc_type>.yaml in W2)."""
    del doc_type  # reserved for the W2 templates-driven prompt registry
    prompts = {
        "sender": "Who is the document from? (full name + title)",
        "recipient": "Who is the document addressed to?",
        "date": "What date should the document carry? (e.g. 2026-05-15)",
        "subject": "What is the document's subject line?",
        "body": "What is the body of the document?",
    }
    return prompts.get(field_name, f"Provide a value for {field_name}:")


def _detect_template_languages(
    required_fields: tuple[str, ...],
) -> list[str]:
    """Detect bilingual/multilingual field suffixes in template
    required_fields using an explicit ISO-639 allowlist.

    Returns sorted list of distinct language codes when ≥2 are found,
    empty list otherwise. Case-sensitive: only lowercase suffixes match
    (scenario-9 — `scope_RU` does NOT count). Uses `_ISO_LANG_CODES`
    explicit allowlist, NOT a generic `[a-z]{2,3}` regex (forbidden-4).

    Algorithm:
      1. For each field name, try to split on the LAST `_` delimiter.
      2. If the suffix is in `_ISO_LANG_CODES`, record it.
      3. Return sorted unique codes if count ≥ 2, else [].
    """
    codes: set[str] = set()
    for field_name in required_fields:
        m = _LANG_SUFFIX_RE.match(field_name)
        if m and m.group("lang") in _ISO_LANG_CODES:
            codes.add(m.group("lang"))
    return sorted(codes) if len(codes) >= 2 else []


def _resolve_active_preset_version(preset_name: str) -> str:
    """Resolve the current version string for the named preset.

    Uses `resolve_latest_preset_path` from preset_edit.py (single source
    of truth for semver resolution). Falls back to '1.0' on ANY error
    (forbidden-3 — never raises; terminal '1.0' fallback per
    forbidden-8).

    Lazy-imports preset_edit to avoid the circular-import cycle:
    preset_edit → document.server → ... → preset_edit.
    """
    try:
        from mint_python.mcp.preset_edit import resolve_latest_preset_path

        preset_path = resolve_latest_preset_path(preset_name)
        # Versioned sibling: parse version from filename.
        m = re.search(r"_v(\d+\.\d+)\.yaml$", preset_path.name)
        if m:
            return m.group(1)
        # BUILTIN_PRESETS baseline: read version from YAML content.
        raw = yaml.safe_load(preset_path.read_text(encoding="utf-8")) or {}
        v = raw.get("version")
        if v:
            return str(v)
    except Exception:
        logger.debug(
            "[MP-Doc][audit] preset version resolution failed; "
            "falling back to '1.0'",
            exc_info=True,
        )
    return "1.0"


def _audit_instructions(
    audit_id: str,
    fields_elicited: list[str],
    doc_type: str,
    template_version: str,
    template_author: str = "",
    *,
    anonymised: bool = False,
    preset_name: str = "klawd",
    required_fields: tuple[str, ...] = (),
    fields_heuristic: list[str] | None = None,
    log_prefix: str = "MP-Doc",
) -> list[str]:
    """Compose the GRACE manifest instruction list for this run.

    Includes the audit_id (cross-reference to logs), elicited-field list
    (provenance: user vs heuristic), doc_type + template_version (so the
    docx records which template authored its layout) + template_author
    when present (Phase-14 W3 records who wrote the template version
    that produced this document — closes the cross-model handoff
    audit-trail gap). Backwards-compat: the legacy
    `generated_by=MP-MEMO-POC` line stays for memo doc_type so existing
    tests' substring asserts on the manifest XML keep matching; other
    doc_types record MP-DOC-GENERIC instead.

    Phase-17 W17-3 (MP-AUDIT-EXTEND) additions:
      - `preset_version=<version>` — ALWAYS stamped; resolves the latest
        versioned preset via resolve_latest_preset_path (closes
        V-MP-THEME-EDIT scenario-10).
      - `lang=<comma-separated codes>` — stamped when template has ≥2
        distinct language-suffix fields (closes V-MP-DOC-BUNDLE
        scenario-7b).

    When the anonymous-flag mitigation fired (MP-DOC-PERSONAL-GUARD),
    append `anonymised=true` to the manifest — flag only, NEVER the
    cleared values (forbidden-6 / scenario-19).
    """
    timestamp = datetime.now(tz=UTC).isoformat()
    generator = "MP-MEMO-POC" if doc_type == "memo" else "MP-DOC-GENERIC"

    # START_BLOCK_AUDIT_PRESET_VERSION
    preset_version = _resolve_active_preset_version(preset_name)
    logger.info(
        "[%s][grace][BLOCK_AUDIT_PRESET_VERSION] "
        "preset=%s preset_version=%s",
        log_prefix,
        preset_name,
        preset_version,
    )
    # END_BLOCK_AUDIT_PRESET_VERSION

    lang_codes = _detect_template_languages(required_fields)
    if lang_codes:
        # START_BLOCK_AUDIT_LANG
        logger.info(
            "[%s][grace][BLOCK_AUDIT_LANG] "
            "lang_codes=%s field_count=%d",
            log_prefix,
            ",".join(lang_codes),
            len(required_fields),
        )
        # END_BLOCK_AUDIT_LANG

    instructions = [
        f"audit_id={audit_id}",
        f"generated_by={generator}",
        f"generated_at={timestamp}",
        f"fields_elicited={','.join(fields_elicited) if fields_elicited else '(none)'}",
        f"fields_heuristic={','.join(fields_heuristic) if fields_heuristic else '(none)'}",
        f"template={doc_type}.yaml",
        f"template_version={template_version}",
        "preset=klawd",
        f"preset_version={preset_version}",
    ]
    if template_author:
        instructions.append(f"template_author={template_author}")
    if lang_codes:
        instructions.append(f"lang={','.join(lang_codes)}")
    if anonymised:
        instructions.append("anonymised=true")
    return instructions


@server.tool(name="mint_create_document")
async def create_document(
    intent: str,
    doc_type: str,
    source_md: str | None = None,
    *,
    ctx: Context,
) -> ToolResult:
    """Generate a klawd-themed document of the requested doc_type via
    planning dialog.

    Looks up `templates/<doc_type>.yaml` (W2 replaces this with the
    governed registry), heuristically extracts known fields from the
    free-text intent (and optional source_md), elicits any still-missing
    required fields via ctx.elicit (with chat-driven fallback when the
    client doesn't implement elicitation/create — verified empirically
    against Claude Desktop), assembles the docx via MP-DOCUMENT with the
    klawd preset and the template's layout, injects a GRACE audit-trail
    manifest carrying doc_type + template_version, and returns a
    triple-shape result optimized for cross-client artifact surfacing.

    IMPORTANT — relaying the file path to the user: when this tool
    succeeds, the response text contains a file:// URI pointing at the
    saved document. Always include that URI verbatim in your reply to
    the user; do not paraphrase it away or omit it. The user opens the
    document by clicking that link, and a summary that drops the path
    leaves them unable to reach the file (closes #3).

    Raises:
        DocumentTypeNotFound: when no template exists for doc_type.
        DocumentElicitationRejected: when the user declines or cancels an
            elicit prompt for a required field.
        DocumentGenerationFailed: when the assembler raises after all
            fields are collected.
    """
    from mint_python.mcp.telemetry import track_call
    with track_call("mint_create_document", doc_type=doc_type):
        result = await _run_pipeline(intent, doc_type, source_md, ctx)
        return _to_tool_result(result)


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _to_tool_result(result: dict[str, Any]) -> ToolResult:
    """Wrap a pipeline result dict in a ToolResult with rich content blocks
    for cross-client artifact surfacing. Returns TextContent with file:// URI
    + ResourceLink for clients that resolve resource references."""
    if result.get("status") != "complete":
        doc_type = result.get("doc_type", "document")
        text_summary = (
            f"⚠️ Need more info — missing: "
            f"{', '.join(result.get('missing_fields', []))}. "
            f"Please provide values inline and call the {doc_type} tool again."
        )
        return ToolResult(
            content=[TextContent(type="text", text=text_summary)],
            structured_content=result,
        )

    path_str = result["path"]
    path = Path(path_str)
    audit_id = result["audit_id"]
    doc_type = result.get("doc_type", "document")
    file_uri = path.absolute().as_uri()
    text_summary = (
        f"✅ {doc_type.title()} ready.\n"
        f"**Open:** [{path.name}]({file_uri})\n"
        f"**File path:** {file_uri}\n"
        f"audit_id: `{audit_id}`"
    )

    return ToolResult(
        content=[
            TextContent(type="text", text=text_summary),
            ResourceLink(
                type="resource_link",
                uri=AnyUrl(file_uri),
                name=path.name,
                mimeType=_DOCX_MIME,
                description=f"Generated {doc_type} (audit_id={audit_id})",
                size=path.stat().st_size if path.exists() else None,
            ),
        ],
        structured_content=result,
    )


__all__ = [
    "MEMO_REQUIRED_FIELDS",
    "DocumentElicitationRejected",
    "DocumentError",
    "DocumentGenerationFailed",
    "DocumentSpec",
    "DocumentTemplate",
    "DocumentTemplateNotFound",
    "DocumentTypeNotFound",
    "create_document",
    "server",
]

# --------------------------------------------------------------------------- #
# Deferred surface registration
# --------------------------------------------------------------------------- #
#
# Phase-14 W2/W3/W4 modules attach @server.tool and @server.resource
# handlers to the shared `server` instance defined above. They have to
# import FROM this module (for `server` and `_TEMPLATES_DIR`), so we
# import them HERE at module-load tail — by this point `server` is fully
# defined, and importing them now triggers their decorator registrations
# without circularity.
#
# Without these tail imports, claude_desktop_config.json (which loads
# `from mint_python.mcp.memo import server`) sees ONLY create_document
# and create_memo — list_templates, get_template, update_template,
# list_presets, get_preset, and the mint:// resource handlers stay
# unregistered and invisible to live MCP clients.
# Phase-16 W3 (MCP-tool parity completion):
# mint_edit_document (over W3b MP-EDIT port; depends transitively on W3a MP-OOXML).
from mint_python.mcp import edit as _edit  # noqa: E402, F401

# Phase-16 W2 (MCP-tool parity + structured preset editor):
# mint_fingerprint_document (over W1 MP-FINGERPRINT port);
# mint_extract_content (over W1 MP-EXTRACT port);
# mint_update_preset_{palette,typography,spacing} (gated through MP-AUTH-SHIM).
from mint_python.mcp import extract as _extract  # noqa: E402, F401
from mint_python.mcp import fingerprint as _fingerprint  # noqa: E402, F401

# Phase-16 W1 (MCP-tool parity): mint_validate_document + mint_fix_document
# tail-register via the same deferred-import pattern.
from mint_python.mcp import fix as _fix  # noqa: E402, F401
from mint_python.mcp import manifest as _manifest  # noqa: E402, F401

# Phase-17 W17-2: mint_suggest_template (template-picker UX).
# Tail-registered so external MCP clients (Claude Desktop / Cursor /
# OpenWebUI) see the tool. The DocumentTypeNotFound raise path inside
# _load_template lazy-imports `suggest_templates` (circular-import-safe).
from mint_python.mcp import picker as _picker  # noqa: E402, F401
from mint_python.mcp import preset_edit as _preset_edit  # noqa: E402, F401
from mint_python.mcp import resources as _resources  # noqa: E402, F401
from mint_python.mcp import validate as _validate  # noqa: E402, F401
from mint_python.templates import registry as _registry  # noqa: E402, F401
