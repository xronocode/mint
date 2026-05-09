# FILE: tools/article_experiment/builder.py
# VERSION: 0.1.0
"""Spec → klawd-themed Document builder.

Takes an ArticleSpec produced by the LLM (and parsed by spec.parse_spec)
and returns a fully-rendered Document. All visual decisions — colors,
fonts, spacing, page layout, callout styling — live here, not in the
LLM's output. This is the load-bearing piece of the MINT thesis.

Public surface: build_document_from_spec(spec) -> Document.
"""
from __future__ import annotations

from mint_python.core.content import Paragraph
from mint_python.sdk import (
    Callout,
    CalloutKind,
    Document,
    List,
    ListKind,
    Margins,
    PageLayout,
    Section,
    Table,
)

from .spec import (
    ArticleSpec,
    Block,
    CalloutBlock,
    CodeBlock,
    ListBlock,
    ParagraphBlock,
    SectionLayout,
    SpecSection,
    TableBlock,
)

_CALLOUT_KIND_MAP = {
    "info": CalloutKind.INFO,
    "warning": CalloutKind.WARNING,
    "code": CalloutKind.CODE,
}

_LIST_KIND_MAP = {
    "bullet": ListKind.BULLET,
    "numbered": ListKind.NUMBERED,
    "checklist": ListKind.CHECKLIST,
}


def _paragraph_with_emphasis(block: ParagraphBlock) -> Paragraph:
    """Render a paragraph, bolding any emphasized substrings.

    Naive str.find pass: each emphasis phrase is found left-to-right and
    the surrounding text is split into runs. Phrases not found in the
    text are silently skipped — the model's output may not survive a
    round-trip through markdown perfectly, and we'd rather render a
    plain paragraph than crash.
    """
    if not block.emphasis:
        return Paragraph(block.text)

    text = block.text
    cuts: list[tuple[int, int]] = []
    cursor = 0
    for phrase in block.emphasis:
        if not phrase:
            continue
        idx = text.find(phrase, cursor)
        if idx == -1:
            continue
        cuts.append((idx, idx + len(phrase)))
        cursor = idx + len(phrase)

    if not cuts:
        return Paragraph(block.text)

    para = Paragraph()
    pos = 0
    for start, end in cuts:
        if start > pos:
            para.add_run(text[pos:start])
        para.add_run(text[start:end], bold=True)
        pos = end
    if pos < len(text):
        para.add_run(text[pos:])
    return para


def _add_block(section: Section, block: Block) -> None:
    if isinstance(block, ParagraphBlock):
        section.add_paragraph(_paragraph_with_emphasis(block))
    elif isinstance(block, CalloutBlock):
        section.add_callout(
            Callout(
                block.body,
                kind=_CALLOUT_KIND_MAP[block.kind],
                title=block.title or None,
            )
        )
    elif isinstance(block, ListBlock):
        section.add_list(
            List(items=list(block.items), kind=_LIST_KIND_MAP[block.kind])
        )
    elif isinstance(block, TableBlock):
        rows: list[list[str]] = []
        if block.header:
            rows.append(list(block.header))
        rows.extend(list(r) for r in block.rows)
        if rows:
            # from_list demands rectangularity; pad/trim short rows to the
            # header width so a flaky model's misshapen table still renders.
            width = len(rows[0])
            normalized = [
                (row + [""] * (width - len(row)))[:width] for row in rows
            ]
            section.add_table(
                Table.from_list(normalized, header=bool(block.header))
            )
    elif isinstance(block, CodeBlock):
        section.add_callout(
            Callout(
                block.content,
                kind=CalloutKind.CODE,
                title=block.language or None,
            )
        )


def _section_with_layout(spec_section: SpecSection) -> Section:
    section = Section(
        title=spec_section.title,
        level=min(spec_section.level, 3),
    )
    if spec_section.layout is not None:
        section = section.with_page_layout(_layout_to_page_layout(spec_section.layout))
    for block in spec_section.blocks:
        _add_block(section, block)
    return section


def _layout_to_page_layout(layout: SectionLayout) -> PageLayout:
    margins = (
        Margins(top=0.75, bottom=0.75, left=0.75, right=0.75)
        if layout.orientation == "landscape"
        else Margins()
    )
    return PageLayout(
        orientation=layout.orientation,
        margins=margins,
        columns=max(1, min(layout.columns, 4)),
        header=layout.header,
        footer=layout.footer,
    )


def build_document_from_spec(spec: ArticleSpec) -> Document:
    """Spec → klawd-themed Document. Pure function over the spec dataclass.

    Always applies the klawd preset, always adds a TOC, always renders the
    cover from spec.title + spec.subtitle. The LLM has no say in any of
    these visual choices — only in WHAT the document is about.
    """
    doc = Document(format="docx", title=spec.title).with_style_preset("klawd")
    subtitle = spec.subtitle or spec.meta.get("subtitle", "")
    doc.add_cover(title=spec.title, subtitle=subtitle or None)
    doc.add_toc(max_level=2)
    for spec_section in spec.sections:
        doc.add_section(_section_with_layout(spec_section))
    return doc
