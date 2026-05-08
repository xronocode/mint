# FILE: src/mint_python/core/section.py
# VERSION: 0.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Fluent Section node per handover §3.2 — heading + ordered list of
#     content blocks (Paragraph, Table, Image). Phase-1 ships the API surface;
#     add_chart is an explicit Phase-2 stub raising PhaseGuardNotImplementedError
#     after emitting BLOCK_PHASE_GUARD so plan-driven callers fail loud at the
#     boundary instead of silently no-op'ing.
#   SCOPE: Public surface = Section, SectionError, SectionLevelOutOfRangeError,
#     PhaseGuardNotImplementedError. Sibling-only deps: MP-CONTENT (Paragraph,
#     Image), MP-TABLE (Table). Section.render(parent_doc) emits a heading
#     paragraph via parent_doc.add_heading(title, level) then iterates blocks
#     calling each .render(parent_doc); children own their own BLOCK markers.
#   DEPENDS: mint_python.core.content (Paragraph, Image), mint_python.core.table
#     (Table), python-docx (1.1.x; Document type only).
#   LINKS: docs/development-plan.xml#MP-SECTION,
#     docs/verification-plan.xml#V-MP-SECTION,
#     docs/knowledge-graph.xml#MP-SECTION
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Section                          - @dataclass; title + level (1..6) + blocks
#   Section.add_paragraph            - str shorthand or Paragraph; returns self
#   Section.add_table                - Table; returns self
#   Section.add_image                - Image; returns self
#   Section.add_chart                - Phase-2 STUB; emits BLOCK_PHASE_GUARD then raises
#   Section.render                   - heading + ordered block render
#   SectionError                     - base error
#   SectionLevelOutOfRangeError      - SECTION_LEVEL_OUT_OF_RANGE
#   PhaseGuardNotImplementedError    - PHASE_GUARD_NOT_IMPLEMENTED (NotImplementedError subclass)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-3 (MP-SECTION): initial implementation per V-MP-SECTION
#     scenarios 1-6 + BLOCK_PHASE_GUARD trace assertion.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from docx.document import Document as DocxDocument

from mint_python.core.content import Image, Paragraph
from mint_python.core.table import Table

logger = logging.getLogger("mint_python.core.section")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SectionError(Exception):
    """Base for MP-SECTION errors."""


class SectionLevelOutOfRangeError(SectionError):
    """Raised when Section level is not in 1..6 (SECTION_LEVEL_OUT_OF_RANGE).

    python-docx supports level=0..9 on add_heading, but the MP-SECTION contract
    constrains us to 1..6 (matching docx Heading 1..Heading 6 style names) so
    that style-resolution downstream stays predictable.
    """


class PhaseGuardNotImplementedError(NotImplementedError):
    """Phase-N stub touched on a not-yet-implemented method.

    Subclass of :class:`NotImplementedError` so callers using ``except
    NotImplementedError`` keep working; carries the target phase + method name
    in the message for diagnosability.
    """


# ---------------------------------------------------------------------------
# Section
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """Section node holding a heading + ordered content blocks.

    Construction::

        Section("Overview", level=1) \
            .add_paragraph("intro line") \
            .add_table(my_table) \
            .add_image(my_image)

    The fluent setters return ``self`` so callers compose the whole section in
    a single expression. ``level`` is validated at construction time (1..6).

    add_chart is a Phase-2 stub: it emits a single
    ``[MP-Section][add_chart][BLOCK_PHASE_GUARD]`` INFO line BEFORE raising
    :class:`PhaseGuardNotImplementedError`, so plan-driven callers that
    accidentally schedule a chart in Phase-1 fail loud + observable.
    """

    title: str
    level: int  # 1..6
    _blocks: list[Paragraph | Table | Image] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not (1 <= self.level <= 6):
            raise SectionLevelOutOfRangeError(
                f"Section level must be in 1..6, got {self.level}"
            )

    def add_paragraph(
        self, text_or_paragraph: str | Paragraph
    ) -> Section:
        """Append a paragraph block; accepts str shorthand or Paragraph instance.

        - ``str`` is wrapped via ``Paragraph(text)`` so callers don't have to
          import the sibling type for the common case.
        - A ``Paragraph`` instance is appended as-is, preserving its existing
          style + run list. We deliberately do NOT accept a ``style`` kwarg
          here: the str path can be styled by constructing the Paragraph
          explicitly, which avoids the ambiguity of "what wins, the explicit
          Paragraph.style or the section-level override?".
        """
        if isinstance(text_or_paragraph, str):
            self._blocks.append(Paragraph(text_or_paragraph))
        else:
            self._blocks.append(text_or_paragraph)
        return self

    def add_table(self, table: Table) -> Section:
        """Append a Table block; returns self for fluent chaining."""
        self._blocks.append(table)
        return self

    def add_image(self, image: Image) -> Section:
        """Append an Image block; returns self for fluent chaining."""
        self._blocks.append(image)
        return self

    # START_BLOCK_PHASE_GUARD
    def add_chart(self, *args: Any, **kwargs: Any) -> Section:
        """Phase-2 stub: emit BLOCK_PHASE_GUARD then raise.

        The matplotlib-backed chart implementation lands with handover §6
        Phase 2 (MP-CHART). Phase-7 ships the API surface only so plan
        consumers can resolve the symbol but get an immediate, observable
        failure if they try to use it.
        """
        logger.info(
            "[MP-Section][add_chart][BLOCK_PHASE_GUARD] "
            "method=add_chart target_phase=Phase 2"
        )
        raise PhaseGuardNotImplementedError(
            "Section.add_chart is a Phase-2 stub: matplotlib chart "
            "implementation lands with handover §6 Phase 2 (MP-CHART). "
            "Phase-7 ships the API surface only."
        )
    # END_BLOCK_PHASE_GUARD

    def render(self, parent_doc: DocxDocument) -> None:
        """Emit heading + ordered child block renders into ``parent_doc``.

        python-docx ``Document.add_heading(text, level=N)`` maps level 1..6 to
        the built-in ``Heading 1`` .. ``Heading 6`` style. We don't emit our
        own BLOCK marker here — children (Paragraph / Table / Image) own
        their own BLOCK_RENDER_CONTENT / BLOCK_RENDER_TABLE markers and
        emitting an extra section-level marker would over-instrument the
        trace. Callers who need section boundaries can reconstruct them
        from caplog by pairing add_heading payloads with their level.
        """
        parent_doc.add_heading(self.title, level=self.level)
        for block in self._blocks:
            block.render(parent_doc)


__all__ = [
    "PhaseGuardNotImplementedError",
    "Section",
    "SectionError",
    "SectionLevelOutOfRangeError",
]
