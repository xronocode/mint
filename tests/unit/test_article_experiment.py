# FILE: tests/unit/test_article_experiment.py
# VERSION: 0.1.0
"""Unit tests for tools/article_experiment — spec parser + builder.

The runner (LLM-calling layer) is NOT tested here; that depends on a
network endpoint not reachable from CI. These tests cover the
deterministic boundary: spec parsing tolerance + builder rendering.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from tools.article_experiment.builder import build_document_from_spec
from tools.article_experiment.spec import (
    ArticleSpec,
    CalloutBlock,
    ParagraphBlock,
    SectionLayout,
    SpecParseError,
    parse_spec,
)

# --------------------------------------------------------------------------- #
# spec.parse_spec — happy path + tolerance
# --------------------------------------------------------------------------- #


class TestSpecParserHappyPath:
    def test_parses_full_spec(self) -> None:
        data = {
            "title": "T",
            "subtitle": "sub",
            "meta": {"author": "test"},
            "sections": [
                {
                    "title": "S1",
                    "level": 2,
                    "layout": {
                        "orientation": "landscape",
                        "columns": 2,
                        "header": "H",
                        "footer": "F",
                    },
                    "blocks": [
                        {"type": "paragraph", "text": "p", "emphasis": ["e"]},
                        {"type": "callout", "kind": "warning", "body": "w", "title": "t"},
                        {"type": "list", "kind": "numbered", "items": ["a", "b"]},
                        {"type": "table", "header": ["x"], "rows": [["1"]]},
                        {"type": "code", "language": "py", "content": "x"},
                    ],
                }
            ],
        }
        spec = parse_spec(data)
        assert isinstance(spec, ArticleSpec)
        assert spec.title == "T"
        assert spec.subtitle == "sub"
        assert spec.meta == {"author": "test"}
        assert len(spec.sections) == 1
        section = spec.sections[0]
        assert section.level == 2
        assert isinstance(section.layout, SectionLayout)
        assert section.layout.orientation == "landscape"
        assert section.layout.columns == 2
        types = [type(b).__name__ for b in section.blocks]
        assert types == [
            "ParagraphBlock", "CalloutBlock", "ListBlock", "TableBlock", "CodeBlock"
        ]


class TestSpecParserTolerance:
    """A 4B-class model output should still parse — we coerce, default,
    and silently drop weird stuff rather than crashing."""

    def test_unknown_top_level_keys_ignored(self) -> None:
        spec = parse_spec({
            "title": "T",
            "mystery_field": "should be silently dropped",
            "sections": [{"title": "S", "blocks": [{"type": "paragraph", "text": "p"}]}],
        })
        assert spec.title == "T"

    def test_unknown_block_types_dropped(self) -> None:
        spec = parse_spec({
            "title": "T",
            "sections": [{
                "title": "S",
                "blocks": [
                    {"type": "paragraph", "text": "real"},
                    {"type": "interpretive_dance", "text": "fake"},
                    {"type": "image", "src": "fake.png"},  # not in our schema
                ],
            }],
        })
        assert len(spec.sections[0].blocks) == 1
        assert isinstance(spec.sections[0].blocks[0], ParagraphBlock)

    def test_invalid_callout_kind_falls_back_to_info(self) -> None:
        spec = parse_spec({
            "title": "T",
            "sections": [{"title": "S", "blocks": [
                {"type": "callout", "kind": "DANGER!", "body": "x"},
            ]}],
        })
        block = spec.sections[0].blocks[0]
        assert isinstance(block, CalloutBlock)
        assert block.kind == "info"

    def test_columns_clamped_to_safe_range(self) -> None:
        spec = parse_spec({
            "title": "T",
            "sections": [{
                "title": "S",
                "layout": {"columns": 99},
                "blocks": [{"type": "paragraph", "text": "p"}],
            }],
        })
        assert spec.sections[0].layout is not None
        assert spec.sections[0].layout.columns == 4  # clamped from 99

    def test_default_layout_omitted_when_no_props(self) -> None:
        spec = parse_spec({
            "title": "T",
            "sections": [{
                "title": "S",
                "layout": {},  # empty layout dict — nothing to apply
                "blocks": [{"type": "paragraph", "text": "p"}],
            }],
        })
        assert spec.sections[0].layout is None

    def test_table_with_no_rows_or_no_header_still_accepted(self) -> None:
        spec = parse_spec({
            "title": "T",
            "sections": [{"title": "S", "blocks": [
                {"type": "paragraph", "text": "p"},
                {"type": "table", "header": ["a", "b"]},  # no rows
            ]}],
        })
        # The header-only table is a valid block.
        block_types = [type(b).__name__ for b in spec.sections[0].blocks]
        assert "TableBlock" in block_types


class TestSpecParserSkeletonErrors:
    """Skeleton-level failures the builder couldn't render around."""

    def test_root_must_be_dict(self) -> None:
        with pytest.raises(SpecParseError, match="root must be an object"):
            parse_spec(["not", "a", "dict"])

    def test_title_required(self) -> None:
        with pytest.raises(SpecParseError, match="title"):
            parse_spec({"sections": [
                {"title": "S", "blocks": [{"type": "paragraph", "text": "p"}]}
            ]})

    def test_sections_must_be_array(self) -> None:
        with pytest.raises(SpecParseError, match="sections"):
            parse_spec({"title": "T", "sections": "not an array"})

    def test_at_least_one_usable_section_required(self) -> None:
        with pytest.raises(SpecParseError, match="at least one usable section"):
            parse_spec({"title": "T", "sections": [
                {"title": "S", "blocks": []},  # no blocks → unusable
            ]})


# --------------------------------------------------------------------------- #
# builder.build_document_from_spec — end-to-end save + lenient validation
# --------------------------------------------------------------------------- #


def test_builder_renders_all_block_types(tmp_path: Path) -> None:
    spec = parse_spec({
        "title": "Builder Test",
        "subtitle": "all blocks",
        "sections": [{
            "title": "Coverage",
            "level": 1,
            "blocks": [
                {"type": "paragraph", "text": "Hello bold world.", "emphasis": ["bold"]},
                {"type": "callout", "kind": "info", "body": "fyi", "title": "Note"},
                {"type": "callout", "kind": "warning", "body": "watch out"},
                {"type": "list", "kind": "bullet", "items": ["a", "b"]},
                {"type": "list", "kind": "numbered", "items": ["1", "2"]},
                {"type": "list", "kind": "checklist", "items": ["todo", "done"]},
                {"type": "table", "header": ["k", "v"], "rows": [["x", "1"]]},
                {"type": "code", "language": "py", "content": "x = 1"},
            ],
        }],
    })
    doc = build_document_from_spec(spec)
    out = tmp_path / "out.docx"
    doc.save(out)
    assert out.exists()
    report = doc.validate(level="lenient")
    assert report.passed
    assert report.hard_count == 0


def test_builder_applies_section_layout(tmp_path: Path) -> None:
    """Section.layout (orientation, columns, header) must surface as sectPr."""
    import zipfile

    from lxml import etree

    spec = parse_spec({
        "title": "Layout",
        "sections": [
            {
                "title": "Wide",
                "layout": {"orientation": "landscape", "header": "Hd"},
                "blocks": [{"type": "paragraph", "text": "x"}],
            },
            {
                "title": "Cols",
                "layout": {"columns": 2},
                "blocks": [{"type": "paragraph", "text": "y"}],
            },
        ],
    })
    doc = build_document_from_spec(spec)
    out = tmp_path / "layout.docx"
    doc.save(out)

    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml")
    tree = etree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    sect_prs = tree.findall(".//w:sectPr", ns)

    landscape = any(
        sp.find("w:pgSz", ns) is not None
        and sp.find("w:pgSz", ns).get(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}orient"
        ) == "landscape"
        for sp in sect_prs
    )
    two_col = any(
        sp.find("w:cols", ns) is not None
        and sp.find("w:cols", ns).get(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}num"
        ) == "2"
        for sp in sect_prs
    )
    assert landscape, "landscape section must surface as sectPr orient='landscape'"
    assert two_col, "2-column section must surface as sectPr w:cols num='2'"


def test_builder_paragraph_emphasis_renders_bold(tmp_path: Path) -> None:
    """Substrings in `emphasis` should appear as bold runs in the saved XML."""
    import zipfile

    spec = parse_spec({
        "title": "Emph",
        "sections": [{
            "title": "S",
            "blocks": [
                {"type": "paragraph", "text": "plain start, BOLD MIDDLE, plain end.",
                 "emphasis": ["BOLD MIDDLE"]},
            ],
        }],
    })
    doc = build_document_from_spec(spec)
    out = tmp_path / "emph.docx"
    doc.save(out)

    with zipfile.ZipFile(out) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
    # The bold run should carry <w:b/> inside its rPr; check by string match.
    assert "BOLD MIDDLE" in document_xml
    assert "<w:b" in document_xml  # at least one bold marker present


def test_builder_handles_tolerant_parse_output(tmp_path: Path) -> None:
    """Even a spec that survived heavy tolerance (dropped unknown fields,
    minimal layout) must produce a valid docx."""
    spec = parse_spec({
        "title": "Tolerant",
        "sections": [{
            "title": "Survives",
            "blocks": [
                {"type": "paragraph", "text": "ok"},
                {"type": "wat", "ignored": True},  # dropped
            ],
        }],
    })
    doc = build_document_from_spec(spec)
    out = tmp_path / "tol.docx"
    doc.save(out)
    assert doc.validate(level="lenient").passed
