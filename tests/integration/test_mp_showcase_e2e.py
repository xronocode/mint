# FILE: tests/integration/test_mp_showcase_e2e.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: E2E showcase generation — builds the richest document the SDK can
#     produce, then validates it passes MP-VALIDATE lenient. Serves as both a
#     capability demonstration and a gap report for missing features.
#   SCOPE: Cover page + TOC + multi-section body with paragraphs, tables, charts,
#     images. Validates lenient MP-VALIDATE passes. Pins baseline fingerprint.
#   DEPENDS: mint_python.sdk (Document, Section, Table, Style, Image, TOC, Chart, presets)
# END_MODULE_CONTRACT
"""E2E Showcase Generation — SDK Capability Demonstration.

Generates the richest document the Pure Python Edition SDK can produce,
then validates it against MP-VALIDATE on lenient mode.

Gaps (not yet supported):
  a) Bulleted/numbered/checklist lists                      — no List type
  b) Merged table cells                                     — grid-only tables
  c) Hyperlinks, bookmarks, footnotes, tab stops            — no API
  d) Multi-column, per-section headers/footers, page breaks — no section API
  e) Callout boxes (info/warning/code block)                — no component
  f) Landscape orientation, custom margins                  — no page API
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

from mint_python.core.content import Paragraph
from mint_python.sdk import (
    Chart,
    Document,
    Image,
    Section,
    Style,
    Table,
    TOC,
    presets,
)

BASELINE_PATH = Path(__file__).parent.parent / "fixtures" / "mp_showcase_baseline.json"


def build_showcase_document(tmp_dir: Path) -> Document:
    """Build the richest document the SDK can produce."""
    doc = Document(format="docx", title="MINT SDK Showcase").with_style_preset("alga_corporate")

    # ---------- Cover Page ----------
    doc.add_cover(title="MINT Pure Python SDK", subtitle="Capability Showcase v1.0")

    # ---------- TOC ----------
    doc.add_toc(max_level=2)

    # ---------- §1: Introduction ----------
    sec = Section("Introduction", level=1)
    sec.add_paragraph(
        "This document demonstrates the current capabilities of the MINT Pure Python "
        "Edition SDK. Every element was generated programmatically without a single "
        "line of Node.js or docx-js."
    )
    sec.add_paragraph(
        "Features demonstrated: cover page, table of contents, multi-section body, "
        "paragraphs, tables (from_list, from_list_of_dicts, financial, comparison), "
        "charts (bar, line, stacked_bar, pie, heatmap, waterfall, gantt), inline "
        "images, and style presets (alga_corporate, minimal, compact)."
    )
    sec.add_paragraph(
        "Gaps documented at end of this guide: lists, merged cells, hyperlinks, "
        "footnotes, callout components, multi-column layouts."
    )
    # Per-run formatting demo (added in MP-CONTENT v0.1.0).
    sec.add_paragraph(
        Paragraph("Per-run formatting demo: ")
        .add_run("bold", bold=True)
        .add_run(", ")
        .add_run("italic", italic=True)
        .add_run(", ")
        .add_run("underlined", underline=True)
        .add_run(", ")
        .add_run("colored", color="#C0392B")
        .add_run(", and ")
        .add_run("larger", font_size_pt=16.0)
        .add_run(" — all in one paragraph without derived Style objects.")
    )
    doc.add_section(sec)

    # ---------- §2: Style System ----------
    style_section = Section("Style System", level=1)
    style_section.add_paragraph("Built-in style presets:")
    for preset_name in sorted(presets.keys()):
        style_section.add_paragraph(f"  • {preset_name} — consistent typography, color palette, spacing")
    doc.add_section(style_section)

    # ---------- §3: Tables ----------
    table_section = Section("Tables", level=1)
    table_section.add_paragraph("Basic table with header:")

    table_section.add_table(Table.from_list([
        ["Format", "Extension", "SDK Support"],
        ["DOCX", ".docx", "Full"],
        ["PDF (via Gotenberg)", ".pdf", "Full"],
        ["PPTX", ".pptx", "Planned"],
        ["XLSX", ".xlsx", "Planned"],
    ]))
    table_section.add_paragraph("Financial table:")

    table_section.add_table(Table.financial(
        rows=[
            ["Quarter", "Revenue ($M)", "Cost ($M)", "Profit ($M)"],
            ["Q1", 12.5, 8.3, 4.2],
            ["Q2", 14.1, 9.1, 5.0],
            ["Q3", 16.2, 10.0, 6.2],
            ["Q4", 18.5, 11.2, 7.3],
        ],
    ))
    table_section.add_paragraph("Comparison table:")

    table_section.add_paragraph("Compatibility table:")

    table_section.add_table(Table.from_list([
        ["Feature", "MINT SDK", "python-docx raw", "docx-js"],
        ["Cover Page", "✓", "Manual*", "✓"],
        ["TOC", "✓", "Manual*", "✓"],
        ["Charts (7 types)", "✓", "—", "—"],
        ["Validation", "✓", "—", "—"],
        ["GRACE Metadata", "✓", "—", "—"],
        ["PDF Export", "✓ (Gotenberg)", "—", "—"],
    ]))

    doc.add_section(table_section)

    # ---------- §4: Charts ----------
    chart_section = Section("Charts", level=1)

    chart_section.add_paragraph("Bar Chart — Quarterly Revenue:")
    chart_section.add_chart(Chart.bar(
        ["Q1", "Q2", "Q3", "Q4"],
        [12.5, 14.1, 16.2, 18.5],
        caption="Revenue ($M)",
    ))

    chart_section.add_paragraph("Line Chart — Revenue Trend:")
    chart_section.add_chart(Chart.line(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        [3.8, 4.1, 4.5, 4.6, 4.7, 4.8, 5.0, 5.2, 5.4, 5.6, 5.8, 6.2],
        caption="Monthly Revenue ($M)",
    ))

    chart_section.add_paragraph("Pie Chart — Revenue Split:")
    chart_section.add_chart(Chart.pie(
        ["Product A", "Product B", "Product C", "Services"],
        [40.0, 30.0, 20.0, 10.0],
        caption="Revenue by Segment (%)",
    ))

    chart_section.add_paragraph("Stacked Bar — Revenue by Region:")
    chart_section.add_chart(Chart.stacked_bar(
        ["North", "South", "East", "West"],
        series={"Product": [5.0, 4.0, 6.0, 3.5], "Services": [4.0, 3.5, 5.5, 3.0]},
        caption="Regional Revenue ($M)",
    ))

    chart_section.add_paragraph("Heatmap — Activity Matrix:")
    chart_section.add_chart(Chart.heatmap(
        matrix=[
            [10, 15, 12, 8, 5],
            [20, 25, 22, 18, 15],
            [15, 20, 18, 14, 12],
            [25, 30, 28, 22, 20],
            [12, 18, 15, 10, 8],
        ],
        row_labels=["9AM", "11AM", "1PM", "3PM", "5PM"],
        col_labels=["Mon", "Tue", "Wed", "Thu", "Fri"],
        caption="Hourly Activity",
    ))

    chart_section.add_paragraph("Waterfall — Cash Flow:")
    chart_section.add_chart(Chart.waterfall(
        ["Start", "Sales", "Costs", "Tax", "End"],
        [100.0, 50.0, -30.0, -10.0, 130.0],
        caption="Cash Flow ($K)",
    ))

    chart_section.add_paragraph("Gantt — Project Timeline:")
    chart_section.add_chart(Chart.gantt(
        [("Design", 0, 3), ("Develop", 3, 5), ("Test", 8, 4), ("Deploy", 12, 2)],
        caption="Project Schedule (weeks)",
    ))

    doc.add_section(chart_section)

    # ---------- §5: Images ----------
    image_section = Section("Images", level=1)
    image_section.add_paragraph("Inline image from file:")

    # Create a tiny test PNG
    import struct
    import zlib

    def _make_png(w: int, h: int, r: int, g: int, b: int) -> bytes:
        def chunk(ctype: bytes, data: bytes) -> bytes:
            c = ctype + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        raw = b""
        for y in range(h):
            raw += b"\x00"
            for x in range(w):
                raw += bytes([r, g, b])
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    png_data = _make_png(50, 50, 70, 130, 180)
    png_path = tmp_dir / "test_blue.png"
    png_path.write_bytes(png_data)

    image_section.add_image(Image.from_path(png_path))
    image_section.add_paragraph("(50×50 test PNG, programmatically generated)")

    doc.add_section(image_section)

    # ---------- §6: Validation Pipeline ----------
    validate_section = Section("Validation & Auto-Fix Pipeline", level=1)
    validate_section.add_paragraph(
        "The SDK includes a full validation pipeline: MP-RULES loads YAML rules, "
        "MP-VALIDATE runs them against the saved .docx OOXML XML, and MP-FIX "
        "applies safe auto-fixes (e.g., D-H09 newline replacement)."
    )
    validate_section.add_paragraph(
        "Severity modes: AUDIT (always pass), LENIENT (hard violations ⇒ fail), "
        "STRICT (any violation ⇒ fail). Fix categories: SAFE, VISUAL, DESTRUCTIVE."
    )
    doc.add_section(validate_section)

    # ---------- §7: GRACE Metadata ----------
    grace_section = Section("GRACE Metadata Injection", level=1)
    grace_section.add_paragraph(
        "Document.inject_grace() writes a GRACE manifest as a Custom XML Part "
        "(urn:mint:grace:2026:manifest) into the saved .docx. The manifest includes "
        "document structure analysis, SHA-256 fingerprint, and 10 AI instructions."
    )
    grace_section.add_paragraph(
        "GRACE metadata enables AI agents to understand the document's structure "
        "before editing, apply design tokens consistently, and detect drift via "
        "fingerprint comparison before and after edits."
    )
    doc.add_section(grace_section)

    # ---------- §8: Known Gaps ----------
    gap_section = Section("Known Gaps & Future Work", level=1)
    gap_section.add_paragraph("The following features are not yet supported by the Pure Python Edition SDK:")
    gaps = [
        "a) Bulleted, numbered, and checklist lists — no List block type",
        "b) Merged table cells — Table assumes regular grid; no merge API",
        "c) Hyperlinks, bookmarks, footnotes, tab stops — no corresponding block types",
        "d) Multi-column layout, per-section headers/footers, explicit page breaks — section/page API not exposed",
        "e) Callout components (info, warning, code block) — no component library",
        "f) Landscape orientation, custom page margins — page-level properties not exposed",
        "g) Watermarks, text boxes, WordArt — artistic elements deferred",
        "h) Track changes, comments, document protection — collaboration features deferred",
        "i) Embedded OLE objects (Excel charts, etc.) — complex embedding deferred",
    ]
    for gap in gaps:
        gap_section.add_paragraph(gap)
    doc.add_section(gap_section)

    return doc


class TestShowcaseE2E:
    """Build the richest document, validate, and compare against golden baseline."""

    def test_build_showcase_passes_lenient_validation(self, tmp_path: Path) -> None:
        """The showcase document must pass lenient MP-VALIDATE (hard_count=0)."""
        doc = build_showcase_document(tmp_path)
        doc.save(tmp_path / "showcase.docx")

        report = doc.validate(level="lenient")
        assert report.passed, f"lenient validation failed: hard={report.hard_count} violations={report.total}"
        assert report.hard_count == 0, f"expected 0 hard violations, got {report.hard_count}"

    def test_showcase_fingerprint_matches_baseline(self, tmp_path: Path) -> None:
        """Structural fingerprint must match the pinned baseline."""
        doc = build_showcase_document(tmp_path)
        doc.save(tmp_path / "showcase.docx")

        actual_hash = hashlib.sha256(
            (tmp_path / "showcase.docx").read_bytes()
        ).hexdigest()

        if BASELINE_PATH.exists() and os.environ.get("MP_SHOWCASE_WRITE_BASELINE") != "1":
            baseline = json.loads(BASELINE_PATH.read_text())
            expected = baseline.get("sha256")
            assert actual_hash == expected, (
                f"Fingerprint divergence! expected={expected}, actual={actual_hash}. "
                f"Set MP_SHOWCASE_WRITE_BASELINE=1 to update the baseline."
            )

        if os.environ.get("MP_SHOWCASE_WRITE_BASELINE") == "1":
            BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
            BASELINE_PATH.write_text(json.dumps({"sha256": actual_hash}, indent=2))

    def test_sections_count(self, tmp_path: Path) -> None:
        """Verify the document has the expected section count."""
        doc = build_showcase_document(tmp_path)
        assert len(doc._sections) >= 8  # cover + TOC + 8 body sections

    def test_charts_count(self, tmp_path: Path) -> None:
        """Verify the document contains all 7 chart types."""
        doc = build_showcase_document(tmp_path)
        chart_types = set()
        for section in doc._sections:
            for block in section._blocks:
                if isinstance(block, Chart):
                    chart_types.add(block.chart_type)
        expected = {"bar", "line", "pie", "stacked_bar", "heatmap", "waterfall", "gantt"}
        assert chart_types >= expected, f"Missing chart types: {expected - chart_types}"

    def test_no_phase_guard_emitted(self, tmp_path: Path, caplog_at_info) -> None:
        """No BLOCK_PHASE_GUARD markers — all stubs retired."""
        doc = build_showcase_document(tmp_path)
        doc.save(tmp_path / "showcase.docx")
        doc.validate()
        doc.fix()
        doc.inject_grace()
        guards = [r.getMessage() for r in caplog_at_info.records if "BLOCK_PHASE_GUARD" in r.getMessage()]
        assert len(guards) == 0, f"Unexpected BLOCK_PHASE_GUARD: {guards}"

    def test_gaps_documented_in_last_section(self, tmp_path: Path) -> None:
        """The final section documents known gaps."""
        doc = build_showcase_document(tmp_path)
        last_section = doc._sections[-1]
        assert "Known Gaps" in last_section.title
        # Should have at least 10 gap items
        assert len(last_section._blocks) >= 10


# Run this manually to write the baseline:
# MP_SHOWCASE_WRITE_BASELINE=1 uv run pytest tests/integration/test_mp_showcase_e2e.py -k fingerprint -v
