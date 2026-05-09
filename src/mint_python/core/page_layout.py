# FILE: src/mint_python/core/page_layout.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Page-layout primitives (orientation, margins, columns, per-section
#     header/footer, page break) for Section.with_page_layout. Closes the
#     showcase gaps "no section API" (multi-column, per-section H/F, page
#     breaks) and "no page API" (landscape, custom margins) in one surface.
#   SCOPE: Public types Margins, Orientation, PageLayout. apply_to_docx_section
#     emits the docx Section configuration (orientation swap, margins,
#     header/footer text, w:cols) on a python-docx Section returned by
#     doc.add_section / doc.sections[0]. Trace marker BLOCK_APPLY_LAYOUT.
#   DEPENDS: python-docx (Section, WD_ORIENTATION, WD_SECTION, Inches),
#     lxml (etree, qn) for the w:cols element which python-docx doesn't expose.
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Orientation             - Literal["portrait", "landscape"]
#   Margins                 - frozen dataclass; top/bottom/left/right inches
#   PageLayout              - frozen dataclass; orientation, margins, columns,
#                             header/footer overrides, page_break_before
#   apply_to_docx_section   - configure a python-docx Section from a PageLayout
# END_MODULE_MAP

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from docx.enum.section import WD_ORIENTATION
from docx.oxml.ns import qn
from docx.section import Section as DocxSection
from docx.shared import Inches
from lxml import etree

logger = logging.getLogger(__name__)

Orientation = Literal["portrait", "landscape"]


class PageLayoutError(ValueError):
    """Base for invalid PageLayout / Margins values."""


@dataclass(frozen=True)
class Margins:
    """Page margins in inches; defaults to Word's standard 1-inch margin.

    Validated at construction: each side must be > 0 and < 22 inches (Word
    rejects values outside ~0..22 in via its UI; we mirror that floor/ceiling
    so an obvious typo like ``Margins(top=100)`` errors immediately rather
    than producing a silently-broken document).
    """

    top: float = 1.0
    bottom: float = 1.0
    left: float = 1.0
    right: float = 1.0

    def __post_init__(self) -> None:
        for name in ("top", "bottom", "left", "right"):
            value = getattr(self, name)
            if not (0 < value < 22):
                raise PageLayoutError(
                    f"Margins.{name} must be in (0, 22) inches, got {value}"
                )


@dataclass(frozen=True)
class PageLayout:
    """Per-Section page-layout configuration.

    Each field is independently optional; passing only ``columns=2`` leaves
    orientation and margins at the inherited defaults. ``page_break_before``
    controls whether the Section starts on a new page (``True``, default) or
    continues on the current page with a continuous section break (``False``).
    """

    orientation: Orientation = "portrait"
    margins: Margins = field(default_factory=Margins)
    columns: int = 1
    column_spacing_inches: float = 0.5
    header: str | None = None
    footer: str | None = None
    page_break_before: bool = True

    def __post_init__(self) -> None:
        if self.columns < 1 or self.columns > 12:
            raise PageLayoutError(
                f"PageLayout.columns must be in 1..12, got {self.columns}"
            )
        if self.column_spacing_inches <= 0:
            raise PageLayoutError(
                "PageLayout.column_spacing_inches must be > 0, "
                f"got {self.column_spacing_inches}"
            )
        if self.orientation not in ("portrait", "landscape"):
            raise PageLayoutError(
                "PageLayout.orientation must be 'portrait' or 'landscape', "
                f"got {self.orientation!r}"
            )


def apply_to_docx_section(docx_section: DocxSection, layout: PageLayout) -> None:
    """Configure a python-docx Section from a PageLayout.

    - Orientation: sets ``WD_ORIENTATION`` and swaps page_width/page_height
      because python-docx doesn't auto-swap on orientation change.
    - Margins: per-side ``Inches`` assignment.
    - Columns: appends/replaces a ``<w:cols w:num="N" w:space="..."/>`` element
      on ``_sectPr`` since python-docx doesn't expose a public columns API.
    - Header/footer: sets text on the section's first paragraph and disables
      ``is_linked_to_previous`` so the override actually takes effect.
    """
    # START_BLOCK_APPLY_LAYOUT
    logger.info(
        "[MP-PageLayout][apply][BLOCK_APPLY_LAYOUT] "
        "orientation=%s columns=%d page_break_before=%s header=%s footer=%s",
        layout.orientation,
        layout.columns,
        layout.page_break_before,
        layout.header is not None,
        layout.footer is not None,
    )
    # END_BLOCK_APPLY_LAYOUT

    # python-docx doesn't auto-swap page_width/page_height when orientation
    # changes, AND add_section() inherits dimensions from the previous
    # section — so a portrait Section that follows a landscape one would
    # otherwise keep the landscape dimensions even after we set orientation
    # back. Normalize: portrait ⇒ width < height; landscape ⇒ width > height.
    # python-docx types these as Length | None, but on any section returned by
    # add_section() / sections[0] they are always set (defaulted to letter
    # 8.5"×11" by the underlying docx package).
    width = docx_section.page_width
    height = docx_section.page_height
    assert width is not None and height is not None
    if layout.orientation == "landscape":
        docx_section.orientation = WD_ORIENTATION.LANDSCAPE
        if width < height:
            docx_section.page_width, docx_section.page_height = height, width
    else:
        docx_section.orientation = WD_ORIENTATION.PORTRAIT
        if width > height:
            docx_section.page_width, docx_section.page_height = height, width

    docx_section.top_margin = Inches(layout.margins.top)
    docx_section.bottom_margin = Inches(layout.margins.bottom)
    docx_section.left_margin = Inches(layout.margins.left)
    docx_section.right_margin = Inches(layout.margins.right)

    sect_pr = docx_section._sectPr
    # Drop any pre-existing w:cols so re-applying a layout is idempotent.
    for existing in sect_pr.findall(qn("w:cols")):
        sect_pr.remove(existing)
    cols = etree.SubElement(sect_pr, qn("w:cols"))
    cols.set(qn("w:num"), str(layout.columns))
    # column_spacing_inches → twentieths-of-a-point (1 inch = 1440 twips).
    cols.set(qn("w:space"), str(int(layout.column_spacing_inches * 1440)))

    if layout.header is not None:
        docx_section.header.is_linked_to_previous = False
        docx_section.header.paragraphs[0].text = layout.header
    if layout.footer is not None:
        docx_section.footer.is_linked_to_previous = False
        docx_section.footer.paragraphs[0].text = layout.footer


__all__ = [
    "Margins",
    "Orientation",
    "PageLayout",
    "PageLayoutError",
    "apply_to_docx_section",
]
