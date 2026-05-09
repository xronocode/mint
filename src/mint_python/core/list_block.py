# FILE: src/mint_python/core/list_block.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: List block — bulleted, numbered, or checklist sequence rendered
#     as N python-docx paragraphs with the appropriate built-in list style.
#     Sibling block of Paragraph/Table/Image/Chart inside a Section.
#   SCOPE: Public surface = List, ListKind, ListError, ListLevelError.
#     render() takes a python-docx Document and writes into it; never to disk.
#   DEPENDS: python-docx (1.1.x).
#   LINKS: docs/development-plan.xml#MP-LIST,
#     docs/verification-plan.xml#V-MP-LIST,
#     docs/knowledge-graph.xml#MP-LIST
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ListKind         - enum: BULLET | NUMBERED | CHECKLIST
#   List             - @dataclass; items + kind + level + .render
#   ListError        - base error
#   ListLevelError   - LIST_LEVEL_OUT_OF_RANGE
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — initial implementation. Items=str only;
#     Paragraph-typed items deferred to a future minor.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from docx.document import Document as DocxDocument
from docx.shared import Inches

logger = logging.getLogger("mint_python.core.list_block")


class ListError(Exception):
    """Base for MP-LIST errors."""


class ListLevelError(ListError):
    """Raised when List.level is negative (LIST_LEVEL_OUT_OF_RANGE)."""


class ListKind(StrEnum):
    """List rendering strategy.

    BULLET    → python-docx 'List Bullet' paragraph style.
    NUMBERED  → python-docx 'List Number' paragraph style.
    CHECKLIST → 'List Bullet' style with a ☐ (U+2610) prefix on each item.
                Native Word checkbox content-controls are intentionally NOT
                emitted; the unicode prefix renders identically across
                Word/LibreOffice/Google Docs without the heavy content-control
                machinery. A future minor may add a ``checklist_native``
                rendering mode.
    """

    BULLET = "bullet"
    NUMBERED = "numbered"
    CHECKLIST = "checklist"


_DOCX_LIST_STYLE: dict[ListKind, str] = {
    ListKind.BULLET: "List Bullet",
    ListKind.NUMBERED: "List Number",
    ListKind.CHECKLIST: "List Bullet",
}

_CHECKLIST_PREFIX = "☐ "  # ☐ + space


@dataclass
class List:
    """Ordered sequence of list items rendered as N styled paragraphs.

    Construction::

        List(["a", "b", "c"])                        # default = bullet
        List(["1", "2"], kind=ListKind.NUMBERED)
        List(["todo"], kind=ListKind.CHECKLIST)
        List(["sub-a"], level=1)                     # nested (indent step)

    Nesting: ``level=N`` applies ``N * 0.25 in`` left indent. The marker style
    stays the same (python-docx's auto-numbered nested list styles like
    ``List Bullet 2`` are not used in v0.1.0 to keep the rendering predictable
    across themes that don't ship those styles).
    """

    items: list[str] = field(default_factory=list)
    kind: ListKind = ListKind.BULLET
    level: int = 0

    def __post_init__(self) -> None:
        if self.level < 0:
            raise ListLevelError(
                f"List.level must be >= 0, got {self.level}"
            )

    # START_BLOCK_RENDER_LIST
    def render(self, parent_doc: DocxDocument) -> None:
        """Append ``len(items)`` paragraphs; one per item.

        Forbidden-1 invariant: render() only mutates ``parent_doc``.
        Forbidden-2 invariant: empty ``items`` emits zero paragraphs and is
        silently allowed (the caller may have a conditional list).
        """
        logger.info(
            "[MP-List][render][BLOCK_RENDER_LIST] "
            "kind=%s items=%d level=%d",
            self.kind.value,
            len(self.items),
            self.level,
        )

        style_name = _DOCX_LIST_STYLE[self.kind]
        prefix = _CHECKLIST_PREFIX if self.kind is ListKind.CHECKLIST else ""
        indent = Inches(0.25 * self.level) if self.level > 0 else None

        for item in self.items:
            docx_para: Any = parent_doc.add_paragraph(
                prefix + item, style=style_name
            )
            if indent is not None:
                docx_para.paragraph_format.left_indent = indent
    # END_BLOCK_RENDER_LIST


__all__ = [
    "List",
    "ListError",
    "ListKind",
    "ListLevelError",
]
