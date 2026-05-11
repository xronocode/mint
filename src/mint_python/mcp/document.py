# FILE: src/mint_python/mcp/document.py
# VERSION: 0.2.0
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
#   server                       - FastMCP server instance (shared with
#                                  mcp/memo.py's create_memo)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 — Phase-14 W1 (MP-DOC-GENERIC). Renamed module
#     memo.py → document.py; generalized Memo* types → Document*; added
#     doc_type parameter through the pipeline and into structured_content
#     (carries doc_type + template_version per V-MP-DOC-GENERIC
#     scenario-6); log prefix is parameterized so create_memo alias keeps
#     [MP-Memo] markers. Backwards-compat shim in mcp/memo.py.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastmcp import Context, FastMCP
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.tools.tool import ToolResult  # type: ignore[import-not-found]
from mcp.shared.exceptions import McpError
from mcp.types import ResourceLink, TextContent

from mint_python.adapters.markdown import markdown_to_spec
from mint_python.core.content import Paragraph
from mint_python.core.section import Section
from mint_python.core.table import Table
from mint_python.grace import bootstrap as grace_bootstrap
from mint_python.qa.visual import score_document as _score_document

logger = logging.getLogger(__name__)


MEMO_REQUIRED_FIELDS: tuple[str, ...] = (
    "sender",
    "recipient",
    "date",
    "subject",
    "body",
)


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
    """


class DocumentGenerationFailed(DocumentError):  # noqa: N818 — error code DOC_GENERATION_FAILED mirrors class name; suffix omitted intentionally
    """Builder failed at render time after all required fields were collected."""


# --------------------------------------------------------------------------- #
# DocumentSpec dataclass + heuristic field extractor
# --------------------------------------------------------------------------- #


@dataclass
class DocumentSpec:
    """Generic content fields covering every known doc_type's union.

    Today (W1) only memo's 5 fields are present — the same shape MEMO-POC
    used. As Phase-14 lands new doc_types, fields get added here (Letter
    will need sender_address / recipient_address / salutation / closing;
    Report will need executive_summary / recommendation, etc.). The
    template's required_fields list determines which subset is actively
    elicited for a given call.
    """

    sender: str | None = None
    recipient: str | None = None
    date: str | None = None
    subject: str | None = None
    body: str | None = None

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
# when asked to be explicit; we match the conventional 5 labels.
_LABEL_RE = re.compile(
    # `[ \t]*` after the colon (NOT `\s*`) — otherwise `\s` consumes the
    # newline and an empty `subject:\n` line eats the next line as its value.
    r"^[ \t]*(sender|from|recipient|to|date|subject|body)[ \t]*:[ \t]*(.+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
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
    r"\.\s+(?=(?:sender|from|recipient|to|date|subject|body)\s*:)",
    re.IGNORECASE,
)


def _heuristic_extract(intent: str, source_md: str | None) -> DocumentSpec:
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
        else:
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
            spec.body = None

    return spec


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
        raise DocumentTypeNotFound(
            f"DOC_TYPE_NOT_FOUND: no template for doc_type={doc_type!r}. "
            f"Available: {', '.join(available) if available else '(none)'}"
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


def _build_document(spec: DocumentSpec, template: DocumentTemplate) -> DocumentLike:
    """Walk template.layout and assemble a Document with klawd preset.

    Returns the configured Document instance — caller saves and (optionally)
    injects GRACE manifest after.
    """
    # Local import to keep the module top-level light and avoid a circular
    # import surface during type-checking. Document is the SDK facade.
    from mint_python.core.document import Document

    doc = Document(
        format="docx",
        title=spec.subject or template.name.title() or "Document",
    ).with_style_preset("klawd")

    section: Section | None = None
    for entry in template.layout:
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


# Type alias for the Document return — helps downstream readers without
# forcing the import at module top-level (Document carries a heavy import
# graph that we don't want eagerly loaded just for type hints).
DocumentLike = Any


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
    spec = _heuristic_extract(intent, source_md)

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
    elicitation_supported = True

    for field_name in template.required_fields:
        if getattr(spec, field_name, None):
            continue

        if not elicitation_supported:
            # Client doesn't support server→client elicitation/create
            # (verified in this session against Claude Desktop's MCP impl).
            # Skip this field; we'll surface the full missing list to the
            # caller as a "needs_more_info" response.
            fields_pending.append(field_name)
            continue

        prompt = _elicit_prompt(field_name, doc_type)
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
        return {
            "status": "needs_more_info",
            "missing_fields": fields_pending,
            "extracted_so_far": {
                name: getattr(spec, name, None)
                for name in template.required_fields
                if getattr(spec, name, None)
            },
            "doc_type": doc_type,
            "guidance": (
                "Your MCP client doesn't support server-driven elicitation "
                "forms. Ask the user in chat for the missing fields listed "
                "in `missing_fields`, then call this tool again with a "
                "richer intent that contains those values inline."
            ),
        }

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
    # vs the heuristic.
    audit_instructions = list(
        _audit_instructions(
            audit_id,
            fields_elicited,
            doc_type,
            template.version,
            template_author=template.author,
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
        "doc_type": doc_type,
        "template_version": template.version,
    }
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


def _audit_instructions(
    audit_id: str,
    fields_elicited: list[str],
    doc_type: str,
    template_version: str,
    template_author: str = "",
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
    """
    timestamp = datetime.now(tz=UTC).isoformat()
    generator = "MP-MEMO-POC" if doc_type == "memo" else "MP-DOC-GENERIC"
    instructions = [
        f"audit_id={audit_id}",
        f"generated_by={generator}",
        f"generated_at={timestamp}",
        f"fields_elicited={','.join(fields_elicited) if fields_elicited else '(none)'}",
        f"template={doc_type}.yaml",
        f"template_version={template_version}",
        "preset=klawd",
    ]
    if template_author:
        instructions.append(f"template_author={template_author}")
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
    result = await _run_pipeline(intent, doc_type, source_md, ctx)
    return _to_tool_result(result)


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _to_tool_result(result: dict[str, Any]) -> ToolResult:
    """Wrap a pipeline result dict in a ToolResult with rich content blocks
    for cross-client artifact surfacing. Strategy follows the May-2026
    research findings: markdown link in text + resource_link spec primitive
    + structured content for machines."""
    if result.get("status") != "complete":
        # Degraded path — only structured content; the model knows how to
        # render the needs_more_info shape and ask the user.
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
    # Imperative-style format with the file:// URI on its own line —
    # easier for orchestrating models to relay verbatim than a
    # markdown link buried in prose. Closes #3.
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
                uri=file_uri,  # type: ignore[arg-type]  # ResourceLink.uri = AnyUrl, str literal accepted
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
from mint_python.mcp import manifest as _manifest  # noqa: E402, F401
from mint_python.mcp import resources as _resources  # noqa: E402, F401
from mint_python.templates import registry as _registry  # noqa: E402, F401

# Phase-16 W1 (MCP-tool parity): mint_validate_document + mint_fix_document
# tail-register via the same deferred-import pattern.
from mint_python.mcp import fix as _fix  # noqa: E402, F401
from mint_python.mcp import validate as _validate  # noqa: E402, F401

# Phase-16 W2 (MCP-tool parity + structured preset editor):
# mint_fingerprint_document (over W1 MP-FINGERPRINT port);
# mint_extract_content (over W1 MP-EXTRACT port);
# mint_update_preset_{palette,typography,spacing} (gated through MP-AUTH-SHIM).
# W3 will append edit.
from mint_python.mcp import extract as _extract  # noqa: E402, F401
from mint_python.mcp import fingerprint as _fingerprint  # noqa: E402, F401
from mint_python.mcp import preset_edit as _preset_edit  # noqa: E402, F401
