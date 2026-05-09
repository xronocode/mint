# FILE: tools/article_experiment/spec.py
# VERSION: 0.1.0
"""Article spec — the contract between the LLM and the MINT builder.

The spec is the shape an LLM must return: typed blocks (paragraph / callout /
list / table / code), grouped into sections, wrapped in a top-level document.
The model never touches OOXML, hex codes, or layout — only structure.

Two surfaces here:
  - dataclasses (typed) — what the builder consumes
  - parse_spec(data: dict) -> ArticleSpec — tolerant parser used at runtime;
    ignores unknown fields, defaults missing optionals, raises SpecParseError
    only on shape errors that would crash the builder

The same module renders the JSON-schema-like description embedded in the
prompt — kept as a Python-side string so the schema and parser stay in sync.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CalloutKind = Literal["info", "warning", "code"]
ListKind = Literal["bullet", "numbered", "checklist"]
SectionLayoutOrientation = Literal["portrait", "landscape"]


class SpecParseError(ValueError):
    """Raised when the LLM's JSON is mis-shaped beyond the parser's tolerance.

    Schema-level violations (missing required fields, wrong types on the
    skeleton) raise this. Field-level oddities (unknown keys, extra blocks,
    minor type slips on optional fields) are silently dropped or coerced —
    the goal is for a 4B-class model's output to still produce a doc.
    """


# --------------------------------------------------------------------------- #
# Block types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParagraphBlock:
    text: str
    # Substrings to bold within ``text``; resolved by the builder via a
    # naive str.find-and-split so the LLM doesn't have to emit run lists.
    emphasis: tuple[str, ...] = ()
    type: Literal["paragraph"] = "paragraph"


@dataclass(frozen=True)
class CalloutBlock:
    body: str
    kind: CalloutKind = "info"
    title: str | None = None
    type: Literal["callout"] = "callout"


@dataclass(frozen=True)
class ListBlock:
    items: tuple[str, ...]
    kind: ListKind = "bullet"
    type: Literal["list"] = "list"


@dataclass(frozen=True)
class TableBlock:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    type: Literal["table"] = "table"


@dataclass(frozen=True)
class CodeBlock:
    content: str
    language: str = ""  # informational; rendered identically regardless
    type: Literal["code"] = "code"


Block = ParagraphBlock | CalloutBlock | ListBlock | TableBlock | CodeBlock


# --------------------------------------------------------------------------- #
# Section + layout
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SectionLayout:
    orientation: SectionLayoutOrientation = "portrait"
    columns: int = 1
    header: str | None = None
    footer: str | None = None


@dataclass(frozen=True)
class SpecSection:
    title: str
    blocks: tuple[Block, ...]
    level: int = 1  # 1..3
    layout: SectionLayout | None = None


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ArticleSpec:
    title: str
    sections: tuple[SpecSection, ...]
    subtitle: str | None = None
    # Free-form metadata — model is encouraged to populate, builder uses
    # for cover footer and report attribution. Unknown keys preserved.
    meta: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _as_str(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_as_str(v) for v in value)


def _parse_block(data: Any) -> Block | None:
    if not isinstance(data, dict):
        return None
    btype = _as_str(data.get("type")).lower().strip()

    if btype == "paragraph":
        text = _as_str(data.get("text"))
        if not text:
            return None
        return ParagraphBlock(
            text=text,
            emphasis=_as_str_tuple(data.get("emphasis", ())),
        )

    if btype == "callout":
        body = _as_str(data.get("body") or data.get("text"))
        if not body:
            return None
        kind_raw = _as_str(data.get("kind", "info")).lower().strip()
        kind: CalloutKind = (
            kind_raw if kind_raw in ("info", "warning", "code") else "info"
        )
        title = data.get("title")
        return CalloutBlock(
            body=body,
            kind=kind,
            title=_as_str(title) if title else None,
        )

    if btype == "list":
        items = _as_str_tuple(data.get("items", ()))
        if not items:
            return None
        kind_raw = _as_str(data.get("kind", "bullet")).lower().strip()
        kind: ListKind = (
            kind_raw if kind_raw in ("bullet", "numbered", "checklist") else "bullet"
        )
        return ListBlock(items=items, kind=kind)

    if btype == "table":
        header = _as_str_tuple(data.get("header", ()))
        raw_rows = data.get("rows", [])
        if not isinstance(raw_rows, (list, tuple)):
            return None
        rows = tuple(_as_str_tuple(r) for r in raw_rows if isinstance(r, (list, tuple)))
        # Accept tables with no header (header=()) or no rows (rows=()) —
        # builder skips empties. Reject only the truly empty table.
        if not header and not rows:
            return None
        return TableBlock(header=header, rows=rows)

    if btype == "code":
        content = _as_str(data.get("content") or data.get("body"))
        if not content:
            return None
        return CodeBlock(content=content, language=_as_str(data.get("language")))

    return None


def _parse_layout(data: Any) -> SectionLayout | None:
    if not isinstance(data, dict):
        return None
    orient = _as_str(data.get("orientation", "portrait")).lower().strip()
    if orient not in ("portrait", "landscape"):
        orient = "portrait"
    columns_raw = data.get("columns", 1)
    try:
        columns = int(columns_raw)
    except (TypeError, ValueError):
        columns = 1
    columns = max(1, min(columns, 4))  # clamp — builder accepts 1..12 but
    # runaway values from a flaky model would dwarf the page; 4 is safe.
    header = data.get("header")
    footer = data.get("footer")
    if orient == "portrait" and columns == 1 and not header and not footer:
        return None  # nothing meaningful to apply
    return SectionLayout(
        orientation=orient,  # type: ignore[arg-type]
        columns=columns,
        header=_as_str(header) if header else None,
        footer=_as_str(footer) if footer else None,
    )


def _parse_section(data: Any) -> SpecSection | None:
    if not isinstance(data, dict):
        return None
    title = _as_str(data.get("title"))
    if not title:
        return None
    raw_blocks = data.get("blocks", [])
    if not isinstance(raw_blocks, (list, tuple)):
        return None
    blocks = tuple(b for b in (_parse_block(rb) for rb in raw_blocks) if b is not None)
    if not blocks:
        return None
    level_raw = data.get("level", 1)
    try:
        level = int(level_raw)
    except (TypeError, ValueError):
        level = 1
    level = max(1, min(level, 3))
    return SpecSection(
        title=title,
        blocks=blocks,
        level=level,
        layout=_parse_layout(data.get("layout")),
    )


def parse_spec(data: Any) -> ArticleSpec:
    """Parse a model-emitted dict into an ArticleSpec.

    Tolerant: unknown keys ignored, malformed blocks dropped, level/columns
    clamped. Raises SpecParseError ONLY when the document skeleton is so
    broken the builder couldn't render anything (no title, no sections,
    no usable content).
    """
    if not isinstance(data, dict):
        raise SpecParseError(f"spec root must be an object, got {type(data).__name__}")
    title = _as_str(data.get("title"))
    if not title:
        raise SpecParseError("spec.title is required and must be a non-empty string")
    raw_sections = data.get("sections", [])
    if not isinstance(raw_sections, (list, tuple)):
        raise SpecParseError(
            f"spec.sections must be an array, got {type(raw_sections).__name__}"
        )
    sections = tuple(
        s for s in (_parse_section(rs) for rs in raw_sections) if s is not None
    )
    if not sections:
        raise SpecParseError("spec must contain at least one usable section")
    subtitle = data.get("subtitle")
    raw_meta = data.get("meta", {})
    meta = (
        {str(k): _as_str(v) for k, v in raw_meta.items()}
        if isinstance(raw_meta, dict)
        else {}
    )
    return ArticleSpec(
        title=title,
        sections=sections,
        subtitle=_as_str(subtitle) if subtitle else None,
        meta=meta,
    )


# --------------------------------------------------------------------------- #
# Schema description for the prompt — kept as a Python string so it never
# drifts from the parser. This is what the LLM actually sees.
# --------------------------------------------------------------------------- #


PROMPT_SCHEMA_DESCRIPTION = """\
Return a single JSON object with this shape (no preface, no markdown fence):

{
  "title": "<string, required>",
  "subtitle": "<string, optional>",
  "meta": { "<string-key>": "<string-value>", ... },
  "sections": [
    {
      "title": "<string, required>",
      "level": 1 | 2 | 3,
      "layout": {
        "orientation": "portrait" | "landscape",
        "columns": 1 | 2 | 3 | 4,
        "header": "<string|null>",
        "footer": "<string|null>"
      },
      "blocks": [
        { "type": "paragraph", "text": "<string>", "emphasis": ["<phrase to bold>", ...] },
        { "type": "callout",   "kind": "info" | "warning" | "code", "body": "<string>", "title": "<string|null>" },
        { "type": "list",      "kind": "bullet" | "numbered" | "checklist", "items": ["<string>", ...] },
        { "type": "table",     "header": ["<col>", ...], "rows": [ ["<cell>", ...], ... ] },
        { "type": "code",      "language": "<string>", "content": "<string>" }
      ]
    }
  ]
}

Rules:
- Output ONLY the JSON object. No prose before or after. No ```json fence.
- Every block MUST carry a "type" field with one of the five values above.
- "emphasis" is optional; substrings listed will be rendered bold inside the paragraph.
- "layout" is optional per section; omit it for default portrait single-column.
- Tables are rectangular: every row must have the same number of cells as "header".
"""
