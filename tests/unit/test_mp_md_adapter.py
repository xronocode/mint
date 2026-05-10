# FILE: tests/unit/test_mp_md_adapter.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-MD-ADAPTER verification — covers scenarios 1-11 of the
#     markdown adapter against the 7 fixtures in tests/fixtures/md_adapter/.
#   SCOPE: Unit tests only — pure-function contract, no LLM, no IO beyond
#     fixture reads. Round-trip integration with MP-DOCUMENT is asserted
#     via scenario-8 (build_document_from_spec + lenient validation).
#   DEPENDS: pytest, mint_python.adapters.markdown,
#     tools.article_experiment.spec, tools.article_experiment.builder
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from tools.article_experiment.spec import (
    ArticleSpec,
    CalloutBlock,
    CodeBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)

from mint_python.adapters.markdown import (
    MD_ADAPTER_EMPTY_INPUT,
    MD_ADAPTER_NO_USABLE_CONTENT,
    MarkdownAdapterError,
    markdown_to_spec,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "md_adapter"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# scenario-1: heading depth → SpecSection levels
# --------------------------------------------------------------------------- #


def test_scenario_1_heading_depth_maps_to_section_level() -> None:
    md = "# H1\nbody under H1\n\n## H2\nbody under H2\n\n### H3\nbody under H3\n"
    spec = markdown_to_spec(md)
    assert isinstance(spec, ArticleSpec)
    assert spec.title == "H1"
    levels = [s.level for s in spec.sections]
    assert levels == [1, 2, 3]


def test_scenario_1_title_override_wins_over_first_h1() -> None:
    md = "# Original Title\nbody\n"
    spec = markdown_to_spec(md, title_override="Forced Title")
    assert spec.title == "Forced Title"


def test_scenario_1_title_fallback_when_no_h1() -> None:
    md = "Just a paragraph, no headings at all.\n"
    spec = markdown_to_spec(md)
    assert spec.title == "Untitled"


# --------------------------------------------------------------------------- #
# scenario-2: GFM table parses to TableBlock
# --------------------------------------------------------------------------- #


def test_scenario_2_gfm_table_to_table_block() -> None:
    spec = markdown_to_spec(_read("gfm_table_alignment.md"))
    tables = [b for s in spec.sections for b in s.blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    assert tables[0].header == ("Left", "Center", "Right")
    assert tables[0].rows == (("a", "b", "c"), ("d", "e", "f"))


# --------------------------------------------------------------------------- #
# scenario-3: fenced code → CodeBlock
# --------------------------------------------------------------------------- #


def test_scenario_3_fenced_code_to_code_block() -> None:
    md = "# x\n\n```python\nx = 1\n```\n"
    spec = markdown_to_spec(md)
    codes = [b for s in spec.sections for b in s.blocks if isinstance(b, CodeBlock)]
    assert len(codes) == 1
    assert codes[0].language == "python"
    assert codes[0].content == "x = 1\n"


# --------------------------------------------------------------------------- #
# scenario-4: blockquote → CalloutBlock(kind="info")
# --------------------------------------------------------------------------- #


def test_scenario_4_blockquote_to_info_callout() -> None:
    md = "# x\n\n> a quoted note\n"
    spec = markdown_to_spec(md)
    callouts = [b for s in spec.sections for b in s.blocks if isinstance(b, CalloutBlock)]
    assert len(callouts) == 1
    assert callouts[0].kind == "info"
    assert "quoted note" in callouts[0].body


# --------------------------------------------------------------------------- #
# scenario-5: list kinds (bullet / numbered / checklist)
# --------------------------------------------------------------------------- #


def test_scenario_5_bullet_list() -> None:
    md = "# x\n\n- one\n- two\n"
    spec = markdown_to_spec(md)
    lists = [b for s in spec.sections for b in s.blocks if isinstance(b, ListBlock)]
    assert len(lists) == 1
    assert lists[0].kind == "bullet"
    assert lists[0].items == ("one", "two")


def test_scenario_5_numbered_list() -> None:
    md = "# x\n\n1. one\n2. two\n"
    spec = markdown_to_spec(md)
    lists = [b for s in spec.sections for b in s.blocks if isinstance(b, ListBlock)]
    assert len(lists) == 1
    assert lists[0].kind == "numbered"


def test_scenario_5_checklist() -> None:
    md = "# x\n\n- [ ] todo\n- [x] done\n"
    spec = markdown_to_spec(md)
    lists = [b for s in spec.sections for b in s.blocks if isinstance(b, ListBlock)]
    assert len(lists) == 1
    assert lists[0].kind == "checklist"
    # The "[ ]" / "[x]" prefix is stripped from the items.
    assert lists[0].items == ("todo", "done")


# --------------------------------------------------------------------------- #
# scenario-6: inline emphasis captured in ParagraphBlock.emphasis
# --------------------------------------------------------------------------- #


def test_scenario_6_inline_emphasis_recorded() -> None:
    md = "# x\n\nA paragraph with **bold phrase** and *italic word*.\n"
    spec = markdown_to_spec(md)
    paras = [b for s in spec.sections for b in s.blocks if isinstance(b, ParagraphBlock)]
    assert len(paras) == 1
    # Emphasis substrings appear verbatim, in document order.
    assert paras[0].emphasis == ("bold phrase", "italic word")
    # Full text reconstructs the original prose without markup characters.
    assert "bold phrase" in paras[0].text
    assert "italic word" in paras[0].text
    assert "**" not in paras[0].text  # markup stripped


def test_scenario_6_nested_emphasis_fixture() -> None:
    spec = markdown_to_spec(_read("nested_emphasis.md"))
    paras = [b for s in spec.sections for b in s.blocks if isinstance(b, ParagraphBlock)]
    assert paras
    all_emphasis = [e for p in paras for e in p.emphasis]
    # bold + italic + bold-italic combo + the single-word emphases all show up.
    assert "bold word" in all_emphasis
    assert "italic word" in all_emphasis
    assert "MINT" in all_emphasis
    assert "runs" in all_emphasis


def test_inline_softbreak_hardbreak_codeinline_in_paragraph() -> None:
    """Cover the softbreak / hardbreak / code_inline branches of _walk_inline."""
    # Softbreak — single \n inside a paragraph between text lines.
    # Hardbreak — line ending with two trailing spaces.
    # Code inline — `backtick`.
    md = (
        "# x\n\n"
        "first line\n"
        "second line after softbreak.\n\n"
        "trailing-space  \nhardbreak after.\n\n"
        "the symbol `code_inline` should round-trip.\n"
    )
    spec = markdown_to_spec(md)
    paras = [b for s in spec.sections for b in s.blocks if isinstance(b, ParagraphBlock)]
    assert len(paras) == 3
    # Softbreak collapses to a space.
    assert "first line second line" in paras[0].text
    # Hardbreak inserts a newline.
    assert "\n" in paras[1].text
    # Code inline content survives.
    assert "code_inline" in paras[2].text


def test_inline_text_helpers_handle_softbreak_hardbreak_codeinline() -> None:
    """Cover softbreak / hardbreak / code_inline branches of _collect_text.

    _collect_text runs in non-paragraph contexts (heading text, blockquote
    body, table cell, list item). The most direct trigger: a list item
    whose content has a softbreak (continuation line), a hardbreak (two
    trailing spaces), and an inline code span — _collect_text must handle
    all three node types without dropping content.
    """
    md = (
        "# x\n\n"
        "- item with `code_in_li` inline\n"
        "  continuation softbreak\n"
        "- next item\n"
    )
    spec = markdown_to_spec(md)
    lists = [b for s in spec.sections for b in s.blocks if isinstance(b, ListBlock)]
    assert lists
    items_text = " ".join(lists[0].items)
    # code_inline content survives.
    assert "code_in_li" in items_text
    # softbreak collapses to a space — continuation merges with the prior line.
    assert "continuation softbreak" in items_text


def test_collect_text_handles_hardbreak_in_list_item() -> None:
    """Hardbreaks (two-trailing-spaces line break) inside a list item route
    through _collect_text's hardbreak branch — list items use _inline_text
    (not _walk_inline), so this is the trigger that exercises the helper's
    hardbreak handling."""
    md = "# x\n\n- first part  \n  second part after hardbreak\n- next item\n"
    spec = markdown_to_spec(md)
    lists = [b for s in spec.sections for b in s.blocks if isinstance(b, ListBlock)]
    assert lists
    # Hardbreak inserts a literal newline; both halves of the item survive.
    first_item = lists[0].items[0]
    assert "first part" in first_item
    assert "second part" in first_item


def test_indented_code_block_to_code_block() -> None:
    """Indented code block (4-space indent) → CodeBlock(language='')."""
    md = "# x\n\nintro\n\n    indented_code = True\n    return indented_code\n"
    spec = markdown_to_spec(md)
    codes = [b for s in spec.sections for b in s.blocks if isinstance(b, CodeBlock)]
    assert len(codes) == 1
    assert codes[0].language == ""
    assert "indented_code" in codes[0].content


def test_unknown_block_type_dropped() -> None:
    """Horizontal rules and similar decorative block-level nodes are dropped
    silently; the walker prefers losing decoration to crashing."""
    md = "# x\n\nbefore\n\n---\n\nafter\n"
    spec = markdown_to_spec(md)
    # Both paragraphs survive even though hr was between them.
    paras = [b for s in spec.sections for b in s.blocks if isinstance(b, ParagraphBlock)]
    assert len(paras) == 2


# --------------------------------------------------------------------------- #
# scenario-7: malformed input — graceful recovery
# --------------------------------------------------------------------------- #


def test_scenario_7_unbalanced_fence_recovers() -> None:
    spec = markdown_to_spec(_read("unbalanced_fence.md"))
    codes = [b for s in spec.sections for b in s.blocks if isinstance(b, CodeBlock)]
    assert len(codes) == 1
    # markdown-it-py runs the fence to EOF — content should include the
    # trailing function body since the closing ``` is missing.
    assert "never_closes" in codes[0].content


# --------------------------------------------------------------------------- #
# scenario-8: round-trip md → spec → docx → lenient validation
# --------------------------------------------------------------------------- #


def test_scenario_8_round_trip_lenient_validation_passes(tmp_path: Path) -> None:
    from tools.article_experiment.builder import build_document_from_spec

    spec = markdown_to_spec(_read("sample_article.md"))
    doc = build_document_from_spec(spec)
    out = tmp_path / "round_trip.docx"
    doc.save(out)
    assert out.exists()
    report = doc.validate(level="lenient")
    assert report.passed
    assert report.hard_count == 0


# --------------------------------------------------------------------------- #
# scenario-9: REPORT_COMPARISON regression — tables stay tables
# --------------------------------------------------------------------------- #


def test_scenario_9_report_comparison_regression(tmp_path: Path) -> None:
    """The motivating bug: a 4B model serialized markdown tables as parallel
    paragraphs in 0.4.0a1's baseline_report.docx. With the deterministic
    adapter, the same input must produce ACTUAL `<w:tbl>` elements."""
    import zipfile

    from tools.article_experiment.builder import build_document_from_spec

    spec = markdown_to_spec(_read("report_comparison_excerpt.md"))
    # The fixture contains exactly 3 markdown tables.
    tables = [b for s in spec.sections for b in s.blocks if isinstance(b, TableBlock)]
    assert len(tables) >= 3, (
        f"adapter must extract all 3 tables from the fixture, got {len(tables)}"
    )

    doc = build_document_from_spec(spec)
    out = tmp_path / "report_excerpt.docx"
    doc.save(out)
    with zipfile.ZipFile(out) as z:
        document_xml = z.read("word/document.xml")
    # Count actual w:tbl elements — proves tables didn't degrade to paragraphs.
    tbl_count = document_xml.count(b"<w:tbl>")
    assert tbl_count >= 3, (
        f"saved docx must contain >= 3 actual <w:tbl> elements, got {tbl_count} "
        "(the 0.4.0a1 LLM-flattened-table regression has returned)"
    )


# --------------------------------------------------------------------------- #
# scenario-10: empty / whitespace-only inputs raise
# --------------------------------------------------------------------------- #


def test_scenario_10_empty_input_raises() -> None:
    with pytest.raises(MarkdownAdapterError, match=MD_ADAPTER_EMPTY_INPUT):
        markdown_to_spec("")


def test_scenario_10_whitespace_only_raises() -> None:
    with pytest.raises(MarkdownAdapterError, match=MD_ADAPTER_NO_USABLE_CONTENT):
        markdown_to_spec(_read("whitespace_only.md"))


def test_scenario_10_no_extractable_blocks_raises() -> None:
    """A document with only a heading (no body content) yields zero
    sections after the empty-section trim, so it raises NO_USABLE_CONTENT."""
    with pytest.raises(MarkdownAdapterError, match=MD_ADAPTER_NO_USABLE_CONTENT):
        markdown_to_spec("# heading-only\n")


# --------------------------------------------------------------------------- #
# scenario-11: BLOCK_PARSE_MD log marker fires once with the documented payload
# --------------------------------------------------------------------------- #


def test_scenario_11_block_parse_md_marker_fires_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    markdown_to_spec(_read("sample_article.md"))
    msgs = [r.getMessage() for r in caplog.records if "BLOCK_PARSE_MD" in r.getMessage()]
    assert len(msgs) == 1, f"expected 1 BLOCK_PARSE_MD emission, got {len(msgs)}"
    msg = msgs[0]
    # Payload schema per V-MP-MD-ADAPTER trace-contract.
    assert "[MP-MdAdapter][parse][BLOCK_PARSE_MD]" in msg
    assert "token_count=" in msg
    assert "section_count=" in msg
    assert "block_count_by_type=" in msg
    assert "has_title_override=" in msg


def test_scenario_11_no_other_prefix_emissions_during_parse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Forbidden cross-talk: only [MP-MdAdapter] prefix in INFO records during
    a markdown_to_spec call. Walker is silent at sub-block level."""
    caplog.set_level(logging.INFO)
    markdown_to_spec(_read("sample_article.md"))
    other_prefix = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO
        and "MP-MdAdapter" not in r.getMessage()
        # Allow framework noise that has no [Module][fn][BLOCK_*] shape
        and "[" in r.getMessage()
    ]
    assert not other_prefix, (
        f"forbidden cross-talk: non-MP-MdAdapter INFO emissions: {other_prefix}"
    )


# --------------------------------------------------------------------------- #
# scenario-11 payload field-set sanity (deterministic structure)
# --------------------------------------------------------------------------- #


def test_block_count_by_type_payload_keys() -> None:
    """The block_count_by_type dict in BLOCK_PARSE_MD payload should only carry
    the documented block-kind keys: paragraph / table / callout / list / code."""
    import logging
    import re
    caplog_records = []

    class CapHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            caplog_records.append(record.getMessage())

    handler = CapHandler()
    logger = logging.getLogger("mint_python.adapters.markdown")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        markdown_to_spec(_read("sample_article.md"))
    finally:
        logger.removeHandler(handler)

    parse_lines = [m for m in caplog_records if "BLOCK_PARSE_MD" in m]
    assert parse_lines
    # Extract block_count_by_type={...} payload fragment.
    m = re.search(r"block_count_by_type=(\{[^}]*\})", parse_lines[0])
    assert m, parse_lines[0]
    keys_in_payload = set(re.findall(r"'(\w+)'", m.group(1)))
    allowed = {"paragraph", "table", "callout", "list", "code"}
    assert keys_in_payload <= allowed, (
        f"unexpected keys in block_count_by_type: {keys_in_payload - allowed}"
    )
