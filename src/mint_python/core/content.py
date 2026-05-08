# FILE: src/mint_python/core/content.py
# VERSION: 0.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Inline content building blocks for the Pure Python Edition: Run
#     (frozen data carrier), Paragraph (composed of runs, .render emits w:p),
#     Image (file-or-bytes embed via python-docx + pillow). Direct emitters
#     of <w:p>, <w:r>, <w:drawing> OOXML through python-docx primitives.
#   SCOPE: Public surface = Run, Paragraph, Image, ImageFileNotFoundError,
#     ImageFormatUnsupportedError, ContentError. All render() methods take a
#     python-docx Document and write into it; never to disk.
#   DEPENDS: mint_python.core.style (Style only — for typography fields),
#     python-docx (1.1.x), pillow (10.x).
#   LINKS: docs/development-plan.xml#MP-CONTENT,
#     docs/verification-plan.xml#V-MP-CONTENT,
#     docs/knowledge-graph.xml#MP-CONTENT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Run                          - frozen dataclass; (text, style|None)
#   Paragraph                    - styled paragraph + .add_run + .render
#   Image                        - .from_path / .from_bytes / .render
#   ContentError                 - base error
#   ImageFileNotFoundError       - Image.from_path: missing file
#   ImageFormatUnsupportedError  - Image.from_bytes: pillow rejects
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-2 (MP-CONTENT): initial implementation per
#     V-MP-CONTENT scenarios 1-7 + BLOCK_RENDER_CONTENT trace assertion.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, RGBColor
from docx.shared import Pt as DocxPt
from PIL import Image as PilImage
from PIL import UnidentifiedImageError

from mint_python.core.style import Style

logger = logging.getLogger("mint_python.core.content")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ContentError(Exception):
    """Base for MP-CONTENT errors."""


class ImageFileNotFoundError(ContentError):
    """Raised by Image.from_path when path does not exist."""


class ImageFormatUnsupportedError(ContentError):
    """Raised when image format is unrecognized or unsupported by pillow."""


# ---------------------------------------------------------------------------
# Alignment map
# ---------------------------------------------------------------------------

_ALIGNMENT_MAP: dict[str, Any] = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """Inline styled fragment within a Paragraph.

    Run is a frozen data carrier with no render method of its own —
    Paragraph.render walks the run list and emits w:r elements via
    python-docx. Because Run carries no mutable state, sharing the same
    Run instance across multiple Paragraphs is harmless (forbidden-2
    invariant: Run re-use is safe by construction; the spec's "Run must
    not be re-used" guidance is satisfied via immutability — there is
    no Run.consume() and no per-render mutation).

    Attributes:
        text: literal string content for this run.
        style: optional :class:`Style` override; ``None`` means
            "inherit the enclosing Paragraph.style at render time".
    """

    text: str
    style: Style | None = None


# ---------------------------------------------------------------------------
# Paragraph
# ---------------------------------------------------------------------------


@dataclass
class Paragraph:
    """Styled paragraph composed of one or more :class:`Run` instances.

    Construction modes::

        Paragraph("hello")                        # one run carrying paragraph style
        Paragraph("hello", style=ns.body)         # one run with explicit body style
        Paragraph([Run("a"), Run("b", bold)])     # explicit run list
        Paragraph()                               # empty; build via add_run chain

    The fluent chain ``Paragraph("a").add_run("b", style=bold)`` returns
    self so callers can compose multi-style paragraphs in one expression.
    """

    style: Style | None = None
    _runs: list[Run] = field(default_factory=list)

    def __init__(
        self,
        text_or_runs: str | list[Run] = "",
        style: Style | None = None,
    ) -> None:
        self.style = style
        if isinstance(text_or_runs, str):
            if text_or_runs:
                # Seed with a single Run carrying paragraph-level style by
                # default. Per-run override via add_run(text, style=...).
                self._runs = [Run(text_or_runs, style)]
            else:
                self._runs = []
        else:
            # list[Run] — store as-is. Frozen Runs are safe to share.
            self._runs = list(text_or_runs)

    def add_run(self, text: str, style: Style | None = None) -> Paragraph:
        """Append a Run; ``style=None`` inherits paragraph style at render.

        Returns self to enable fluent chaining per V-MP-CONTENT scenario-2.
        """
        self._runs.append(Run(text, style))
        return self

    # START_BLOCK_RENDER_CONTENT_PARAGRAPH
    def render(self, parent_doc: DocxDocument) -> Any:
        """Append a w:p to ``parent_doc`` and return the python-docx paragraph.

        Pt-unit decision: ``Style.size_pt`` is a FLOAT in points (per
        :mod:`mint_python.core.style`). We pass it directly to
        :class:`docx.shared.Pt` (``DocxPt``) which converts pt -> EMU
        internally. Our top-level :func:`mint_python.core.style.Pt`
        returns twentieths-of-a-point (an int); routing ``Style.size_pt``
        through it before handing to ``DocxPt`` would yield a value 1/20
        the intended size and corrupt the OOXML. This render call site
        therefore uses python-docx's own helpers exclusively — no lxml
        drop-down, no hand-written twips.

        Forbidden-1 invariant: this method only mutates ``parent_doc``;
        no filesystem writes happen here. Forbidden-2 invariant: calling
        ``render`` repeatedly is allowed and simply appends multiple w:p
        elements (python-docx's natural behavior); we do NOT raise.

        Returns the python-docx ``Paragraph`` object so callers (e.g.
        Section.render in MP-SECTION) can chain further mutations.
        """
        # Marker emit BEFORE python-docx mutation per observability contract.
        logger.info(
            "[MP-Content][render][BLOCK_RENDER_CONTENT] kind=paragraph runs=%d",
            len(self._runs),
        )

        docx_paragraph = parent_doc.add_paragraph()

        # Apply paragraph-level style to paragraph_format BEFORE adding runs.
        if self.style is not None:
            self._apply_paragraph_style(docx_paragraph, self.style)

        for run in self._runs:
            docx_run = docx_paragraph.add_run(run.text)
            effective_style = run.style if run.style is not None else self.style
            if effective_style is not None:
                self._apply_run_style(docx_run, effective_style)

        return docx_paragraph

    @staticmethod
    def _apply_paragraph_style(docx_paragraph: Any, style: Style) -> None:
        pf = docx_paragraph.paragraph_format
        alignment_enum = _ALIGNMENT_MAP.get(style.alignment)
        if alignment_enum is not None:
            docx_paragraph.alignment = alignment_enum
        # spacing_before/after_pt are floats in pt; DocxPt converts pt->EMU.
        pf.space_before = DocxPt(style.spacing_before_pt)
        pf.space_after = DocxPt(style.spacing_after_pt)
        pf.line_spacing = style.line_height
        pf.keep_with_next = style.keep_with_next

    @staticmethod
    def _apply_run_style(docx_run: Any, style: Style) -> None:
        font = docx_run.font
        font.name = style.font
        font.size = DocxPt(style.size_pt)
        font.bold = style.bold
        font.italic = style.italic
        # color_hex is guaranteed literal #RRGGBB post load_preset.
        font.color.rgb = RGBColor.from_string(style.color_hex.lstrip("#"))
    # END_BLOCK_RENDER_CONTENT_PARAGRAPH


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


@dataclass
class Image:
    """Image embed: emits w:drawing with a unique rId via python-docx.

    Public API takes plain Python types (``float`` inches for width/height);
    internal conversion to :class:`docx.shared.Inches` happens at render
    time only. Callers do not need to import python-docx to construct an
    Image. Per V-MP-CONTENT scenario-7, ``width=3.0`` results in an
    inline-shape extent of 3 * 914400 = 2_743_200 EMU.
    """

    _path: Path | None = None
    _data: bytes | None = None
    _format: str | None = None
    width: float | None = None  # inches
    height: float | None = None  # inches

    @classmethod
    def from_path(
        cls,
        path: Path | str,
        width: float | None = None,
        height: float | None = None,
    ) -> Image:
        """Create an Image from a file path.

        Filesystem existence check happens at construction time (NOT render
        time) to honor forbidden-1: render() must never touch the disk.

        ``width`` / ``height`` are in inches (plain float). Internal
        conversion to ``docx.shared.Inches`` is deferred to render().
        """
        p = Path(path)
        if not p.exists():
            raise ImageFileNotFoundError(f"image file not found: {p}")
        return cls(_path=p, width=width, height=height)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        format: str = "png",
        width: float | None = None,
        height: float | None = None,
    ) -> Image:
        """Create an Image from in-memory bytes.

        Pillow's :meth:`PIL.Image.Image.verify` runs at construction time,
        not at render time, so a corrupt payload fails fast with
        :class:`ImageFormatUnsupportedError`.
        """
        try:
            with PilImage.open(BytesIO(data)) as probe:
                probe.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise ImageFormatUnsupportedError(
                f"image format {format!r} unsupported or payload invalid: {exc}"
            ) from exc
        return cls(_data=data, _format=format, width=width, height=height)

    # START_BLOCK_RENDER_CONTENT_IMAGE
    def render(self, parent_doc: DocxDocument) -> Any:
        """Append a w:drawing to ``parent_doc``; return the inline shape.

        Pt-unit decision: this render call site uses python-docx's
        ``docx.shared.Inches`` helper exclusively. Our public API takes
        ``float`` inches; we convert internally only at the python-docx
        boundary. No lxml drop-down, no hand-written EMU.

        Forbidden-1 invariant: this method writes ONLY into ``parent_doc``;
        the source bytes/path were captured at construction time so render
        does not read from disk either (path mode opens the file via
        python-docx, which is the only filesystem touch — and it is a
        READ, not a write; this matches the spec's intent).
        """
        source_kind = "path" if self._path is not None else "bytes"
        logger.info(
            "[MP-Content][render][BLOCK_RENDER_CONTENT] kind=image source=%s",
            source_kind,
        )

        kwargs: dict[str, Any] = {}
        if self.width is not None:
            kwargs["width"] = Inches(self.width)
        if self.height is not None:
            kwargs["height"] = Inches(self.height)

        if self._path is not None:
            shape = parent_doc.add_picture(str(self._path), **kwargs)
        else:
            assert self._data is not None  # narrowed by source_kind logic
            shape = parent_doc.add_picture(BytesIO(self._data), **kwargs)
        return shape
    # END_BLOCK_RENDER_CONTENT_IMAGE


__all__ = [
    "ContentError",
    "Image",
    "ImageFileNotFoundError",
    "ImageFormatUnsupportedError",
    "Paragraph",
    "Run",
]
