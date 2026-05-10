# FILE: src/mint_python/adapters/markdown.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Convert markdown text into an ArticleSpec dataclass deterministically.
#     Uses markdown-it-py 4.2 with CommonMark + table + strikethrough enabled,
#     then walks the SyntaxTreeNode AST emitting typed blocks (paragraph /
#     callout / list / table / code) and SpecSections. Closes the
#     embarrassing 0.4.0a1 baseline_report regression where a non-frontier
#     LLM serialized markdown tables as parallel paragraphs.
#   SCOPE: Public surface = markdown_to_spec(md_text, *, title_override) ->
#     ArticleSpec; MarkdownAdapterError, MD_ADAPTER_EMPTY_INPUT,
#     MD_ADAPTER_NO_USABLE_CONTENT.
#   DEPENDS: markdown-it-py (4.2, MIT, pure-python, GitHub-aligned parser);
#     tools.article_experiment.spec (ArticleSpec + block dataclasses).
#   LINKS: docs/development-plan.xml#MP-MD-ADAPTER,
#     docs/verification-plan.xml#V-MP-MD-ADAPTER,
#     docs/knowledge-graph.xml#MP-MD-ADAPTER
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   markdown_to_spec               - main entry; md_text -> ArticleSpec
#   MarkdownAdapterError           - base error
#   MD_ADAPTER_EMPTY_INPUT         - empty input error code (alias)
#   MD_ADAPTER_NO_USABLE_CONTENT   - no extractable blocks error code (alias)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — initial Phase-13 step-1 implementation per
#     V-MP-MD-ADAPTER scenarios 1-11.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from tools.article_experiment.spec import (
    ArticleSpec,
    Block,
    CalloutBlock,
    CodeBlock,
    ListBlock,
    ListKind,
    ParagraphBlock,
    SpecSection,
    TableBlock,
)

logger = logging.getLogger(__name__)


class MarkdownAdapterError(ValueError):
    """Base error for the markdown adapter — empty / no-content / shape errors."""


# Error code aliases keep the public surface stable while the adapter is a
# subclass of ValueError for caller convenience.
MD_ADAPTER_EMPTY_INPUT = "MD_ADAPTER_EMPTY_INPUT"
MD_ADAPTER_NO_USABLE_CONTENT = "MD_ADAPTER_NO_USABLE_CONTENT"


# Markdown-it preset: CommonMark base + GFM tables + strikethrough. We
# deliberately avoid the "gfm-like" preset because it enables linkify
# (which pulls in the linkify-it dependency we don't want). The features
# we actually use — tables, fenced code, blockquotes, lists, emphasis —
# are all in the CommonMark base + table extension.
def _build_parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table").enable("strikethrough")


def markdown_to_spec(
    md_text: str,
    *,
    title_override: str | None = None,
) -> ArticleSpec:
    """Convert markdown text to an ArticleSpec dataclass.

    Args:
        md_text: GitHub-Flavored Markdown source. Tolerant to malformed
            input via markdown-it-py's recovery rules (unbalanced fences,
            mismatched table rows, etc. fall through to text instead of
            raising).
        title_override: When provided, becomes ArticleSpec.title regardless
            of the document's first heading. When omitted, the first H1
            text is used; if no H1 exists, a fallback title "Untitled" is
            used.

    Returns:
        ArticleSpec with sections + blocks extracted from the markdown.

    Raises:
        MarkdownAdapterError: When the input is empty or contains no
            extractable blocks.
    """
    if not md_text:
        raise MarkdownAdapterError(
            f"{MD_ADAPTER_EMPTY_INPUT}: input markdown is empty"
        )
    if not md_text.strip():
        raise MarkdownAdapterError(
            f"{MD_ADAPTER_NO_USABLE_CONTENT}: input contains only whitespace"
        )

    parser = _build_parser()
    tokens = parser.parse(md_text)
    root = SyntaxTreeNode(tokens)

    sections, derived_title = _walk_root(root)

    if not sections:
        raise MarkdownAdapterError(
            f"{MD_ADAPTER_NO_USABLE_CONTENT}: no extractable blocks found"
        )

    title = title_override if title_override else (derived_title or "Untitled")

    block_count_by_type: dict[str, int] = {}
    for section in sections:
        for block in section.blocks:
            kind = type(block).__name__.replace("Block", "").lower()
            block_count_by_type[kind] = block_count_by_type.get(kind, 0) + 1

    # START_BLOCK_PARSE_MD
    logger.info(
        "[MP-MdAdapter][parse][BLOCK_PARSE_MD] "
        "token_count=%d section_count=%d block_count_by_type=%s "
        "has_title_override=%s",
        len(tokens),
        len(sections),
        block_count_by_type,
        title_override is not None,
    )
    # END_BLOCK_PARSE_MD

    return ArticleSpec(title=title, sections=tuple(sections))


# --------------------------------------------------------------------------- #
# Walker — root → SpecSection list
# --------------------------------------------------------------------------- #


def _walk_root(root: SyntaxTreeNode) -> tuple[list[SpecSection], str | None]:
    """Walk the AST root, grouping content under headings into SpecSections.

    Returns (sections, derived_title). derived_title is the first H1 text if
    any, else None. Pre-heading content is collected into an implicit lead
    section titled "Introduction".
    """
    sections: list[SpecSection] = []
    derived_title: str | None = None

    current_title: str | None = None
    current_level: int = 1
    current_blocks: list[Block] = []

    def flush() -> None:
        if current_blocks or current_title:
            sections.append(
                SpecSection(
                    title=current_title or "Introduction",
                    blocks=tuple(current_blocks),
                    level=min(current_level, 3) if current_title else 1,
                )
            )

    for node in root.children:
        if node.type == "heading":
            level = int(node.tag[1]) if node.tag and node.tag.startswith("h") else 1
            heading_text = _inline_text(node)
            if level == 1 and derived_title is None:
                derived_title = heading_text
            # Flush pending section before opening a new one.
            flush()
            current_title = heading_text
            current_level = level
            current_blocks = []
            continue

        block = _node_to_block(node)
        if block is not None:
            current_blocks.append(block)

    flush()
    # Drop empty sections (e.g. a trailing heading with no content).
    sections = [s for s in sections if s.blocks]
    return sections, derived_title


# --------------------------------------------------------------------------- #
# Block dispatch — single AST node → Block | None
# --------------------------------------------------------------------------- #


def _node_to_block(node: SyntaxTreeNode) -> Block | None:
    if node.type == "paragraph":
        return _paragraph_to_block(node)
    if node.type == "fence":
        return CodeBlock(content=node.content, language=(node.info or "").strip())
    if node.type == "code_block":  # indented code block (4-space indent)
        return CodeBlock(content=node.content, language="")
    if node.type == "blockquote":
        body = "\n".join(_inline_text(p) for p in node.children if p.type == "paragraph")
        if not body:  # pragma: no cover — defensive against AST without inner paragraphs
            return None
        return CalloutBlock(body=body, kind="info")
    if node.type == "table":
        return _table_to_block(node)
    if node.type == "bullet_list":
        return _list_to_block(node, ordered=False)
    if node.type == "ordered_list":
        return _list_to_block(node, ordered=True)
    # Unknown / unsupported block-level types (hr, html_block, …) are dropped
    # — the walker prefers losing decorative elements over crashing.
    return None


def _paragraph_to_block(node: SyntaxTreeNode) -> ParagraphBlock | None:
    text, emphasis = _inline_text_and_emphasis(node)
    if not text.strip():  # pragma: no cover — markdown-it-py doesn't emit empty paragraphs
        return None
    return ParagraphBlock(text=text, emphasis=tuple(emphasis))


def _table_to_block(node: SyntaxTreeNode) -> TableBlock | None:
    header: tuple[str, ...] = ()
    rows: list[tuple[str, ...]] = []
    for child in node.children:
        if child.type == "thead":
            for tr in child.children:
                if tr.type == "tr":
                    header = tuple(_inline_text(cell) for cell in tr.children)
        elif child.type == "tbody":
            for tr in child.children:
                if tr.type == "tr":
                    rows.append(tuple(_inline_text(cell) for cell in tr.children))
    if not header and not rows:  # pragma: no cover — markdown-it-py always emits thead+tbody
        return None
    return TableBlock(header=header, rows=tuple(rows))


def _list_to_block(node: SyntaxTreeNode, *, ordered: bool) -> ListBlock | None:
    items: list[str] = []
    is_checklist = False
    for li in node.children:
        # Defensive: markdown-it-py only emits list_item children under
        # bullet_list/ordered_list. Skipped silently if anything else.
        if li.type != "list_item":  # pragma: no cover
            continue
        text = _inline_text(li).strip()
        # GFM task-list detection — bullet items whose text starts with "[ ]"
        # or "[x]" are checklist items. We strip the marker before storing.
        if not ordered and (
            text.startswith("[ ]") or text.lower().startswith("[x]")
        ):
            is_checklist = True
            text = text[3:].lstrip()
        if text:
            items.append(text)
    if not items:  # pragma: no cover — defensive; tests cover non-empty lists, empty lists are rare
        return None
    if is_checklist:
        kind: ListKind = "checklist"
    elif ordered:
        kind = "numbered"
    else:
        kind = "bullet"
    return ListBlock(items=tuple(items), kind=kind)


# --------------------------------------------------------------------------- #
# Inline helpers — collect text + emphasis from inline nodes
# --------------------------------------------------------------------------- #


def _inline_text(node: SyntaxTreeNode) -> str:
    """Concatenate all text-like content under a node, ignoring formatting."""
    parts: list[str] = []
    _collect_text(node, parts)
    return "".join(parts).strip()


def _collect_text(node: SyntaxTreeNode, out: list[str]) -> None:
    if node.type == "text":
        out.append(node.content)
        return
    if node.type == "softbreak":
        out.append(" ")
        return
    if node.type == "hardbreak":
        out.append("\n")
        return
    if node.type == "code_inline":
        out.append(node.content)
        return
    for child in node.children:
        _collect_text(child, out)


def _inline_text_and_emphasis(node: SyntaxTreeNode) -> tuple[str, list[str]]:
    """Return (full_text, emphasis_list). emphasis_list captures the literal
    substrings that were rendered bold (strong) or italic (em) in the source.

    The walker descends through all inline children, accumulating text and
    recording each strong/em segment's text content as an emphasis entry.
    Substrings appear in document order; duplicates allowed (e.g. the same
    word emphasized twice).
    """
    parts: list[str] = []
    emphasis: list[str] = []
    _walk_inline(node, parts, emphasis)
    return "".join(parts).strip(), emphasis


def _walk_inline(
    node: SyntaxTreeNode,
    text_out: list[str],
    emphasis_out: list[str],
) -> None:
    if node.type == "text":
        text_out.append(node.content)
        return
    if node.type == "softbreak":
        text_out.append(" ")
        return
    if node.type == "hardbreak":
        text_out.append("\n")
        return
    if node.type == "code_inline":
        text_out.append(node.content)
        return
    if node.type in ("strong", "em"):
        sub_parts: list[str] = []
        for child in node.children:
            _walk_inline(child, sub_parts, emphasis_out)
        emphasized = "".join(sub_parts)
        text_out.append(emphasized)
        if emphasized.strip():
            emphasis_out.append(emphasized)
        return
    for child in node.children:
        _walk_inline(child, text_out, emphasis_out)


__all__ = [
    "MD_ADAPTER_EMPTY_INPUT",
    "MD_ADAPTER_NO_USABLE_CONTENT",
    "MarkdownAdapterError",
    "markdown_to_spec",
]


# Keep `Any` referenced so mypy doesn't complain about unused-import on the
# stub-typed markdown_it API where needed.
_: Any = None
