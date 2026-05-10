# FILE: tests/integration/test_klawd_showcase.py
# VERSION: 0.1.0
"""KLAWD showcase — Anthropic Claude baseline fidelity demo.

Builds a document using the ``klawd`` style preset (loaded from
src/mint_python/core/presets/klawd.yaml — the Anthropic Claude baseline
palette + typography per docs/reference/docx_showcase_guide.md) and
exercises every feature class the SDK supports today. Verifies lenient
MP-VALIDATE passes and that the saved sectPr surfaces the layout
features.

Run ``MP_KLAWD_PERSIST=1 uv run pytest tests/integration/test_klawd_showcase.py``
to copy the generated document to ``dist/klawd_showcase.docx`` for
manual visual comparison against ``docs/reference/anthropic_claude_baseline.docx``.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

from lxml import etree

from mint_python.core.content import Paragraph
from mint_python.sdk import (
    Callout,
    CalloutKind,
    Cell,
    Chart,
    Document,
    List,
    ListKind,
    Margins,
    PageLayout,
    Section,
    Table,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Hand-rolled solid-color PNG generator — no Pillow dependency."""
    import struct
    import zlib

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw = b""
    for _ in range(height):
        raw += b"\x00"
        for _ in range(width):
            raw += bytes(rgb)
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend


def build_klawd_showcase(tmp_dir: Path) -> Document:
    """Build a klawd-themed showcase exercising every SDK feature class."""
    doc = (
        Document(format="docx", title="KLAWD Showcase")
        .with_style_preset("klawd")
        .add_cover(
            title="KLAWD Showcase",
            subtitle="Anthropic Claude Baseline — Capability Demonstration",
        )
        .add_toc(max_level=2)
    )

    # ------------------------------------------------------------------ #
    # §1 Typography & character formatting
    # ------------------------------------------------------------------ #
    typo = Section("Typography & Character Formatting", level=1)
    typo.add_paragraph(
        "Headings, body text, and captions all derive from the klawd preset "
        "(Arial throughout; Primary Blue #1B3A5C for H1/H3; Accent Blue "
        "#2E75B6 for H2; Dark Gray #333333 body)."
    )
    typo.add_paragraph(
        Paragraph("Per-run formatting: ")
        .add_run("bold", bold=True)
        .add_run(", ")
        .add_run("italic", italic=True)
        .add_run(", ")
        .add_run("underlined", underline=True)
        .add_run(", ")
        .add_run("accent-coloured", color="#2E75B6")
        .add_run(", ")
        .add_run("error-coloured", color="#C0392B")
        .add_run(", and ")
        .add_run("larger", font_size_pt=14.0)
        .add_run(" — combinable in any one paragraph.")
    )
    doc.add_section(typo)

    # ------------------------------------------------------------------ #
    # §2 Lists
    # ------------------------------------------------------------------ #
    lists = Section("Lists", level=1)
    lists.add_paragraph("All three list kinds (bullet, numbered, checklist):")
    lists.add_list(
        List(
            items=[
                "Bullet — primary point",
                "Bullet — secondary point",
                "Bullet — tertiary point",
            ],
            kind=ListKind.BULLET,
        )
    )
    lists.add_list(
        List(
            items=[
                "Numbered roadmap step one",
                "Numbered roadmap step two",
                "Numbered roadmap step three",
            ],
            kind=ListKind.NUMBERED,
        )
    )
    lists.add_list(
        List(
            items=[
                "Pre-flight: tests green",
                "Pre-flight: lint + mypy clean",
                "Pre-flight: 100% coverage",
            ],
            kind=ListKind.CHECKLIST,
        )
    )
    doc.add_section(lists)

    # ------------------------------------------------------------------ #
    # §3 Tables — basic + merged + financial
    # ------------------------------------------------------------------ #
    tables = Section("Tables", level=1)
    tables.add_paragraph("Basic table with header — table_header style is white-on-primary:")
    tables.add_table(
        Table.from_list([
            ["Format", "Extension", "SDK Support"],
            ["DOCX", ".docx", "Full"],
            ["PDF (via Gotenberg)", ".pdf", "Full"],
            ["PPTX", ".pptx", "Planned"],
        ])
    )
    tables.add_paragraph("Merged cells — colspan=3 across the title row:")
    tables.add_table(
        Table.from_list(
            [
                # Empty placeholders cover the merged span; only the
                # first cell's text renders, but every row stays
                # rectangular for the from_list shape check.
                [Cell("Quarterly Performance", colspan=3), Cell(""), Cell("")],
                ["Quarter", "Revenue ($M)", "YoY %"],
                ["Q1", "12.5", "+8%"],
                ["Q2", "14.1", "+13%"],
                ["Q3", "16.2", "+15%"],
            ],
            header=False,
        )
    )
    tables.add_paragraph("Financial table preset:")
    tables.add_table(
        Table.financial(
            rows=[
                ["Quarter", "Revenue", "Cost", "Profit"],
                ["Q1", 12.5, 8.3, 4.2],
                ["Q2", 14.1, 9.1, 5.0],
                ["Q3", 16.2, 10.0, 6.2],
                ["Q4", 18.5, 11.2, 7.3],
            ],
        )
    )
    doc.add_section(tables)

    # ------------------------------------------------------------------ #
    # §4 Images & charts (landscape — wider charts read better)
    # ------------------------------------------------------------------ #
    visuals = Section("Images & Charts", level=1).with_page_layout(
        PageLayout(
            orientation="landscape",
            margins=Margins(top=0.75, bottom=0.75, left=0.75, right=0.75),
            header="KLAWD Showcase — Visual Assets",
            footer="Confidential",
        )
    )
    visuals.add_paragraph("Inline image (50x50 solid Primary Blue):")
    blue_png = tmp_dir / "klawd_blue.png"
    blue_png.write_bytes(_make_solid_png(50, 50, (27, 58, 92)))  # #1B3A5C
    from mint_python.sdk import Image
    visuals.add_image(Image.from_path(blue_png))

    visuals.add_paragraph("Bar chart — Quarterly Revenue:")
    visuals.add_chart(Chart.bar(
        ["Q1", "Q2", "Q3", "Q4"],
        [12.5, 14.1, 16.2, 18.5],
        caption="Revenue ($M)",
    ))
    visuals.add_paragraph("Line chart — Monthly Revenue Trend:")
    visuals.add_chart(Chart.line(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
        [3.8, 4.1, 4.5, 4.6, 4.7, 4.8],
        caption="Monthly Revenue ($M)",
    ))
    visuals.add_paragraph("Pie chart — Revenue Split:")
    visuals.add_chart(Chart.pie(
        ["Product A", "Product B", "Services"],
        [50.0, 30.0, 20.0],
        caption="Revenue by Segment (%)",
    ))
    doc.add_section(visuals)

    # ------------------------------------------------------------------ #
    # §5 Callouts — colours match Anthropic baseline by construction
    # ------------------------------------------------------------------ #
    callouts = Section("Callout Components", level=1)
    callouts.add_paragraph(
        "All three callout kinds. Border + fill colours are bound to the "
        "design system tokens (Accent Blue #2E75B6 + Callout BG #EBF5FB "
        "for info; Warning Amber #E8A838 + Warning BG #FFF8E1; "
        "neutral grays for code)."
    )
    callouts.add_callout(Callout(
        "Info callout — for incidental context that improves comprehension "
        "without being load-bearing for the procedure.",
        kind=CalloutKind.INFO,
        title="Note",
    ))
    callouts.add_callout(Callout(
        "Warning callout — flags non-obvious caveats. Use sparingly so the "
        "signal stays loud.",
        kind=CalloutKind.WARNING,
        title="Heads up",
    ))
    callouts.add_callout(Callout(
        "doc = Document(format='docx').with_style_preset('klawd')\n"
        "doc.save('out.docx')",
        kind=CalloutKind.CODE,
        title="Quick start",
    ))
    doc.add_section(callouts)

    # ------------------------------------------------------------------ #
    # §6 Hyperlinks, bookmarks, footnotes
    # ------------------------------------------------------------------ #
    refs = Section("Hyperlinks, Bookmarks & Footnotes", level=1)
    refs.add_paragraph(
        Paragraph("External link to ")
        .add_run("the MINT repo", link="https://github.com/xronocode/mint")
        .add_run(" and an internal jump to ")
        .add_run("the typography section", link="#typography_anchor")
        .add_run(" — both rendered as Hyperlink-styled runs.")
    )
    refs.add_paragraph(
        Paragraph("This paragraph carries a footnote")
        .add_run(
            "*",
            footnote=(
                "Footnotes are emitted on demand — the word/footnotes.xml "
                "part is bootstrapped on first Run.footnote use."
            ),
        )
        .add_run(" and another one")
        .add_run(
            "**",
            footnote="Subsequent footnotes share the same part with auto-incrementing ids.",
        )
        .add_run(".")
    )
    # Bookmark anchor — placed via Run.bookmark on a labeled run.
    refs.add_paragraph(
        Paragraph()
        .add_run("Anchor for the internal jump: ")
        .add_run("typography_anchor", bookmark="typography_anchor")
    )
    doc.add_section(refs)

    # ------------------------------------------------------------------ #
    # §7 Two-column flow — Glossary
    # ------------------------------------------------------------------ #
    glossary = Section("Glossary", level=1).with_page_layout(
        PageLayout(columns=2, page_break_before=True)
    )
    glossary.add_paragraph("Two-column flow demonstrates PageLayout.columns:")
    glossary.add_list(
        List(
            items=[
                "DXA — twentieths of a point; OOXML measurement unit.",
                "Twips — synonym for DXA in many APIs.",
                "Pt — point; 1pt = 20 DXA = 1/72 inch.",
                "EMU — English Metric Unit; 914400 EMU = 1 inch.",
                "sectPr — section properties OOXML element.",
                "rPr — run properties OOXML element.",
                "pPr — paragraph properties OOXML element.",
            ],
            kind=ListKind.BULLET,
        )
    )
    doc.add_section(glossary)

    # ------------------------------------------------------------------ #
    # §8 Closing — summary
    # ------------------------------------------------------------------ #
    closing = Section("Summary", level=1)
    closing.add_paragraph(
        "This showcase exercises every block type currently supported by "
        "the MINT Pure Python Edition under the klawd preset. Visual "
        "fidelity to docs/reference/anthropic_claude_baseline.docx is "
        "driven by the YAML preset's color palette + typography scale; "
        "callout colours are bound to the same design tokens by "
        "construction in MP-CALLOUT."
    )
    doc.add_section(closing)

    return doc


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_klawd_showcase_builds_and_passes_lenient_validation(tmp_path: Path) -> None:
    doc = build_klawd_showcase(tmp_path)
    out = tmp_path / "klawd_showcase.docx"
    doc.save(out)

    report = doc.validate(level="lenient")
    assert report.passed, (
        f"klawd showcase failed lenient validation: "
        f"hard={report.hard_count} violations={report.total}"
    )

    if os.environ.get("MP_KLAWD_PERSIST") == "1":
        dist = Path(__file__).resolve().parent.parent.parent / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        target = dist / "klawd_showcase.docx"
        shutil.copy(out, target)


def test_klawd_showcase_emits_landscape_and_two_column(tmp_path: Path) -> None:
    """Visual sectPr properties survive save+reload."""
    doc = build_klawd_showcase(tmp_path)
    out = tmp_path / "klawd_showcase.docx"
    doc.save(out)

    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml")
    tree = etree.fromstring(xml)
    ns = {"w": W_NS}
    sect_prs = tree.findall(".//w:sectPr", ns)

    landscape_seen = False
    two_col_seen = False
    for sp in sect_prs:
        pg_sz = sp.find("w:pgSz", ns)
        if pg_sz is not None and pg_sz.get(f"{{{W_NS}}}orient") == "landscape":
            landscape_seen = True
        cols = sp.find("w:cols", ns)
        if cols is not None and cols.get(f"{{{W_NS}}}num") == "2":
            two_col_seen = True
    assert landscape_seen, "Visuals section must emit a landscape sectPr"
    assert two_col_seen, "Glossary section must emit a 2-column sectPr"


def test_klawd_preset_loads_via_yaml() -> None:
    """The klawd preset is YAML-encoded; load_preset must dispatch on .yaml."""
    from mint_python.core.style import BUILTIN_PRESETS, load_preset

    assert "klawd" in BUILTIN_PRESETS
    assert BUILTIN_PRESETS["klawd"].suffix == ".yaml"

    ns = load_preset(name="klawd")
    # Spot-check the design-system-derived values.
    assert ns.heading1.color_hex == "#1B3A5C"   # Primary Blue
    assert ns.heading2.color_hex == "#2E75B6"   # Accent Blue
    assert ns.body.color_hex == "#333333"       # Dark Gray
    assert ns.caption.italic
    assert ns.heading1.font == ns.body.font == "Arial"


def test_klawd_preset_visually_applied_to_saved_styles_xml(tmp_path: Path) -> None:
    """After Document.save(), styles.xml must carry klawd's typography.

    Regression guard for the silent gap discovered post-alpha: before
    apply_preset_to_doc was wired into Document.save(), with_style_preset
    only stored the preset; render emitted python-docx's stock theme styles
    (Calibri-themed accent1, NOT klawd's Arial #1B3A5C).
    """
    import zipfile

    doc = (
        Document(format="docx", title="visual-fidelity check")
        .with_style_preset("klawd")
    )
    doc.add_section(Section("S", level=1).add_paragraph("body"))
    out = tmp_path / "klawd_visual.docx"
    doc.save(out)

    with zipfile.ZipFile(out) as z:
        styles_xml = z.read("word/styles.xml").decode("utf-8")

    # Klawd primary blue must reach Heading 1's color attribute.
    assert "1B3A5C" in styles_xml, "klawd primary #1B3A5C missing from styles.xml"
    # Arial must be specified at least once (Heading and/or Normal).
    assert "Arial" in styles_xml, "klawd's Arial font missing from styles.xml"
    # Body color #333333 (Dark Gray) must reach Normal style.
    assert "333333" in styles_xml, "klawd body #333333 missing from styles.xml"


def test_klawd_vs_claret_serif_produce_visually_distinct_styles(tmp_path: Path) -> None:
    """Same content under different presets must produce different styles.xml.

    This is the litmus test for "preset is a real abstraction": same builder,
    same blocks, same docx — only the preset name flipped. styles.xml must
    differ on font + color so a downstream reader sees a different document.
    """
    import zipfile

    def _styles_for(preset: str) -> str:
        doc = (
            Document(format="docx", title=f"check-{preset}")
            .with_style_preset(preset)
        )
        doc.add_section(Section("Heading", level=1).add_paragraph("body"))
        out = tmp_path / f"{preset}.docx"
        doc.save(out)
        with zipfile.ZipFile(out) as z:
            return z.read("word/styles.xml").decode("utf-8")

    s_klawd = _styles_for("klawd")
    s_claret = _styles_for("claret_serif")

    assert s_klawd != s_claret, "presets must produce visibly different styles.xml"
    # Each preset's signature colors land in their own output.
    assert "1B3A5C" in s_klawd and "1B3A5C" not in s_claret
    assert "7A1F2B" in s_claret and "7A1F2B" not in s_klawd
    # Font families differ.
    assert "Arial" in s_klawd
    assert "Georgia" in s_claret


def test_yaml_preset_invalid_yaml_raises_schema_error(tmp_path: Path) -> None:
    """Malformed YAML at the parser stage surfaces as STYLE_PRESET_INVALID_SCHEMA.

    Covers the yaml.YAMLError branch in _parse_preset_text — load_preset
    catches the YAML parser error and re-raises with the schema-error
    type so callers don't have to import yaml just to handle it.
    """
    import pytest

    from mint_python.core.style import STYLE_PRESET_INVALID_SCHEMA, load_preset

    bad = tmp_path / "broken.yaml"
    # Unclosed flow mapping — yaml.safe_load raises rather than returning a
    # dict. (Plain "indentation typos" tend to parse as something valid; we
    # need a syntactic error the parser can't recover from.)
    bad.write_text("name: broken\nfoo: {bar: 1\n")
    with pytest.raises(STYLE_PRESET_INVALID_SCHEMA, match="invalid YAML"):
        load_preset(path=bad)
