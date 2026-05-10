# FILE: src/mint_python/mcp/memo.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: FastMCP create_memo tool — end-to-end POC of the planning-mode
#     dialog pattern via the MCP elicitation primitive (spec 2025-06-18,
#     fastmcp.Context.elicit). Closes the loop: caller sends a free-text
#     intent, the tool extracts what it can heuristically, elicits each
#     missing required field through the connected MCP client (Claude
#     Desktop primary; OpenWebUI / Cursor / Copilot follow), assembles a
#     klawd-themed Memo via MP-DOCUMENT, injects a GRACE audit-trail
#     manifest, and returns the saved docx path.
#   SCOPE: Public surface = create_memo (FastMCP tool), MemoSpec dataclass,
#     MemoElicitationRejected error, MemoTemplateNotFound error,
#     MemoGenerationFailed error. The Memo template structure (which
#     fields, in what order, what blocks) lives in templates/memo.yaml as
#     DATA — not embedded here. This is the canonical example Phase-14
#     doc-type tools (Letter, Report, Contract) follow.
#   DEPENDS: fastmcp (Context, FastMCP), mint_python.core.document
#     (Document), mint_python.core.section (Section), mint_python.core
#     .table (Table), mint_python.core.content (Paragraph), mint_python
#     .adapters.markdown (markdown_to_spec for source_md fact extraction),
#     mint_python.grace (bootstrap for audit-trail injection), pyyaml
#     (templates/memo.yaml loader).
#   LINKS: docs/development-plan.xml#MP-MEMO-POC,
#     docs/verification-plan.xml#V-MP-MEMO-POC,
#     docs/knowledge-graph.xml#MP-MEMO-POC
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   create_memo                  - @mcp.tool async fn; the only public entry
#   MemoSpec                     - frozen dataclass with 5 required fields
#   MemoElicitationRejected      - error raised when user declines elicit
#   MemoTemplateNotFound         - error raised when memo.yaml missing
#   MemoGenerationFailed         - error raised when builder fails
#   MEMO_REQUIRED_FIELDS         - tuple of field names in elicitation order
#   server                       - FastMCP server instance (mcp/memo)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-13 step-3 initial implementation per
#     V-MP-MEMO-POC scenarios 1-9.
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


class MemoError(Exception):
    """Base for MEMO-POC errors."""


class MemoElicitationRejected(MemoError):  # noqa: N818 — error code MEMO_ELICITATION_REJECTED mirrors class name; suffix omitted intentionally
    """User declined / cancelled an elicitation request for a required field.

    Carries the field name so callers can surface a useful message; no docx
    is produced when this fires.
    """

    def __init__(self, field_name: str, reason: str = "declined") -> None:
        self.field_name = field_name
        self.reason = reason
        super().__init__(
            f"MEMO_ELICITATION_REJECTED: user {reason} elicitation for "
            f"required field {field_name!r}"
        )


class MemoTemplateNotFound(MemoError):  # noqa: N818 — error code MEMO_TEMPLATE_NOT_FOUND mirrors class name; suffix omitted intentionally
    """templates/memo.yaml missing on disk."""


class MemoGenerationFailed(MemoError):  # noqa: N818 — error code MEMO_GENERATION_FAILED mirrors class name; suffix omitted intentionally
    """Builder failed at render time after all required fields were collected."""


# --------------------------------------------------------------------------- #
# MemoSpec dataclass + heuristic field extractor
# --------------------------------------------------------------------------- #


@dataclass
class MemoSpec:
    """Memo content fields. POC scope keeps 5 required fields tight; cc /
    signature / classification / distribution_list extend in Phase-14."""

    sender: str | None = None
    recipient: str | None = None
    date: str | None = None
    subject: str | None = None
    body: str | None = None

    def filled(self) -> list[str]:
        return [
            name
            for name in MEMO_REQUIRED_FIELDS
            if getattr(self, name)
        ]


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


def _heuristic_extract(intent: str, source_md: str | None) -> MemoSpec:
    """Best-effort field extractor over free-text intent + optional source.

    Conservative — when uncertain, leaves the field as None so elicitation
    kicks in. False positives here silently produce wrong content; false
    negatives just mean an extra elicit call.

    Strategy is layered:
      1. Try the labelled-key form ("sender: X\\nrecipient: Y\\n...") which
         LLMs emit naturally when asked to be explicit.
      2. Fall back to prose patterns ("Memo from X to Y about Z").
      3. Body extraction tries (in order): labelled "Body:\\n\\n…" block,
         single-line "body: …", source_md content.
    """
    spec = MemoSpec()

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
            setattr(spec, target, value)

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


_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "templates" / "memo.yaml"
)


@dataclass
class MemoTemplate:
    """Loaded templates/memo.yaml — the data-driven memo layout."""

    name: str
    version: str
    required_fields: tuple[str, ...]
    layout: list[dict[str, Any]] = field(default_factory=list)


def _load_template() -> MemoTemplate:
    if not _TEMPLATE_PATH.exists():
        raise MemoTemplateNotFound(
            f"MEMO_TEMPLATE_NOT_FOUND: templates/memo.yaml not at {_TEMPLATE_PATH}"
        )
    raw = yaml.safe_load(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return MemoTemplate(
        name=str(raw.get("name", "memo")),
        version=str(raw.get("version", "1.0")),
        required_fields=tuple(raw.get("required_fields", MEMO_REQUIRED_FIELDS)),
        layout=list(raw.get("layout", [])),
    )


# --------------------------------------------------------------------------- #
# Builder — assemble Document from MemoSpec via templates/memo.yaml
# --------------------------------------------------------------------------- #


def _substitute(text: str, spec: MemoSpec) -> str:
    """Replace `{{ field }}` placeholders with the spec's field values."""

    def _resolve(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        value = getattr(spec, name, None)
        return str(value) if value is not None else ""

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", _resolve, text)


def _build_document(spec: MemoSpec, template: MemoTemplate) -> DocumentLike:
    """Walk template.layout and assemble a Document with klawd preset.

    Returns the configured Document instance — caller saves and (optionally)
    injects GRACE manifest after.
    """
    # Local import to keep the module top-level light and avoid a circular
    # import surface during type-checking. Document is the SDK facade.
    from mint_python.core.document import Document

    doc = Document(format="docx", title=spec.subject or "Memorandum").with_style_preset("klawd")

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
            section = Section(title="Memo", level=1)
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
# create_memo — the FastMCP tool
# --------------------------------------------------------------------------- #


server = FastMCP("MINT-Memo", instructions="Phase-13 MEMO-POC: planning-mode memo generator")


async def _run_memo_pipeline(
    intent: str,
    source_md: str | None,
    ctx: Context,
) -> dict[str, Any]:
    """Internal pipeline — the testable, dict-returning core. The MCP-facing
    wrapper `create_memo` calls this and rewraps the result with rich
    content blocks (markdown link + resource_link + structured) for cross-
    client artifact surfacing.

    On the success path the dict shape is:
        {"status": "complete", "path": str, "audit_id": str,
         "fields_elicited": list[str]}

    On the chat-driven fallback path (client doesn't support
    elicitation/create — verified against Claude Desktop):
        {"status": "needs_more_info", "missing_fields": list[str],
         "extracted_so_far": dict, "guidance": str}
    """
    spec = _heuristic_extract(intent, source_md)

    # START_BLOCK_PARSE_INTENT
    logger.info(
        "[MP-Memo][create][BLOCK_PARSE_INTENT] "
        "source_md_present=%s fields_extracted_heuristically=%s",
        source_md is not None,
        spec.filled(),
    )
    # END_BLOCK_PARSE_INTENT

    fields_elicited: list[str] = []
    fields_pending: list[str] = []  # filled when elicitation isn't supported
    elicitation_supported = True

    for field_name in MEMO_REQUIRED_FIELDS:
        if getattr(spec, field_name):
            continue

        if not elicitation_supported:
            # Client doesn't support server→client elicitation/create
            # (verified in this session against Claude Desktop's MCP impl).
            # Skip this field; we'll surface the full missing list to the
            # caller as a "needs_more_info" response.
            fields_pending.append(field_name)
            continue

        prompt = _elicit_prompt(field_name)
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
                    "[MP-Memo][elicit][BLOCK_ELICIT_FIELD] "
                    "field_name=%s action=unsupported attempt=1",
                    field_name,
                )
                # END_BLOCK_ELICIT_FIELD
                continue
            raise  # other MCP errors propagate

        action = "accept" if isinstance(result, AcceptedElicitation) else "reject"

        # START_BLOCK_ELICIT_FIELD
        logger.info(
            "[MP-Memo][elicit][BLOCK_ELICIT_FIELD] "
            "field_name=%s action=%s attempt=1",
            field_name,
            action,
        )
        # END_BLOCK_ELICIT_FIELD

        if not isinstance(result, AcceptedElicitation):
            raise MemoElicitationRejected(
                field_name=field_name,
                reason=getattr(result, "action", "decline"),
            )
        # AcceptedElicitation[T] carries .data
        setattr(spec, field_name, str(result.data))
        fields_elicited.append(field_name)

    # If the client couldn't elicit, return a structured needs_more_info
    # response. The connected model is expected to ask the user in chat
    # for the missing fields and re-invoke create_memo with a fuller intent.
    if fields_pending:
        return {
            "status": "needs_more_info",
            "missing_fields": fields_pending,
            "extracted_so_far": {
                name: getattr(spec, name)
                for name in MEMO_REQUIRED_FIELDS
                if getattr(spec, name)
            },
            "guidance": (
                "Your MCP client doesn't support server-driven elicitation "
                "forms. Ask the user in chat for the missing fields listed "
                "in `missing_fields`, then call create_memo again with a "
                "richer intent that contains those values inline."
            ),
        }

    # All required fields filled; load template and assemble.
    template = _load_template()
    output_dir = _resolve_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_id = str(uuid.uuid4())
    output_path = output_dir / _memo_filename(spec, audit_id)

    try:
        doc = _build_document(spec, template)
        doc.save(output_path)
    except Exception as exc:
        raise MemoGenerationFailed(
            f"MEMO_GENERATION_FAILED: builder raised {type(exc).__name__}: {exc}"
        ) from exc

    docx_size = output_path.stat().st_size
    section_count = len(getattr(doc, "_sections", []))

    # START_BLOCK_BUILD_DOCX
    logger.info(
        "[MP-Memo][build][BLOCK_BUILD_DOCX] "
        "output_path=%s docx_size_bytes=%d sections_count=%d",
        output_path,
        docx_size,
        section_count,
    )
    # END_BLOCK_BUILD_DOCX

    # GRACE audit injection — embeds metadata into the saved docx as a
    # custom XML part. The audit_id (UUID4) is the cross-reference back
    # to this generation; fields_elicited records what came from the user
    # vs the heuristic.
    audit_instructions = list(_audit_instructions(audit_id, fields_elicited))
    manifest = grace_bootstrap(
        document_path=output_path,
        rules=audit_instructions,
        output_path=output_path,
    )

    # START_BLOCK_INJECT_GRACE
    logger.info(
        "[MP-Memo][grace][BLOCK_INJECT_GRACE] "
        "audit_id=%s instructions_count=%d manifest_size_bytes=%d",
        audit_id,
        len(manifest.instructions),
        output_path.stat().st_size - docx_size,
    )
    # END_BLOCK_INJECT_GRACE

    return {
        "status": "complete",
        "path": str(output_path),
        "audit_id": audit_id,
        "fields_elicited": fields_elicited,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "MINT"


def _resolve_output_dir() -> Path:
    """Resolve the directory generated memos land in. Default ~/Documents/MINT;
    override via MINT_MEMO_DIR env var. Avoids /tmp because Claude Desktop's
    sandbox hides /tmp from the user (verified during 2026-05-10 smoke), and
    macOS reaps /tmp aggressively."""
    import os

    override = os.environ.get("MINT_MEMO_DIR")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_OUTPUT_DIR


def _memo_filename(spec: MemoSpec, audit_id: str) -> str:
    """Produce a stable, human-readable filename. Format:
    `memo_<YYYY-MM-DD>_<subject-slug>_<audit-short>.docx`. The audit short
    suffix prevents filename collisions when two memos share date+subject."""
    date_part = (spec.date or datetime.now(tz=UTC).strftime("%Y-%m-%d")).strip()
    # Sanitize: keep ISO-shape only; otherwise fall back to today.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_part):
        date_part = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    subject_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", spec.subject or "memo").strip("_")[:40]
    short_id = audit_id.split("-")[0]
    return f"memo_{date_part}_{subject_slug}_{short_id}.docx"


def _elicit_prompt(field_name: str) -> str:
    """User-facing prompt for each required field. Concise; the connected
    MCP client renders it in the form's label."""
    prompts = {
        "sender": "Who is the memo from? (full name + title)",
        "recipient": "Who is the memo addressed to?",
        "date": "What date should the memo carry? (e.g. 2026-05-15)",
        "subject": "What is the memo's subject line?",
        "body": "What is the body of the memo?",
    }
    return prompts.get(field_name, f"Provide a value for {field_name}:")


def _audit_instructions(audit_id: str, fields_elicited: list[str]) -> list[str]:
    """Compose the GRACE manifest instruction list for this memo run.

    Includes the audit_id (so future readers can cross-reference logs),
    the elicited-field list (so a reader knows which content came from
    the user vs the model heuristic), and a timestamp.
    """
    timestamp = datetime.now(tz=UTC).isoformat()
    return [
        f"audit_id={audit_id}",
        "generated_by=MP-MEMO-POC",
        f"generated_at={timestamp}",
        f"fields_elicited={','.join(fields_elicited) if fields_elicited else '(none)'}",
        "template=memo.yaml",
        "preset=klawd",
    ]


@server.tool
async def create_memo(
    intent: str,
    source_md: str | None = None,
    *,
    ctx: Context,
) -> ToolResult:
    """Generate a klawd-themed Memo via planning dialog.

    Heuristically extracts sender / recipient / date / subject / body from
    the free-text intent (and optional source_md), elicits any still-missing
    required fields via ctx.elicit (with chat-driven fallback when the
    client doesn't implement elicitation/create — verified empirically
    against Claude Desktop), assembles the docx via MP-DOCUMENT with the
    klawd preset and the templates/memo.yaml layout, injects a GRACE
    audit-trail manifest, and returns a triple-shape result optimized for
    cross-client artifact surfacing:

    - **TextContent** with a markdown link `[Open memo.docx](file://…)` —
      universally clickable in Claude Desktop, Cursor, OpenWebUI.
    - **ResourceLink** with the same `file://` URI + docx mimetype — the
      MCP-spec primitive for "downloadable artifact"; honored by Cursor
      and VS Code, harmlessly ignored elsewhere.
    - **structuredContent** with `{status, path, audit_id, fields_elicited}`
      for programmatic consumers.

    On the chat-driven fallback path (status=needs_more_info) only
    structuredContent is returned with the missing fields list — the
    connected model is expected to ask the user in chat and re-invoke
    with a fuller intent.

    Raises:
        MemoElicitationRejected: when the user declines or cancels an
            elicit prompt for a required field.
        MemoTemplateNotFound: when templates/memo.yaml is missing.
        MemoGenerationFailed: when the assembler raises after all fields
            are collected.
    """
    result = await _run_memo_pipeline(intent, source_md, ctx)
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
        text_summary = (
            f"⚠️ Need more info — missing: "
            f"{', '.join(result.get('missing_fields', []))}. "
            "Please provide values inline and call create_memo again."
        )
        return ToolResult(
            content=[TextContent(type="text", text=text_summary)],
            structured_content=result,
        )

    path_str = result["path"]
    path = Path(path_str)
    audit_id = result["audit_id"]
    file_uri = path.absolute().as_uri()
    text_summary = (
        f"✅ Memo ready — [Open {path.name}]({file_uri})\n"
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
                description=f"Generated memo (audit_id={audit_id})",
                size=path.stat().st_size if path.exists() else None,
            ),
        ],
        structured_content=result,
    )


__all__ = [
    "MEMO_REQUIRED_FIELDS",
    "MemoElicitationRejected",
    "MemoError",
    "MemoGenerationFailed",
    "MemoSpec",
    "MemoTemplate",
    "MemoTemplateNotFound",
    "create_memo",
    "server",
]
