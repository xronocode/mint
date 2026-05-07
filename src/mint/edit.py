# FILE: src/mint/edit.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Edit existing DOCX without regeneration. Accepts a typed EditPlan
#            (JSON list of typed ops) and applies it via deterministic XML
#            transforms over an M-OOXML unpack tree. The LLM never sees raw
#            OOXML — it only sees extracted plain text plus stable anchors.
#            Supports tracked changes (w:ins/w:del) and threaded comments
#            (commentsExtended.xml paraIdParent) per ECMA-376.
#   SCOPE: validate plan → backup → unpack → extract anchors → resolve →
#          execute typed handlers in order → pack → run M-VALIDATE → return
#          EditResult with per-op diff. PPTX edits are EDIT_OP_UNSUPPORTED in
#          v0 (oos-2). LLM call itself is OUT OF SCOPE for edit(); callers
#          construct an EditPlan then pass it in.
#   DEPENDS: M-OOXML, M-CONFIG, M-VALIDATE, M-LLM
#   LINKS: docs/knowledge-graph.xml#M-EDIT, docs/development-plan.xml#M-EDIT,
#          docs/verification-plan.xml#V-M-EDIT, docs/verification-plan.xml#VF-010
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   EditError                    - exception with `code` attribute
#   TextAnchor                   - frozen dataclass; output of extract_text_with_anchors
#   Anchor                       - frozen dataclass; tagged on `type` (paragraph_index|text|hash)
#   EditMetadata                 - frozen dataclass; correlation_id, source_prompt_hash, model
#   EditOp                       - frozen dataclass; tagged on `type`, carries op_id+anchor+payload
#   EditPlan                     - frozen dataclass; format, ops, metadata
#   OpOutcome                    - frozen dataclass; per-op diff entry
#   EditResult                   - frozen dataclass; final return value of edit()
#   SUPPORTED_OP_TYPES           - frozenset of op type literals
#   STANDARD_STYLE_IDS           - styles always allowed without styles.xml lookup
#   edit                         - public: full pipeline entry point
#   validate_plan                - public: static plan validation
#   extract_text_with_anchors    - public: emit TextAnchor list from unpack tree
#   build_edit_prompt            - public: render LLM-facing prompt (no OOXML)
#   resolve_anchor               - public: map Anchor → live element
#   render_diff                  - public: produce list[OpOutcome] for the result
#   _paragraph_hash              - module-level helper (monkeypatched in tests)
#   _normalize_paragraph_text    - whitespace-collapse + NFKC normalization
#   _compute_source_prompt_hash  - SHA256 of instruction + anchor digest
#   _handle_replace_text         - op-1 handler
#   _handle_insert_paragraph     - op-2 handler
#   _handle_delete_paragraph     - op-3 handler
#   _handle_set_paragraph_style  - op-4 handler
#   _handle_tracked_replace      - op-5 handler
#   _handle_tracked_delete       - op-6 handler
#   _handle_add_comment          - op-7 handler
#   _handle_accept_change        - op-8 handler
#   _handle_reject_change        - op-9 handler
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation (Phase-5 Wave-5-2)
# END_CHANGE_SUMMARY

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import logging
import shutil
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from lxml import etree

from mint import ooxml as m_ooxml
from mint import validate as m_validate
from mint.config import SeverityMode

logger = logging.getLogger("mint.edit")

# ---------------------------------------------------------------------------
# Namespaces (mirrors M-OOXML; kept local so this module does not reach into
# private names of ooxml.py).
# ---------------------------------------------------------------------------

W_NS: Final[str] = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS: Final[str] = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS: Final[str] = "http://schemas.microsoft.com/office/word/2012/wordml"
R_NS: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR_NS: Final[str] = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS: Final[str] = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS: Final[str] = "http://www.w3.org/XML/1998/namespace"

W: Final[str] = f"{{{W_NS}}}"
W14: Final[str] = f"{{{W14_NS}}}"
W15: Final[str] = f"{{{W15_NS}}}"
R: Final[str] = f"{{{R_NS}}}"
PR: Final[str] = f"{{{PR_NS}}}"
CT: Final[str] = f"{{{CT_NS}}}"
XML: Final[str] = f"{{{XML_NS}}}"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_OP_TYPES: Final[frozenset[str]] = frozenset(
    {
        "replace_text",
        "insert_paragraph",
        "delete_paragraph",
        "set_paragraph_style",
        "tracked_replace",
        "tracked_delete",
        "add_comment",
        "accept_change",
        "reject_change",
    }
)

# accept_change / reject_change route through the tracked-change observability
# block too (they manipulate w:ins / w:del subtrees).
TRACKED_OP_TYPES: Final[frozenset[str]] = frozenset(
    {"tracked_replace", "tracked_delete", "accept_change", "reject_change"}
)

REVISION_OP_TYPES: Final[frozenset[str]] = frozenset(
    {"tracked_replace", "tracked_delete", "add_comment"}
)

SUPPORTED_ANCHOR_TYPES: Final[frozenset[str]] = frozenset(
    {"paragraph_index", "text", "hash"}
)

SUPPORTED_PARTS: Final[frozenset[str]] = frozenset(
    {"document", "header", "footer", "footnote", "endnote"}
)

# Style ids that may be referenced even if not present in styles.xml.
STANDARD_STYLE_IDS: Final[frozenset[str]] = frozenset(
    {
        "Normal",
        "Heading1",
        "Heading2",
        "Heading3",
        "Heading4",
        "Heading5",
        "Heading6",
        "ListParagraph",
    }
)

ANCHOR_VALUE_MAX_LEN: Final[int] = 512
SNIPPET_MAX_LEN: Final[int] = 240


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EditError(Exception):
    """Document-edit-pipeline error.

    The ``code`` attribute is one of:
    ``EDIT_PLAN_INVALID``, ``EDIT_ANCHOR_NOT_FOUND``, ``EDIT_ANCHOR_AMBIGUOUS``,
    ``EDIT_OP_UNSUPPORTED``, ``EDIT_TRACKED_CHANGE_INVALID``,
    ``EDIT_VALIDATION_FAILED``, ``BACKUP_FAILED``.
    """

    code: str = "EDIT_UNKNOWN"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextAnchor:
    paragraph_index: int
    hash: str
    text: str
    part: Literal["document", "header", "footer", "footnote", "endnote"]


@dataclass(frozen=True)
class Anchor:
    type: Literal["paragraph_index", "text", "hash"]
    value: str | int
    context_before: str | None = None
    context_after: str | None = None
    part: Literal["document", "header", "footer", "footnote", "endnote"] = "document"


@dataclass(frozen=True)
class EditOp:
    """Tagged-union edit operation.

    The ``type`` discriminator selects the variant. Variant-specific fields
    live in ``payload`` to keep the dataclass shape uniform — JSON callers
    flatten payload onto the op dict; ``EditOp.from_dict`` re-folds it.
    """

    type: str
    op_id: str
    anchor: Anchor
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EditOp:
        if not isinstance(raw, dict):
            raise EditError(
                f"EditOp must be a dict, got {type(raw).__name__}",
                code="EDIT_PLAN_INVALID",
            )
        op_type = raw.get("type")
        op_id = raw.get("op_id")
        anchor_raw = raw.get("anchor")
        if not isinstance(op_type, str):
            raise EditError(
                "EditOp.type must be a string", code="EDIT_PLAN_INVALID"
            )
        if not isinstance(op_id, str) or not op_id:
            raise EditError(
                "EditOp.op_id must be a non-empty string",
                code="EDIT_PLAN_INVALID",
            )
        if not isinstance(anchor_raw, dict):
            raise EditError(
                f"EditOp[{op_id}].anchor must be a dict",
                code="EDIT_PLAN_INVALID",
            )
        anchor = _anchor_from_dict(anchor_raw)
        # Strip well-known top-level keys; everything else is the payload.
        payload = {
            k: v
            for k, v in raw.items()
            if k not in {"type", "op_id", "anchor"}
        }
        return cls(type=op_type, op_id=op_id, anchor=anchor, payload=payload)


def _anchor_from_dict(raw: dict[str, Any]) -> Anchor:
    a_type = raw.get("type")
    if a_type not in SUPPORTED_ANCHOR_TYPES:
        raise EditError(
            f"Anchor.type must be one of {sorted(SUPPORTED_ANCHOR_TYPES)}, "
            f"got {a_type!r}",
            code="EDIT_PLAN_INVALID",
        )
    value = raw.get("value")
    if value is None:
        raise EditError("Anchor.value missing", code="EDIT_PLAN_INVALID")
    part = raw.get("part", "document")
    if part not in SUPPORTED_PARTS:
        raise EditError(
            f"Anchor.part must be one of {sorted(SUPPORTED_PARTS)}, got {part!r}",
            code="EDIT_PLAN_INVALID",
        )
    return Anchor(
        type=a_type,
        value=value,
        context_before=raw.get("context_before"),
        context_after=raw.get("context_after"),
        part=part,
    )


@dataclass(frozen=True)
class EditMetadata:
    correlation_id: str
    source_prompt_hash: str
    model: str
    created_at: str


@dataclass(frozen=True)
class EditPlan:
    format: Literal["docx", "pptx"]
    ops: list[EditOp]
    metadata: EditMetadata


@dataclass(frozen=True)
class OpOutcome:
    op_id: str
    success: bool
    error_code: str | None
    affected_part: str
    before_snippet: str
    after_snippet: str


@dataclass(frozen=True)
class EditResult:
    output_path: Path | None
    backup_path: Path
    success: bool
    ops_total: int
    ops_succeeded: int
    ops_failed: int
    validation_report: Any
    diff: list[OpOutcome]
    duration_ms: int
    error: str | None


# ---------------------------------------------------------------------------
# Hashing helpers (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------


def _normalize_paragraph_text(p: etree._Element) -> str:
    """Whitespace-collapse + NFKC normalization for hashing/identity."""
    text_parts: list[str] = []
    for t in p.iter(f"{W}t"):
        if t.text:
            text_parts.append(t.text)
    raw = "".join(text_parts)
    nfkc = unicodedata.normalize("NFKC", raw)
    # Whitespace collapse: any run of whitespace → single space; trim.
    collapsed = " ".join(nfkc.split())
    return collapsed


def _paragraph_hash(p: etree._Element) -> str:
    """8-hex SHA1 prefix of the normalized paragraph text.

    Tests monkeypatch this for the hash-collision scenario (V-M-EDIT
    scenario-24). Keep it as a simple module-level function so monkeypatch
    works without per-call indirection.
    """
    text = _normalize_paragraph_text(p)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _paragraph_visible_text(p: etree._Element) -> str:
    """Return the visible plain-text content of a paragraph (no normalization).

    Used for build_edit_prompt and for OpOutcome snippets.
    """
    parts: list[str] = []
    for t in p.iter(f"{W}t"):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _compute_source_prompt_hash(
    user_instruction: str, anchors: list[TextAnchor]
) -> str:
    """SHA256 of user instruction + serialized anchor digest."""
    digest = {
        "instruction": user_instruction,
        "anchors": [
            {
                "i": a.paragraph_index,
                "h": a.hash,
                "p": a.part,
                # Include text length only — content stays out of the digest
                # so the hash is stable across debug logging redaction.
                "n": len(a.text),
            }
            for a in anchors
        ],
    }
    serialized = json.dumps(digest, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API: validate_plan
# ---------------------------------------------------------------------------


# START_CONTRACT: validate_plan
#   PURPOSE: Static check on EditPlan: ops non-empty, op_ids unique, types
#            supported, anchor.value bounded, revision ops carry author/date.
#   INPUTS: { plan: EditPlan }
#   OUTPUTS: None — raises EditError(code='EDIT_PLAN_INVALID') on failure.
#   SIDE_EFFECTS: emits BLOCK_EDIT_PLAN_VALIDATE log markers.
#   LINKS: V-M-EDIT scenario-1..3, scenario-23, scenario-27, VF-010 inv-1, inv-2
# END_CONTRACT: validate_plan
def validate_plan(plan: EditPlan) -> None:
    # START_BLOCK_EDIT_PLAN_VALIDATE
    logger.info(
        "[Edit][validate_plan][BLOCK_EDIT_PLAN_VALIDATE] start ops=%d format=%s",
        len(plan.ops),
        plan.format,
    )

    if plan.format == "pptx":
        # oos-2: PPTX edit support is deferred to v0.5.
        raise EditError(
            "PPTX edit support is out of scope in v0",
            code="EDIT_OP_UNSUPPORTED",
        )
    if plan.format != "docx":
        raise EditError(
            f"Unsupported plan.format: {plan.format!r}",
            code="EDIT_PLAN_INVALID",
        )

    if not plan.ops:
        raise EditError(
            "EditPlan.ops must contain at least one op",
            code="EDIT_PLAN_INVALID",
        )

    seen_ids: set[str] = set()
    for op in plan.ops:
        if op.type not in SUPPORTED_OP_TYPES:
            raise EditError(
                f"Unsupported op type: {op.type!r} (op_id={op.op_id})",
                code="EDIT_OP_UNSUPPORTED",
            )
        if not op.op_id:
            raise EditError(
                "Every op must carry a non-empty op_id",
                code="EDIT_PLAN_INVALID",
            )
        if op.op_id in seen_ids:
            raise EditError(
                f"Duplicate op_id: {op.op_id}", code="EDIT_PLAN_INVALID"
            )
        seen_ids.add(op.op_id)

        # Anchor checks.
        anchor = op.anchor
        if anchor.type not in SUPPORTED_ANCHOR_TYPES:
            raise EditError(
                f"Anchor.type {anchor.type!r} unsupported (op_id={op.op_id})",
                code="EDIT_PLAN_INVALID",
            )
        _validate_anchor_value(anchor, op.op_id)

        # Revision-op checks.
        if op.type in REVISION_OP_TYPES:
            author = op.payload.get("author")
            date = op.payload.get("date")
            if author is not None and not isinstance(author, str):
                raise EditError(
                    f"op_id={op.op_id} payload.author must be str",
                    code="EDIT_PLAN_INVALID",
                )
            if date is not None and not isinstance(date, str):
                raise EditError(
                    f"op_id={op.op_id} payload.date must be str",
                    code="EDIT_PLAN_INVALID",
                )
            # author/date may be omitted — edit() injects defaults from the
            # caller-supplied author argument plus current UTC time. This is
            # documented behavior so the LLM does not need to invent them.

    logger.info(
        "[Edit][validate_plan][BLOCK_EDIT_PLAN_VALIDATE] done ops=%d",
        len(plan.ops),
    )
    # END_BLOCK_EDIT_PLAN_VALIDATE


def _validate_anchor_value(anchor: Anchor, op_id: str) -> None:
    value = anchor.value
    if anchor.type == "paragraph_index":
        if not isinstance(value, int):
            raise EditError(
                f"op_id={op_id} anchor.value for paragraph_index must be int",
                code="EDIT_PLAN_INVALID",
            )
        if value < 0:
            raise EditError(
                f"op_id={op_id} anchor.value must be >= 0",
                code="EDIT_PLAN_INVALID",
            )
        return

    if not isinstance(value, str):
        raise EditError(
            f"op_id={op_id} anchor.value for type={anchor.type} must be str",
            code="EDIT_PLAN_INVALID",
        )
    if not value:
        raise EditError(
            f"op_id={op_id} anchor.value must be non-empty",
            code="EDIT_PLAN_INVALID",
        )
    if len(value) > ANCHOR_VALUE_MAX_LEN:
        raise EditError(
            f"op_id={op_id} anchor.value length {len(value)} exceeds "
            f"{ANCHOR_VALUE_MAX_LEN}",
            code="EDIT_PLAN_INVALID",
        )
    # ASCII control characters are forbidden.
    for ch in value:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise EditError(
                f"op_id={op_id} anchor.value contains ASCII control char",
                code="EDIT_PLAN_INVALID",
            )

    if anchor.type == "hash":
        if len(value) != 8:
            raise EditError(
                f"op_id={op_id} anchor.value for type=hash must be 8 hex chars",
                code="EDIT_PLAN_INVALID",
            )
        try:
            int(value, 16)
        except ValueError as exc:
            raise EditError(
                f"op_id={op_id} anchor.value for type=hash must be hex",
                code="EDIT_PLAN_INVALID",
            ) from exc


# ---------------------------------------------------------------------------
# Public API: extract_text_with_anchors
# ---------------------------------------------------------------------------


# Mapping from the "part" enum value to (relative XML path, optional).
# Only document.xml is required; the others are scanned only if they exist.
_PART_PATHS: dict[str, list[str]] = {
    "document": ["word/document.xml"],
    "header": [
        "word/header1.xml",
        "word/header2.xml",
        "word/header3.xml",
    ],
    "footer": [
        "word/footer1.xml",
        "word/footer2.xml",
        "word/footer3.xml",
    ],
    "footnote": ["word/footnotes.xml"],
    "endnote": ["word/endnotes.xml"],
}


# START_CONTRACT: extract_text_with_anchors
#   PURPOSE: Walk an unpacked DOCX tree and emit a stable list of TextAnchor
#            records: paragraph_index (int, original-tree order), hash (8-hex
#            SHA1 prefix of NFKC-normalized whitespace-collapsed text),
#            visible text, part. Used by build_edit_prompt and resolve_anchor.
#   INPUTS: { unpack_dir: Path }
#   OUTPUTS: { list[TextAnchor] }
#   SIDE_EFFECTS: emits BLOCK_EDIT_EXTRACT_TEXT log markers.
#   LINKS: V-M-EDIT scenario-19, scenario-21, scenario-29
# END_CONTRACT: extract_text_with_anchors
def extract_text_with_anchors(unpack_dir: Path) -> list[TextAnchor]:
    # START_BLOCK_EDIT_EXTRACT_TEXT
    logger.info(
        "[Edit][extract_text][BLOCK_EDIT_EXTRACT_TEXT] start unpack_dir=%s",
        unpack_dir,
    )
    anchors: list[TextAnchor] = []
    counter = 0
    for part_name, candidates in _PART_PATHS.items():
        for rel in candidates:
            full = unpack_dir / rel
            if not full.exists():
                continue
            try:
                tree = etree.parse(str(full)).getroot()
            except etree.XMLSyntaxError:  # pragma: no cover - defensive
                continue
            for p in tree.iter(f"{W}p"):
                anchors.append(
                    TextAnchor(
                        paragraph_index=counter,
                        hash=_paragraph_hash(p),
                        text=_paragraph_visible_text(p),
                        part=part_name,  # type: ignore[arg-type]
                    )
                )
                counter += 1
    logger.info(
        "[Edit][extract_text][BLOCK_EDIT_EXTRACT_TEXT] done anchors=%d",
        len(anchors),
    )
    logger.debug(
        "[Edit][extract_text] full_anchor_list=%s",
        [(a.paragraph_index, a.hash, a.part) for a in anchors],
    )
    # END_BLOCK_EDIT_EXTRACT_TEXT
    return anchors


# ---------------------------------------------------------------------------
# Public API: build_edit_prompt
# ---------------------------------------------------------------------------

_OP_SCHEMA_DESCRIPTION: Final[str] = """
Available operation types (the JSON discriminator field is named "type"):
  - replace_text       : payload {old_text, new_text}
  - insert_paragraph   : payload {text, style_id} — style_id from the standard
                         set Heading1..Heading6, Normal, ListParagraph, or
                         present in styles.xml
  - delete_paragraph   : payload {}
  - set_paragraph_style: payload {style_id}
  - tracked_replace    : payload {old_text, new_text, author?, date?}
  - tracked_delete     : payload {}
  - add_comment        : payload {text, author?, parent_id?}
  - accept_change      : payload {change_id}
  - reject_change      : payload {change_id}

Anchor format: each line "#<paragraph_index>:<hash>" precedes the visible
plain text of one paragraph. To target a paragraph, set:
  anchor = {type: "paragraph_index", value: <int>}
or
  anchor = {type: "hash", value: "<8 hex chars>"}
or
  anchor = {type: "text", value: "<verbatim substring>",
            context_before: "...", context_after: "..."}
""".strip()


# START_CONTRACT: build_edit_prompt
#   PURPOSE: Build an LLM prompt from a user instruction + extracted anchor
#            list + a fixed op-type schema. Anchors are rendered as
#            "#<paragraph_index>:<hash>" so the model emits anchor.value in
#            that exact format. NEVER includes raw OOXML XML — VF-010.inv-8
#            and VF-010.forbidden-1.
#   INPUTS: { user_instruction: str, anchors: list[TextAnchor] }
#   OUTPUTS: { str }
#   SIDE_EFFECTS: none.
#   LINKS: V-M-EDIT scenario-13, scenario-29, VF-010.forbidden-1, Wave-5-2
#          evidence-1
# END_CONTRACT: build_edit_prompt
def build_edit_prompt(user_instruction: str, anchors: list[TextAnchor]) -> str:
    lines: list[str] = []
    lines.append("USER INSTRUCTION:")
    lines.append(user_instruction)
    lines.append("")
    lines.append("DOCUMENT ANCHORS (one per paragraph):")
    for a in anchors:
        token = f"#{a.paragraph_index}:{a.hash}"
        # Render visible text on the same line; redact internal XML
        # angle brackets if any user content somehow contains them so the
        # forbidden-tag regex stays clean.
        safe_text = a.text.replace("<", "[lt]").replace(">", "[gt]")
        lines.append(f"{token} [{a.part}] {safe_text}")
    lines.append("")
    lines.append(_OP_SCHEMA_DESCRIPTION)
    lines.append("")
    lines.append(
        "Return a JSON EditPlan: "
        '{"format":"docx","ops":[<EditOp>...],'
        '"metadata":{...optional, computed by MINT...}}'
    )
    rendered = "\n".join(lines)
    logger.debug(
        "[Edit][build_edit_prompt] length=%d anchors=%d",
        len(rendered),
        len(anchors),
    )
    return rendered


# ---------------------------------------------------------------------------
# Public API: resolve_anchor
# ---------------------------------------------------------------------------


# START_CONTRACT: resolve_anchor
#   PURPOSE: Map an Anchor to a paragraph element in the LIVE tree by re-locating
#            an identity captured at unpack time. Multiple matches surviving
#            without disambiguation → EDIT_ANCHOR_AMBIGUOUS. Element no longer
#            attached → EDIT_ANCHOR_NOT_FOUND.
#   INPUTS: { anchor: Anchor, unpack_dir: Path,
#             original_anchors: list[TextAnchor] }
#   OUTPUTS: { lxml.etree._Element — the paragraph in the LIVE tree }
#   SIDE_EFFECTS: emits BLOCK_EDIT_RESOLVE_ANCHOR log markers.
#   LINKS: V-M-EDIT scenario-5, scenario-6, scenario-19, scenario-21,
#          scenario-24
# END_CONTRACT: resolve_anchor
def resolve_anchor(
    anchor: Anchor,
    unpack_dir: Path,
    original_anchors: list[TextAnchor],
) -> etree._Element:
    return _resolve_anchor_with_state(anchor, unpack_dir, original_anchors)[0]


def _resolve_anchor_with_state(
    anchor: Anchor,
    unpack_dir: Path,
    original_anchors: list[TextAnchor],
) -> tuple[etree._Element, str]:
    """Resolve and also return the part name of the live tree."""
    # Find the original TextAnchor(s) for this Anchor.
    candidates_by_part: dict[str, list[TextAnchor]] = {}
    if anchor.type == "paragraph_index":
        if not isinstance(anchor.value, int):
            raise EditError(
                "Anchor.value must be int for type=paragraph_index",
                code="EDIT_PLAN_INVALID",
            )
        match = next(
            (
                a
                for a in original_anchors
                if a.paragraph_index == anchor.value and a.part == anchor.part
            ),
            None,
        )
        if match is None:
            raise EditError(
                f"Anchor paragraph_index={anchor.value} part={anchor.part} "
                "not present in original tree",
                code="EDIT_ANCHOR_NOT_FOUND",
            )
        candidates_by_part.setdefault(match.part, []).append(match)

    elif anchor.type == "hash":
        if not isinstance(anchor.value, str):
            raise EditError(
                "Anchor.value must be str for type=hash",
                code="EDIT_PLAN_INVALID",
            )
        matches = [
            a
            for a in original_anchors
            if a.hash == anchor.value and a.part == anchor.part
        ]
        if len(matches) == 0:
            raise EditError(
                f"No paragraph with hash={anchor.value} in part={anchor.part}",
                code="EDIT_ANCHOR_NOT_FOUND",
            )
        if len(matches) > 1:
            indices = [m.paragraph_index for m in matches]
            raise EditError(
                f"Ambiguous hash anchor {anchor.value!r} in part="
                f"{anchor.part}: {len(matches)} paragraphs match "
                f"(paragraph_index values: {indices})",
                code="EDIT_ANCHOR_AMBIGUOUS",
            )
        candidates_by_part.setdefault(matches[0].part, []).extend(matches)

    elif anchor.type == "text":
        if not isinstance(anchor.value, str):
            raise EditError(
                "Anchor.value must be str for type=text",
                code="EDIT_PLAN_INVALID",
            )
        substring = anchor.value
        matches = [
            a
            for a in original_anchors
            if substring in a.text and a.part == anchor.part
        ]
        # Disambiguate using context_before / context_after if multiple match.
        if len(matches) > 1 and (
            anchor.context_before or anchor.context_after
        ):
            disambiguated: list[TextAnchor] = []
            for a in matches:
                idx = a.text.find(substring)
                ok = True
                if anchor.context_before is not None:
                    before = a.text[:idx]
                    if not before.endswith(anchor.context_before):
                        ok = False
                if ok and anchor.context_after is not None:
                    after = a.text[idx + len(substring) :]
                    if not after.startswith(anchor.context_after):
                        ok = False
                if ok:
                    disambiguated.append(a)
            matches = disambiguated
        if len(matches) == 0:
            raise EditError(
                f"No paragraph contains text {anchor.value!r} in part="
                f"{anchor.part}",
                code="EDIT_ANCHOR_NOT_FOUND",
            )
        if len(matches) > 1:
            indices = [m.paragraph_index for m in matches]
            raise EditError(
                f"Ambiguous text anchor {anchor.value!r} in part="
                f"{anchor.part}: matches paragraph_index {indices}",
                code="EDIT_ANCHOR_AMBIGUOUS",
            )
        candidates_by_part.setdefault(matches[0].part, []).extend(matches)
    else:
        raise EditError(
            f"Anchor.type {anchor.type!r} unsupported",
            code="EDIT_PLAN_INVALID",
        )

    # Now locate the matched paragraph in the LIVE tree.
    for part_name, ta_list in candidates_by_part.items():
        ta = ta_list[0]
        live_p = _find_live_paragraph_by_identity(unpack_dir, part_name, ta)
        if live_p is None:
            raise EditError(
                f"Anchor for paragraph_index={ta.paragraph_index} no longer "
                "resolves in live tree (deleted or detached by earlier op)",
                code="EDIT_ANCHOR_NOT_FOUND",
            )
        logger.info(
            "[Edit][resolve_anchor][BLOCK_EDIT_RESOLVE_ANCHOR] "
            "part=%s paragraph_index=%d hash=%s anchor_type=%s",
            part_name,
            ta.paragraph_index,
            ta.hash,
            anchor.type,
        )
        return live_p, part_name

    # Unreachable: all branches above either raise or populate candidates.
    raise EditError(  # pragma: no cover
        "resolve_anchor: no candidate part", code="EDIT_ANCHOR_NOT_FOUND"
    )


def _find_live_paragraph_by_identity(
    unpack_dir: Path, part_name: str, ta: TextAnchor
) -> etree._Element | None:
    """Find the paragraph in the live tree that matches the original TextAnchor.

    Identity = the (paragraph_index, hash) pair captured at unpack time. After
    earlier ops mutate the live tree, the paragraph may have shifted index but
    its content (and therefore its hash) is unchanged. We resolve by:

      1. Scanning the LIVE tree for paragraphs whose normalized-text hash
         equals ta.hash. If exactly one matches, return it. If multiple match
         (e.g. duplicated content), use original within-part position to
         disambiguate by picking the one whose order matches.
      2. If none match, the original paragraph was deleted by an earlier op
         in this same plan → return None (caller raises EDIT_ANCHOR_NOT_FOUND).

    A paragraph that has been mutated by replace_text in the SAME plan still
    counts as resolved when the lookup happens against the original anchor —
    but in our pipeline, we resolve every op's anchor against the
    `original_anchors` list, so the hash we look up here is always the
    pre-edit hash. After an in-place text edit, the live tree's hash would
    differ — so we also accept a position-based match if the within-part
    position is preserved AND the paragraph still exists.
    """
    candidates = _PART_PATHS.get(part_name, [])
    for rel in candidates:
        full = unpack_dir / rel
        if not full.exists():
            continue
        tree = etree.parse(str(full)).getroot()
        live_paragraphs = list(tree.iter(f"{W}p"))

        # First: hash-based identity match. This is the strong identity.
        hash_matches: list[etree._Element] = [
            p for p in live_paragraphs if _paragraph_hash(p) == ta.hash
        ]
        if len(hash_matches) == 1:
            result: etree._Element = hash_matches[0]
            return result
        if len(hash_matches) > 1:
            # Multiple paragraphs share the original hash. Disambiguate by
            # within-part order: the original anchor was at ta.paragraph_index;
            # pick the live paragraph whose live within-part position is closest
            # to that original index.
            best: etree._Element = min(
                hash_matches,
                key=lambda p: abs(live_paragraphs.index(p) - ta.paragraph_index),
            )
            return best
    return None


# ---------------------------------------------------------------------------
# Public API: edit
# ---------------------------------------------------------------------------


# START_CONTRACT: edit
#   PURPOSE: Full pipeline: validate plan → backup → unpack → extract anchors
#            → resolve and apply each op against the original-tree anchor map
#            → pack → run M-VALIDATE → return EditResult.
#   INPUTS: { document_path: Path, edit_plan: EditPlan,
#             author: str = 'MINT', output_path: Optional[Path],
#             severity_mode: Optional[SeverityMode] }
#   OUTPUTS: { EditResult }
#   SIDE_EFFECTS: writes backup; writes output; may write tmpdir; reads input.
#   LINKS: DF-007, V-M-EDIT scenario-4, scenario-15, scenario-22, scenario-25,
#          scenario-26, scenario-28
# END_CONTRACT: edit
def edit(
    document_path: Path,
    edit_plan: EditPlan,
    author: str = "MINT",
    output_path: Path | None = None,
    severity_mode: SeverityMode | None = None,
) -> EditResult:
    document_path = Path(document_path)
    started = time.monotonic()

    # 1. validate_plan — also rejects PPTX up front (oos-2).
    validate_plan(edit_plan)

    # 2. resolve output_path / backup_path before any IO mutation.
    if output_path is None:
        output_path = document_path.with_suffix(
            ".edited" + document_path.suffix
        )
    output_path = Path(output_path)
    backup_path = Path(str(document_path) + ".bak")
    if output_path == backup_path:
        raise EditError(
            "output_path and backup_path must differ",
            code="EDIT_PLAN_INVALID",
        )

    # 3. backup BEFORE any tree mutation (pol-2). If the backup write fails,
    # raise BACKUP_FAILED before unpack.
    # START_BLOCK_EDIT_BACKUP
    logger.info(
        "[Edit][backup][BLOCK_EDIT_BACKUP] start src=%s dst=%s",
        document_path.name,
        backup_path.name,
    )
    try:
        if not document_path.exists():
            raise EditError(
                f"Input document not found: {document_path}",
                code="BACKUP_FAILED",
            )
        shutil.copy2(document_path, backup_path)
    except EditError:
        raise
    except OSError as exc:
        raise EditError(
            f"Failed to create backup at {backup_path}: {exc}",
            code="BACKUP_FAILED",
        ) from exc
    logger.info(
        "[Edit][backup][BLOCK_EDIT_BACKUP] done backup=%s bytes=%d",
        backup_path.name,
        backup_path.stat().st_size,
    )
    # END_BLOCK_EDIT_BACKUP

    diff: list[OpOutcome] = []
    ops_succeeded = 0
    ops_failed = 0

    with tempfile.TemporaryDirectory(prefix="mint_edit_") as tmp:
        unpack_dir = Path(tmp) / "unpack"

        # 4. unpack via M-OOXML — emits BLOCK_OOXML_UNPACK.
        m_ooxml.unpack(document_path, unpack_dir)

        # 5. snapshot anchors against the ORIGINAL tree.
        original_anchors = extract_text_with_anchors(unpack_dir)

        # 6. iterate ops in order against ORIGINAL anchors.
        # State that ops may need:
        next_change_id = _next_revision_id(unpack_dir)
        comments_state = _CommentsState.load(unpack_dir)

        for op in edit_plan.ops:
            try:
                outcome = _execute_op(
                    op,
                    unpack_dir,
                    original_anchors,
                    author,
                    next_change_id_holder=[next_change_id],
                    comments_state=comments_state,
                )
                # update next_change_id from holder
                # (handlers mutate the holder so successive ops get fresh ids).
            except EditError as exc:
                outcome = OpOutcome(
                    op_id=op.op_id,
                    success=False,
                    error_code=exc.code,
                    affected_part=op.anchor.part,
                    before_snippet="",
                    after_snippet="",
                )
                diff.append(outcome)
                ops_failed += 1
                # On any op failure, abort the pipeline (per DF-007 step-4).
                # Backup remains, but no output is written.
                duration_ms = int((time.monotonic() - started) * 1000)
                return EditResult(
                    output_path=None,
                    backup_path=backup_path,
                    success=False,
                    ops_total=len(edit_plan.ops),
                    ops_succeeded=ops_succeeded,
                    ops_failed=ops_failed,
                    validation_report=None,
                    diff=diff,
                    duration_ms=duration_ms,
                    error=str(exc),
                )
            diff.append(outcome)
            if outcome.success:
                ops_succeeded += 1
            else:
                ops_failed += 1

        # 7. flush comments_state changes to disk (if any).
        comments_state.flush(unpack_dir)

        # 8. fail-fast on broken rels.
        m_ooxml.validate_relationships(unpack_dir)

        # 9. pack — emits BLOCK_OOXML_PACK (and optionally BLOCK_OOXML_AUTOREPAIR).
        m_ooxml.pack(unpack_dir, output_path)

    # 10. validate the output via M-VALIDATE — emits BLOCK_RUN_CHECKS.
    effective_mode = severity_mode if severity_mode is not None else SeverityMode.AUDIT
    validation_report = m_validate.run_checks(output_path, effective_mode)

    # 11. strict mode rejects on validation regression.
    if effective_mode == SeverityMode.STRICT and not validation_report.passed:
        # Refuse to write output_path: delete the packed output.
        with contextlib.suppress(OSError):
            output_path.unlink(missing_ok=True)
        duration_ms = int((time.monotonic() - started) * 1000)
        raise EditError(
            f"Output failed M-VALIDATE in strict mode: "
            f"hard={validation_report.hard_count} "
            f"soft={validation_report.soft_count}",
            code="EDIT_VALIDATION_FAILED",
        )

    duration_ms = int((time.monotonic() - started) * 1000)

    return EditResult(
        output_path=output_path,
        backup_path=backup_path,
        success=ops_failed == 0,
        ops_total=len(edit_plan.ops),
        ops_succeeded=ops_succeeded,
        ops_failed=ops_failed,
        validation_report=validation_report,
        diff=diff,
        duration_ms=duration_ms,
        error=None,
    )


# ---------------------------------------------------------------------------
# Op dispatch
# ---------------------------------------------------------------------------


def _execute_op(
    op: EditOp,
    unpack_dir: Path,
    original_anchors: list[TextAnchor],
    default_author: str,
    next_change_id_holder: list[int],
    comments_state: _CommentsState,
) -> OpOutcome:
    """Resolve op's anchor and dispatch to the variant handler. Returns OpOutcome
    on success; raises EditError on failure (caller turns it into a failed
    OpOutcome).
    """
    if op.type not in SUPPORTED_OP_TYPES:
        raise EditError(
            f"op_id={op.op_id} type={op.type!r} unsupported",
            code="EDIT_OP_UNSUPPORTED",
        )

    live_p, part_name = _resolve_anchor_with_state(
        op.anchor, unpack_dir, original_anchors
    )
    before_snippet = _truncate_snippet(_paragraph_visible_text(live_p))

    # Date defaults to now-UTC if not in payload.
    op_author = op.payload.get("author", default_author)
    op_date = op.payload.get("date") or _now_iso_utc()

    # Dispatch.
    if op.type == "replace_text":
        _handle_replace_text(op, live_p)
    elif op.type == "insert_paragraph":
        _handle_insert_paragraph(op, live_p, unpack_dir)
    elif op.type == "delete_paragraph":
        _handle_delete_paragraph(op, live_p, unpack_dir)
    elif op.type == "set_paragraph_style":
        _handle_set_paragraph_style(op, live_p, unpack_dir)
    elif op.type == "tracked_replace":
        _handle_tracked_replace(
            op, live_p, op_author, op_date, next_change_id_holder
        )
    elif op.type == "tracked_delete":
        _handle_tracked_delete(
            op, live_p, op_author, op_date, next_change_id_holder
        )
    elif op.type == "add_comment":
        _handle_add_comment(
            op, live_p, op_author, op_date, comments_state
        )
    elif op.type == "accept_change":
        _handle_accept_change(op, live_p)
    elif op.type == "reject_change":
        _handle_reject_change(op, live_p)
    else:  # pragma: no cover - validated above
        raise EditError(
            f"unknown op type: {op.type}", code="EDIT_OP_UNSUPPORTED"
        )

    after_snippet = _truncate_snippet(_paragraph_visible_text(live_p))

    # Persist the live tree back to disk for whichever part holds live_p.
    _flush_part_tree(unpack_dir, part_name, live_p)

    logger.info(
        "[Edit][apply_op][BLOCK_EDIT_APPLY_OP] op_id=%s op_type=%s "
        "part=%s outcome=success",
        op.op_id,
        op.type,
        part_name,
    )
    logger.debug(
        "[Edit][apply_op] op_id=%s before_len=%d after_len=%d",
        op.op_id,
        len(before_snippet),
        len(after_snippet),
    )

    if op.type in TRACKED_OP_TYPES:
        logger.info(
            "[Edit][tracked_change][BLOCK_EDIT_TRACKED_CHANGE] op_id=%s "
            "op_type=%s target_part=%s",
            op.op_id,
            op.type,
            part_name,
        )

    return OpOutcome(
        op_id=op.op_id,
        success=True,
        error_code=None,
        affected_part=part_name,
        before_snippet=before_snippet,
        after_snippet=after_snippet,
    )


def _flush_part_tree(
    unpack_dir: Path, part_name: str, any_node: etree._Element
) -> None:
    """Serialize the document tree containing ``any_node`` back to disk."""
    root = any_node.getroottree().getroot()
    rel_path = _part_relpath_for_root(part_name, root)
    if rel_path is None:
        return
    full = unpack_dir / rel_path
    full.write_bytes(
        etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
    )


def _part_relpath_for_root(part_name: str, root: etree._Element) -> str | None:
    """Find the on-disk relative path for the live tree's root."""
    candidates = _PART_PATHS.get(part_name, [])
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_replace_text(op: EditOp, p: etree._Element) -> None:
    old_text = op.payload.get("old_text", "")
    new_text = op.payload.get("new_text", "")
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        raise EditError(
            f"op_id={op.op_id} replace_text needs old_text/new_text strings",
            code="EDIT_PLAN_INVALID",
        )
    # Find a w:t whose text contains old_text (in document order).
    for t in p.iter(f"{W}t"):
        if t.text and old_text in t.text:
            t.text = t.text.replace(old_text, new_text, 1)
            # Ensure xml:space=preserve if leading/trailing whitespace.
            if t.text != t.text.strip():
                t.set(f"{XML}space", "preserve")
            return
    raise EditError(
        f"op_id={op.op_id} replace_text: substring {old_text!r} not found",
        code="EDIT_ANCHOR_NOT_FOUND",
    )


def _handle_insert_paragraph(
    op: EditOp, p: etree._Element, unpack_dir: Path
) -> None:
    text = op.payload.get("text", "")
    style_id = op.payload.get("style_id", "Normal")
    if not isinstance(text, str) or not isinstance(style_id, str):
        raise EditError(
            f"op_id={op.op_id} insert_paragraph: text/style_id must be strings",
            code="EDIT_PLAN_INVALID",
        )
    if not _style_is_known(unpack_dir, style_id):
        raise EditError(
            f"op_id={op.op_id} style_id={style_id!r} not in styles.xml or "
            f"standard set",
            code="EDIT_OP_UNSUPPORTED",
        )
    new_p = _build_paragraph(text, style_id)
    parent = p.getparent()
    if parent is None:
        raise EditError(
            f"op_id={op.op_id} anchored paragraph has no parent",
            code="EDIT_PLAN_INVALID",
        )
    parent.insert(list(parent).index(p) + 1, new_p)


def _handle_delete_paragraph(
    op: EditOp, p: etree._Element, unpack_dir: Path
) -> None:
    parent = p.getparent()
    if parent is None:
        raise EditError(
            f"op_id={op.op_id} cannot delete root-level element",
            code="EDIT_PLAN_INVALID",
        )
    # Collect rIds referenced from this paragraph.
    rids = _collect_rids(p)
    parent.remove(p)
    # Drop the rels that are no longer referenced from anywhere in document.xml.
    if rids:
        _prune_unused_rels(unpack_dir, rids)


def _handle_set_paragraph_style(
    op: EditOp, p: etree._Element, unpack_dir: Path
) -> None:
    style_id = op.payload.get("style_id")
    if not isinstance(style_id, str) or not style_id:
        raise EditError(
            f"op_id={op.op_id} set_paragraph_style needs style_id",
            code="EDIT_PLAN_INVALID",
        )
    if not _style_is_known(unpack_dir, style_id):
        raise EditError(
            f"op_id={op.op_id} style_id={style_id!r} unknown",
            code="EDIT_OP_UNSUPPORTED",
        )
    ppr = p.find(f"{W}pPr")
    if ppr is None:
        ppr = etree.SubElement(p, f"{W}pPr")
        # pPr must be the first child of w:p.
        p.remove(ppr)
        p.insert(0, ppr)
    pstyle = ppr.find(f"{W}pStyle")
    if pstyle is None:
        pstyle = etree.SubElement(ppr, f"{W}pStyle")
    pstyle.set(f"{W}val", style_id)


def _handle_tracked_replace(
    op: EditOp,
    p: etree._Element,
    author: str,
    date: str,
    holder: list[int],
) -> None:
    old_text = op.payload.get("old_text", "")
    new_text = op.payload.get("new_text", "")
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        raise EditError(
            f"op_id={op.op_id} tracked_replace needs old_text/new_text",
            code="EDIT_PLAN_INVALID",
        )
    # Locate w:r whose w:t contains old_text.
    target_run: etree._Element | None = None
    target_t: etree._Element | None = None
    for r in p.iter(f"{W}r"):
        for t in r.findall(f"{W}t"):
            if t.text and old_text in t.text:
                target_run = r
                target_t = t
                break
        if target_run is not None:
            break
    if target_run is None or target_t is None:
        raise EditError(
            f"op_id={op.op_id} tracked_replace: text {old_text!r} not found",
            code="EDIT_ANCHOR_NOT_FOUND",
        )
    rpr = target_run.find(f"{W}rPr")
    parent = target_run.getparent()
    if parent is None:
        raise EditError(
            f"op_id={op.op_id} target run has no parent",
            code="EDIT_TRACKED_CHANGE_INVALID",
        )
    # Replace the target run with: (preceding text run) + w:del + w:ins +
    # (following text run). We keep things simple by splitting only on the
    # exact old_text occurrence inside target_t.text.
    full = target_t.text or ""
    idx = full.find(old_text)
    before = full[:idx]
    after = full[idx + len(old_text) :]

    pos = list(parent).index(target_run)
    parent.remove(target_run)

    insert_at = pos
    if before:
        run_before = _make_run(before, rpr)
        parent.insert(insert_at, run_before)
        insert_at += 1

    del_id = _next_id(holder)
    ins_id = _next_id(holder)
    del_el = etree.SubElement(parent, f"{W}del")
    del_el.set(f"{W}id", str(del_id))
    del_el.set(f"{W}author", author)
    del_el.set(f"{W}date", date)
    parent.remove(del_el)  # detach (SubElement attached); reinsert at index
    del_inner_run = _make_run(old_text, rpr, as_del=True)
    del_el.append(del_inner_run)
    parent.insert(insert_at, del_el)
    insert_at += 1

    ins_el = etree.SubElement(parent, f"{W}ins")
    ins_el.set(f"{W}id", str(ins_id))
    ins_el.set(f"{W}author", author)
    ins_el.set(f"{W}date", date)
    parent.remove(ins_el)
    ins_inner_run = _make_run(new_text, rpr)
    ins_el.append(ins_inner_run)
    parent.insert(insert_at, ins_el)
    insert_at += 1

    if after:
        run_after = _make_run(after, rpr)
        parent.insert(insert_at, run_after)


def _handle_tracked_delete(
    op: EditOp,
    p: etree._Element,
    author: str,
    date: str,
    holder: list[int],
) -> None:
    """Wrap every run of the paragraph in w:del, plus mark the paragraph mark."""
    runs = list(p.findall(f"{W}r"))
    if not runs:
        raise EditError(
            f"op_id={op.op_id} no runs to delete in paragraph",
            code="EDIT_TRACKED_CHANGE_INVALID",
        )
    for run in runs:
        rpr = run.find(f"{W}rPr")
        idx = list(p).index(run)
        p.remove(run)
        del_id = _next_id(holder)
        del_el = etree.Element(f"{W}del")
        del_el.set(f"{W}id", str(del_id))
        del_el.set(f"{W}author", author)
        del_el.set(f"{W}date", date)
        # Convert each w:t to w:delText.
        for t in run.findall(f"{W}t"):
            del_t = etree.SubElement(run, f"{W}delText")
            if t.get(f"{XML}space"):
                del_t.set(f"{XML}space", t.get(f"{XML}space", ""))
            del_t.text = t.text
            run.remove(t)
        del_el.append(run)
        p.insert(idx, del_el)
        _ = rpr  # rPr stays inside run (preserved automatically)

    # Mark the paragraph mark via w:pPr/w:rPr/w:del.
    ppr = p.find(f"{W}pPr")
    if ppr is None:
        ppr = etree.Element(f"{W}pPr")
        p.insert(0, ppr)
    rpr_in_ppr = ppr.find(f"{W}rPr")
    if rpr_in_ppr is None:
        rpr_in_ppr = etree.SubElement(ppr, f"{W}rPr")
    pmark_del = etree.SubElement(rpr_in_ppr, f"{W}del")
    pmark_del.set(f"{W}id", str(_next_id(holder)))
    pmark_del.set(f"{W}author", author)
    pmark_del.set(f"{W}date", date)


def _handle_add_comment(
    op: EditOp,
    p: etree._Element,
    author: str,
    date: str,
    state: _CommentsState,
) -> None:
    text = op.payload.get("text", "")
    parent_id = op.payload.get("parent_id")
    if not isinstance(text, str) or not text:
        raise EditError(
            f"op_id={op.op_id} add_comment needs payload.text (non-empty str)",
            code="EDIT_PLAN_INVALID",
        )
    new_id = state.next_comment_id()
    new_para_id = state.next_para_id()

    # 1. Append <w:comment id=new_id> to comments.xml.
    comment_el = etree.SubElement(state.comments_root, f"{W}comment")
    comment_el.set(f"{W}id", str(new_id))
    comment_el.set(f"{W}author", author)
    comment_el.set(f"{W}date", date)
    initials = "".join(part[0] for part in author.split() if part)[:3] or "X"
    comment_el.set(f"{W}initials", initials)
    cp = etree.SubElement(comment_el, f"{W}p")
    cp.set(f"{W14}paraId", new_para_id)
    cr = etree.SubElement(cp, f"{W}r")
    ct = etree.SubElement(cr, f"{W}t")
    ct.text = text

    # 2. Add commentRangeStart/End as siblings of w:r in the target paragraph.
    # Insert range markers before the first w:r and after the last w:r so they
    # are NEVER nested inside a w:r (VF-010.inv-7).
    runs = p.findall(f"{W}r")
    if runs:
        first_run = runs[0]
        last_run = runs[-1]
        rs = etree.Element(f"{W}commentRangeStart")
        rs.set(f"{W}id", str(new_id))
        re_el = etree.Element(f"{W}commentRangeEnd")
        re_el.set(f"{W}id", str(new_id))
        first_idx = list(p).index(first_run)
        p.insert(first_idx, rs)
        last_idx = list(p).index(last_run)
        p.insert(last_idx + 1, re_el)
        # Reference run after the rangeEnd.
        ref_run = etree.Element(f"{W}r")
        ref_rpr = etree.SubElement(ref_run, f"{W}rPr")
        rstyle = etree.SubElement(ref_rpr, f"{W}rStyle")
        rstyle.set(f"{W}val", "CommentReference")
        ref = etree.SubElement(ref_run, f"{W}commentReference")
        ref.set(f"{W}id", str(new_id))
        p.insert(list(p).index(re_el) + 1, ref_run)
    else:
        # No runs: put markers at end.
        rs = etree.SubElement(p, f"{W}commentRangeStart")
        rs.set(f"{W}id", str(new_id))
        re_el = etree.SubElement(p, f"{W}commentRangeEnd")
        re_el.set(f"{W}id", str(new_id))

    # 3. Reply linkage via commentsExtended.xml — only when parent_id != None.
    if parent_id is not None:
        try:
            parent_id_int = int(parent_id)
        except (TypeError, ValueError) as exc:
            raise EditError(
                f"op_id={op.op_id} parent_id must be int-compatible",
                code="EDIT_PLAN_INVALID",
            ) from exc
        parent_para_id = state.find_para_id_for_comment(parent_id_int)
        if parent_para_id is None:
            raise EditError(
                f"op_id={op.op_id} parent_id={parent_id_int} not found",
                code="EDIT_TRACKED_CHANGE_INVALID",
            )
        ex = etree.SubElement(state.comments_extended_root, f"{W15}commentEx")
        ex.set(f"{W15}paraId", new_para_id)
        ex.set(f"{W15}paraIdParent", parent_para_id)
        ex.set(f"{W15}done", "0")
    else:
        ex = etree.SubElement(state.comments_extended_root, f"{W15}commentEx")
        ex.set(f"{W15}paraId", new_para_id)
        ex.set(f"{W15}done", "0")

    state.dirty = True


def _handle_accept_change(op: EditOp, p: etree._Element) -> None:
    change_id = _require_change_id(op)
    target = _find_revision(p, change_id)
    if target is None:
        raise EditError(
            f"op_id={op.op_id} change_id={change_id} not found",
            code="EDIT_TRACKED_CHANGE_INVALID",
        )
    if target.tag == f"{W}ins":
        # Accept insertion: drop wrapper, keep inner runs.
        parent = target.getparent()
        if parent is None:
            raise EditError(
                f"op_id={op.op_id} ins has no parent",
                code="EDIT_TRACKED_CHANGE_INVALID",
            )
        idx = list(parent).index(target)
        for child in list(target):
            parent.insert(idx, child)
            idx += 1
        parent.remove(target)
    elif target.tag == f"{W}del":
        # Accept deletion: drop subtree entirely.
        parent = target.getparent()
        if parent is None:
            raise EditError(
                f"op_id={op.op_id} del has no parent",
                code="EDIT_TRACKED_CHANGE_INVALID",
            )
        parent.remove(target)
    else:
        # paragraph-mark deletion (w:pPr/w:rPr/w:del)
        # accept = collapse paragraph mark — we just remove the marker.
        parent = target.getparent()
        if parent is not None:
            parent.remove(target)


def _handle_reject_change(op: EditOp, p: etree._Element) -> None:
    change_id = _require_change_id(op)
    target = _find_revision(p, change_id)
    if target is None:
        raise EditError(
            f"op_id={op.op_id} change_id={change_id} not found",
            code="EDIT_TRACKED_CHANGE_INVALID",
        )
    if target.tag == f"{W}ins":
        # Reject insertion: drop subtree (inserted text gone).
        parent = target.getparent()
        if parent is not None:
            parent.remove(target)
    elif target.tag == f"{W}del":
        # Reject deletion: keep the runs, restore w:t from w:delText.
        parent = target.getparent()
        if parent is None:
            raise EditError(
                f"op_id={op.op_id} del has no parent",
                code="EDIT_TRACKED_CHANGE_INVALID",
            )
        idx = list(parent).index(target)
        for run in list(target):
            # Inside w:del, every w:t was promoted to w:delText. Convert back.
            for del_t in run.findall(f"{W}delText"):
                t = etree.SubElement(run, f"{W}t")
                if del_t.get(f"{XML}space"):
                    t.set(f"{XML}space", del_t.get(f"{XML}space", ""))
                t.text = del_t.text
                run.remove(del_t)
            parent.insert(idx, run)
            idx += 1
        parent.remove(target)
    else:
        # paragraph-mark deletion: reject = remove the marker.
        parent = target.getparent()
        if parent is not None:
            parent.remove(target)


# ---------------------------------------------------------------------------
# Helper: revision lookup, run/paragraph builders, rels prune, styles
# ---------------------------------------------------------------------------


def _require_change_id(op: EditOp) -> int:
    raw = op.payload.get("change_id")
    if raw is None:
        raise EditError(
            f"op_id={op.op_id} payload.change_id required",
            code="EDIT_PLAN_INVALID",
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise EditError(
            f"op_id={op.op_id} payload.change_id must be int-compatible",
            code="EDIT_PLAN_INVALID",
        ) from exc


def _find_revision(p: etree._Element, change_id: int) -> etree._Element | None:
    for el in p.iter():
        if el.tag in {f"{W}ins", f"{W}del"}:
            val = el.get(f"{W}id")
            if val is not None:
                try:
                    if int(val) == change_id:
                        return el
                except ValueError:
                    continue
    return None


def _next_revision_id(unpack_dir: Path) -> int:
    """Find the highest existing w:id on w:ins/w:del across document.xml."""
    full = unpack_dir / "word/document.xml"
    if not full.exists():
        return 1
    try:
        root = etree.parse(str(full)).getroot()
    except etree.XMLSyntaxError:  # pragma: no cover
        return 1
    used: list[int] = []
    for el in root.iter():
        if el.tag in {f"{W}ins", f"{W}del"}:
            val = el.get(f"{W}id")
            if val is not None:
                try:
                    used.append(int(val))
                except ValueError:
                    continue
    return (max(used) + 1) if used else 1


def _next_id(holder: list[int]) -> int:
    val = holder[0]
    holder[0] = val + 1
    return val


def _make_run(
    text: str, rpr_template: etree._Element | None, *, as_del: bool = False
) -> etree._Element:
    run = etree.Element(f"{W}r")
    if rpr_template is not None:
        # Deep copy the rPr template so both branches preserve it (inv-5).
        run.append(_deep_copy(rpr_template))
    tag = f"{W}delText" if as_del else f"{W}t"
    t = etree.SubElement(run, tag)
    if text != text.strip():
        t.set(f"{XML}space", "preserve")
    t.text = text
    return run


def _deep_copy(el: etree._Element) -> etree._Element:
    return etree.fromstring(etree.tostring(el))


def _build_paragraph(text: str, style_id: str) -> etree._Element:
    p = etree.Element(f"{W}p")
    ppr = etree.SubElement(p, f"{W}pPr")
    pstyle = etree.SubElement(ppr, f"{W}pStyle")
    pstyle.set(f"{W}val", style_id)
    r = etree.SubElement(p, f"{W}r")
    t = etree.SubElement(r, f"{W}t")
    if text != text.strip():
        t.set(f"{XML}space", "preserve")
    t.text = text
    return p


def _collect_rids(p: etree._Element) -> set[str]:
    """Collect r:id / r:embed values referenced inside a paragraph."""
    rids: set[str] = set()
    for el in p.iter():
        for attr_name, attr_val in el.attrib.items():
            if (
                isinstance(attr_name, str)
                and attr_name.startswith(R)
                and isinstance(attr_val, str)
            ):
                rids.add(attr_val)
    return rids


def _prune_unused_rels(unpack_dir: Path, candidate_rids: set[str]) -> None:
    doc = unpack_dir / "word/document.xml"
    rels = unpack_dir / "word/_rels/document.xml.rels"
    if not doc.exists() or not rels.exists():
        return
    try:
        doc_root = etree.parse(str(doc)).getroot()
    except etree.XMLSyntaxError:  # pragma: no cover
        return
    still_used: set[str] = set()
    for el in doc_root.iter():
        for k, v in el.attrib.items():
            if isinstance(k, str) and k.startswith(R) and v in candidate_rids:
                still_used.add(v)
    to_drop = candidate_rids - still_used
    if not to_drop:
        return
    try:
        rels_root = etree.parse(str(rels)).getroot()
    except etree.XMLSyntaxError:  # pragma: no cover
        return
    for rel in list(rels_root.findall(f"{PR}Relationship")):
        if rel.get("Id") in to_drop:
            rels_root.remove(rel)
    rels.write_bytes(
        etree.tostring(
            rels_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
    )


def _style_is_known(unpack_dir: Path, style_id: str) -> bool:
    if style_id in STANDARD_STYLE_IDS:
        return True
    styles_xml = unpack_dir / "word/styles.xml"
    if not styles_xml.exists():
        return False
    try:
        root = etree.parse(str(styles_xml)).getroot()
    except etree.XMLSyntaxError:  # pragma: no cover
        return False
    return any(
        s.get(f"{W}styleId") == style_id for s in root.iter(f"{W}style")
    )


def _truncate_snippet(text: str) -> str:
    if len(text) <= SNIPPET_MAX_LEN:
        return text
    return text[: SNIPPET_MAX_LEN - 3] + "..."


def _now_iso_utc() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Comments state
# ---------------------------------------------------------------------------


@dataclass
class _CommentsState:
    """In-memory aggregate for word/comments.xml + word/commentsExtended.xml.

    Holds the lxml roots so handlers append to them in-place; flush() writes
    them back to disk and updates [Content_Types].xml + .rels on first add.
    """

    comments_root: etree._Element
    comments_extended_root: etree._Element
    has_comments_part: bool
    has_extended_part: bool
    next_id: int
    next_para_seed: int
    dirty: bool = False

    @classmethod
    def load(cls, unpack_dir: Path) -> _CommentsState:
        comments_path = unpack_dir / "word/comments.xml"
        extended_path = unpack_dir / "word/commentsExtended.xml"
        has_comments = comments_path.exists()
        has_extended = extended_path.exists()
        if has_comments:
            comments_root = etree.parse(str(comments_path)).getroot()
            ids: list[int] = []
            for c in comments_root.iter(f"{W}comment"):
                v = c.get(f"{W}id")
                if v is not None:
                    with contextlib.suppress(ValueError):
                        ids.append(int(v))
            next_id = (max(ids) + 1) if ids else 0
        else:
            comments_root = etree.Element(
                f"{W}comments",
                nsmap={"w": W_NS, "w14": W14_NS},
            )
            next_id = 0
        if has_extended:
            comments_extended_root = etree.parse(str(extended_path)).getroot()
        else:
            comments_extended_root = etree.Element(
                f"{W15}commentsEx",
                nsmap={"w15": W15_NS},
            )
        return cls(
            comments_root=comments_root,
            comments_extended_root=comments_extended_root,
            has_comments_part=has_comments,
            has_extended_part=has_extended,
            next_id=next_id,
            next_para_seed=1,
        )

    def next_comment_id(self) -> int:
        val = self.next_id
        self.next_id += 1
        return val

    def next_para_id(self) -> str:
        # 8-hex string. We use a private counter so the value is deterministic
        # within this run, not a clock-derived one.
        val = self.next_para_seed
        self.next_para_seed += 1
        return f"{val:08X}"

    def find_para_id_for_comment(self, comment_id: int) -> str | None:
        for c in self.comments_root.iter(f"{W}comment"):
            v = c.get(f"{W}id")
            if v is None:
                continue
            try:
                if int(v) != comment_id:
                    continue
            except ValueError:
                continue
            for inner_p in c.iter(f"{W}p"):
                pid = inner_p.get(f"{W14}paraId")
                if pid:
                    return pid
            return None
        return None

    def flush(self, unpack_dir: Path) -> None:
        if not self.dirty:
            return
        # Always write whichever files exist or were created.
        word = unpack_dir / "word"
        word.mkdir(exist_ok=True)
        (word / "comments.xml").write_bytes(
            etree.tostring(
                self.comments_root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
        )
        (word / "commentsExtended.xml").write_bytes(
            etree.tostring(
                self.comments_extended_root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
        )
        # Add Content-Types overrides + relationships if first time.
        if not self.has_comments_part:
            _ensure_content_type_override(
                unpack_dir,
                "/word/comments.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
            )
            _ensure_relationship(
                unpack_dir,
                "word/_rels/document.xml.rels",
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
                "comments.xml",
            )
            self.has_comments_part = True
        if not self.has_extended_part:
            _ensure_content_type_override(
                unpack_dir,
                "/word/commentsExtended.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml",
            )
            _ensure_relationship(
                unpack_dir,
                "word/_rels/document.xml.rels",
                "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
                "commentsExtended.xml",
            )
            self.has_extended_part = True


def _ensure_content_type_override(
    unpack_dir: Path, part_name: str, content_type: str
) -> None:
    ct_path = unpack_dir / "[Content_Types].xml"
    if not ct_path.exists():
        return
    root = etree.parse(str(ct_path)).getroot()
    for ov in root.findall(f"{CT}Override"):
        if ov.get("PartName") == part_name:
            return
    ov = etree.SubElement(root, f"{CT}Override")
    ov.set("PartName", part_name)
    ov.set("ContentType", content_type)
    ct_path.write_bytes(
        etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
    )


def _ensure_relationship(
    unpack_dir: Path, rels_relpath: str, rel_type: str, target: str
) -> None:
    rels_path = unpack_dir / rels_relpath
    if not rels_path.exists():
        rels_path.parent.mkdir(parents=True, exist_ok=True)
        rels_path.write_bytes(
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<Relationships xmlns="{PR_NS}"></Relationships>'
            ).encode()
        )
    root = etree.parse(str(rels_path)).getroot()
    for rel in root.findall(f"{PR}Relationship"):
        if rel.get("Type") == rel_type and rel.get("Target") == target:
            return
    used_ids = {rel.get("Id", "") for rel in root.findall(f"{PR}Relationship")}
    new_id = _generate_rel_id(used_ids)
    rel = etree.SubElement(root, f"{PR}Relationship")
    rel.set("Id", new_id)
    rel.set("Type", rel_type)
    rel.set("Target", target)
    rels_path.write_bytes(
        etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
    )


def _generate_rel_id(used: set[str]) -> str:
    i = 1
    while f"rId{i}" in used:
        i += 1
    return f"rId{i}"


# ---------------------------------------------------------------------------
# Public API: render_diff
# ---------------------------------------------------------------------------


# START_CONTRACT: render_diff
#   PURPOSE: Format a list of OpOutcome into the EditResult.diff field.
#            Currently a pass-through: snippets are already truncated to
#            SNIPPET_MAX_LEN at op-execution time.
#   INPUTS: { outcomes: list[OpOutcome] }
#   OUTPUTS: { list[OpOutcome] }
#   SIDE_EFFECTS: none.
# END_CONTRACT: render_diff
def render_diff(outcomes: list[OpOutcome]) -> list[OpOutcome]:
    return list(outcomes)


# ---------------------------------------------------------------------------
# Convenience: build EditMetadata + serialize EditPlan from JSON
# ---------------------------------------------------------------------------


def make_edit_metadata(
    user_instruction: str,
    anchors: list[TextAnchor],
    *,
    model: str = "manual",
) -> EditMetadata:
    """Build EditMetadata with M-EDIT-computed correlation_id and source_prompt_hash.

    Tests use this to construct EditPlans without going through an LLM. The
    helper is also useful for the Step-3 MCP wrapper.
    """
    return EditMetadata(
        correlation_id=str(uuid.uuid4()),
        source_prompt_hash=_compute_source_prompt_hash(
            user_instruction, anchors
        ),
        model=model,
        created_at=_now_iso_utc(),
    )


def edit_plan_from_dict(raw: dict[str, Any]) -> EditPlan:
    """Construct an EditPlan from a JSON-decoded dict.

    Accepts ``{"format": "...", "ops": [...], "metadata": {...}?}``. The
    metadata block, if present, is honored only for ``model``; correlation_id
    and source_prompt_hash are computed by M-EDIT (VF-010.inv-10).
    """
    fmt = raw.get("format", "docx")
    if fmt not in {"docx", "pptx"}:
        raise EditError(
            f"EditPlan.format {fmt!r} unsupported", code="EDIT_PLAN_INVALID"
        )
    ops_raw = raw.get("ops", [])
    if not isinstance(ops_raw, list):
        raise EditError(
            "EditPlan.ops must be a list", code="EDIT_PLAN_INVALID"
        )
    ops = [EditOp.from_dict(o) for o in ops_raw]
    meta_raw = raw.get("metadata") or {}
    model = (
        meta_raw.get("model", "manual") if isinstance(meta_raw, dict) else "manual"
    )
    metadata = EditMetadata(
        correlation_id=str(uuid.uuid4()),
        source_prompt_hash=hashlib.sha256(
            json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        model=model,
        created_at=_now_iso_utc(),
    )
    return EditPlan(format=fmt, ops=ops, metadata=metadata)
