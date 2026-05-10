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
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastmcp import Context, FastMCP
from fastmcp.server.elicitation import AcceptedElicitation

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
# Fallback for recipient-only: "to X" or "Recipient: X".
_RECIPIENT_RE = re.compile(
    r"\b(?:Recipient:|to)\s+([^,.;\n]+)",
    re.IGNORECASE,
)
# Subject patterns: "about X" / "regarding X" / "re: X".
_SUBJECT_RE = re.compile(r"\b(?:about|regarding|re:)\s+([^,.;]+)", re.IGNORECASE)


def _heuristic_extract(intent: str, source_md: str | None) -> MemoSpec:
    """Best-effort field extractor over free-text intent + optional source.

    Conservative — when uncertain, leaves the field as None so elicitation
    kicks in. False positives here silently produce wrong content; false
    negatives just mean an extra elicit call.
    """
    spec = MemoSpec()

    # Date
    m = _DATE_RE.search(intent)
    if m:
        spec.date = m.group(1).strip()

    # From / To — try joint, fall back to halves.
    m = _FROM_TO_RE.search(intent)
    if m:
        spec.sender = m.group(1).strip()
        spec.recipient = m.group(2).strip()
    else:
        m = _FROM_RE.search(intent)
        if m:
            spec.sender = m.group(1).strip()
        m = _RECIPIENT_RE.search(intent)
        if m:
            spec.recipient = m.group(1).strip()

    # Subject
    m = _SUBJECT_RE.search(intent)
    if m:
        spec.subject = m.group(1).strip().rstrip(".")

    # Body — if source_md is provided, use the markdown adapter's first
    # section's joined text as a starting body; otherwise leave None and
    # elicit. The body is the longest field, hardest to extract reliably.
    if source_md and source_md.strip():
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
            text = _substitute(str(entry.get("text", "")), spec)
            section.add_paragraph(Paragraph(text))
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


# --------------------------------------------------------------------------- #
# create_memo — the FastMCP tool
# --------------------------------------------------------------------------- #


server = FastMCP("MINT-Memo", instructions="Phase-13 MEMO-POC: planning-mode memo generator")


@server.tool
async def create_memo(
    intent: str,
    source_md: str | None = None,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Generate a klawd-themed Memo via planning dialog.

    1. Heuristically extract sender / recipient / date / subject / body
       from the free-text intent and optional source_md.
    2. For each missing required field, await ctx.elicit(...) — the
       connected MCP client renders a structured form to the user.
    3. Once all 5 fields present, assemble a Document via MP-DOCUMENT
       with the klawd preset and the templates/memo.yaml layout.
    4. Inject a GRACE audit-trail manifest with audit_id, timestamp,
       and the list of fields that required elicitation.
    5. Return {path, audit_id, fields_elicited}.

    Raises:
        MemoElicitationRejected: when the user declines or cancels an
            elicit prompt for a required field.
        MemoTemplateNotFound: when templates/memo.yaml is missing.
        MemoGenerationFailed: when the assembler raises after all fields
            are collected.
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
    for field_name in MEMO_REQUIRED_FIELDS:
        if getattr(spec, field_name):
            continue
        prompt = _elicit_prompt(field_name)
        result = await ctx.elicit(
            message=prompt,
            response_type=str,  # type: ignore[arg-type]  # fastmcp overload picks str via response_type=str
            response_title=field_name,
        )
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

    # All required fields filled; load template and assemble.
    template = _load_template()
    output_dir = Path(tempfile.mkdtemp(prefix="mint_memo_"))
    output_path = output_dir / "memo.docx"

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
    audit_id = str(uuid.uuid4())
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
        "path": str(output_path),
        "audit_id": audit_id,
        "fields_elicited": fields_elicited,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


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
