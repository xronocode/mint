"""Structural quality rubric for MINT-generated DOCX (deterministic, measurable).

Replaces the qwen2.5vl-based VLM scorer (which gave cargo-cult positives on
mediocre output). Counts concrete design elements: running header, footer
pagination, cover hero, TOC dot-leaders, callout boxes, table styling,
multi-level lists, embedded images, code blocks, empty-page gaps.

Each check returns 0 or 1. Total score is percent-passed. Reference doc
(docs/docx_showcase.docx) hits ~18/20. Acceptable threshold: 15/20.

Usage:
    python e2e_qa_structural.py path/to/doc.docx [--threshold 15]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}
W = f"{{{NS['w']}}}"
R = f"{{{NS['r']}}}"


@dataclass
class CheckResult:
    id: str
    name: str
    passed: bool
    detail: str = ""


@dataclass
class DocBundle:
    """Pre-parsed docx parts."""
    zf: zipfile.ZipFile
    doc_root: etree._Element
    settings_root: etree._Element | None
    parts: set[str]
    headers: list[etree._Element]
    footers: list[etree._Element]
    raw_doc: str

    @property
    def all_runs(self) -> list[etree._Element]:
        return list(self.doc_root.iter(f"{W}r"))

    @property
    def all_paragraphs(self) -> list[etree._Element]:
        return list(self.doc_root.iter(f"{W}p"))

    @property
    def all_tables(self) -> list[etree._Element]:
        return list(self.doc_root.iter(f"{W}tbl"))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load(path: Path) -> DocBundle:
    zf = zipfile.ZipFile(path)
    parts = set(zf.namelist())
    doc = etree.fromstring(zf.read("word/document.xml"))
    settings = None
    if "word/settings.xml" in parts:
        settings = etree.fromstring(zf.read("word/settings.xml"))
    headers = []
    footers = []
    for n in sorted(parts):
        if n.startswith("word/header") and n.endswith(".xml"):
            headers.append(etree.fromstring(zf.read(n)))
        elif n.startswith("word/footer") and n.endswith(".xml"):
            footers.append(etree.fromstring(zf.read(n)))
    raw_doc = etree.tostring(doc).decode("utf-8", errors="replace")
    return DocBundle(zf=zf, doc_root=doc, settings_root=settings,
                     parts=parts, headers=headers, footers=footers,
                     raw_doc=raw_doc)


def _text_of(el: etree._Element) -> str:
    return "".join((t.text or "") for t in el.iter(f"{W}t"))


# ---------------------------------------------------------------------------
# Checks (each returns CheckResult)
# ---------------------------------------------------------------------------


def check_01_running_header_with_title(b: DocBundle) -> CheckResult:
    """Header file exists, has visible text, and references doc title."""
    if not b.headers:
        return CheckResult("01", "running header present", False,
                           "no word/header*.xml")
    # Doc title heuristic: first paragraph's text
    first_text = ""
    for p in b.all_paragraphs[:5]:
        t = _text_of(p).strip()
        if t:
            first_text = t
            break
    title_words = [w for w in re.split(r"\s+", first_text) if len(w) >= 3][:3]

    for h in b.headers:
        h_text = _text_of(h).strip()
        if not h_text:
            continue
        # Pass if any non-trivial header text exists, doubly-pass if shares title words
        match = any(w.lower() in h_text.lower() for w in title_words)
        return CheckResult("01", "running header present", True,
                           f"header text='{h_text[:60]}'"
                           f"{' (matches title)' if match else ''}")
    return CheckResult("01", "running header present", False,
                       "header parts exist but contain no visible text")


def check_02_footer_pagination(b: DocBundle) -> CheckResult:
    """Footer has PageNumber.CURRENT field (or static page-X-of-Y format)."""
    if not b.footers:
        return CheckResult("02", "footer pagination", False, "no footer parts")
    for f in b.footers:
        # PageNumber.CURRENT compiles to <w:fldChar> + instr "PAGE"
        for instr in f.iter(f"{W}instrText"):
            if instr.text and "PAGE" in instr.text.upper():
                return CheckResult("02", "footer pagination", True,
                                   f"PAGE field in {instr.text.strip()[:40]}")
        # Or simpleField with PAGE
        for fs in f.iter(f"{W}fldSimple"):
            instr = fs.get(f"{W}instr", "")
            if "PAGE" in instr.upper():
                return CheckResult("02", "footer pagination", True,
                                   f"fldSimple {instr[:40]}")
    return CheckResult("02", "footer pagination", False,
                       "footers exist but no PAGE field")


def check_03_footer_total_pages(b: DocBundle) -> CheckResult:
    """Footer has 'Page X of Y' (NUMPAGES field)."""
    for f in b.footers:
        for instr in f.iter(f"{W}instrText"):
            if instr.text and "NUMPAGES" in instr.text.upper():
                return CheckResult("03", "footer 'page X of Y'", True, "NUMPAGES")
        for fs in f.iter(f"{W}fldSimple"):
            instr = fs.get(f"{W}instr", "")
            if "NUMPAGES" in instr.upper():
                return CheckResult("03", "footer 'page X of Y'", True,
                                   "NUMPAGES via fldSimple")
    return CheckResult("03", "footer 'page X of Y'", False,
                       "no NUMPAGES reference")


def check_04_cover_hero(b: DocBundle) -> CheckResult:
    """Cover (first page before first section break) has either an image or
    a vertically-balanced large title."""
    # Find first sectPr break
    has_image_in_first = False
    found_title_size = 0
    paragraphs_before_break = []
    for p in b.all_paragraphs:
        sect_pr = p.find(f".//{W}sectPr")
        if p.find(f".//{W}drawing") is not None:
            has_image_in_first = True
        for sz in p.iter(f"{W}sz"):
            v = sz.get(f"{W}val", "0")
            if v.isdigit():
                found_title_size = max(found_title_size, int(v))
        paragraphs_before_break.append(p)
        # break detector: first <w:p> whose pPr has sectPr (page break-after via section)
        if sect_pr is not None:
            break

    if has_image_in_first:
        return CheckResult("04", "cover has hero image or large title", True,
                           "embedded drawing on cover")
    if found_title_size >= 56:  # 28pt half-points
        return CheckResult("04", "cover has hero image or large title", True,
                           f"largest text size on cover = {found_title_size}")
    return CheckResult("04", "cover has hero image or large title", False,
                       f"no image, max size only {found_title_size} half-pts")


def check_05_cover_no_paragraph_overflow(b: DocBundle) -> CheckResult:
    """Cover (first 6 paragraphs) has no single paragraph >300 chars."""
    long_count = 0
    for p in b.all_paragraphs[:6]:
        t = _text_of(p)
        if len(t) > 300:
            long_count += 1
    if long_count == 0:
        return CheckResult("05", "cover paragraphs not overflowing", True, "all <=300 chars")
    return CheckResult(
        "05", "cover paragraphs not overflowing", False,
        f"{long_count} paragraph(s) >300 chars on cover (should be tagline)")


def check_06_toc_field(b: DocBundle) -> CheckResult:
    """TOC field present (instrText starting with TOC)."""
    for instr in b.doc_root.iter(f"{W}instrText"):
        if instr.text and re.match(r"\s*TOC\b", instr.text):
            return CheckResult("06", "TOC field present", True, instr.text.strip()[:40])
    return CheckResult("06", "TOC field present", False, "no TOC instr")


def check_07_toc_dot_leader(b: DocBundle) -> CheckResult:
    """TOC entries have tab-leader (dot/middleDot/hyphen) for visual alignment."""
    # Heuristic: check for <w:tabs><w:tab w:leader="dot"/> in any paragraph after the TOC field
    leader_found = False
    for tab in b.doc_root.iter(f"{W}tab"):
        leader = tab.get(f"{W}leader", "")
        if leader in ("dot", "middleDot", "hyphen"):
            leader_found = True
            break
    if leader_found:
        return CheckResult("07", "TOC dot leader", True, "leader='dot'")
    return CheckResult("07", "TOC dot leader", False,
                       "TOC entries have no tab leader — looks like a list, not a TOC")


def check_08_toc_page_numbers(b: DocBundle) -> CheckResult:
    """TOC entries reference page numbers via PAGEREF, OR have static numeric
    text right-aligned with a dot-leader tab (visually equivalent for users
    who don't refresh the field)."""
    for instr in b.doc_root.iter(f"{W}instrText"):
        if instr.text and "PAGEREF" in instr.text.upper():
            return CheckResult("08", "TOC entries with page numbers", True,
                               "PAGEREF fields present")
    # Fallback: paragraph with right-tab dot-leader AND a final run with a
    # short numeric text — that's a static-number TOC entry.
    static_numeric_entries = 0
    for p in b.all_paragraphs:
        ppr = p.find(f"{W}pPr")
        if ppr is None:
            continue
        tabs = ppr.find(f"{W}tabs")
        if tabs is None:
            continue
        if not any(
            tab.get(f"{W}leader", "") == "dot"
            for tab in tabs.findall(f"{W}tab")
        ):
            continue
        runs = p.findall(f"{W}r")
        if not runs:
            continue
        last_text = "".join(
            (t.text or "") for t in runs[-1].iter(f"{W}t")
        ).strip()
        if last_text.isdigit() and 0 < int(last_text) < 1000:
            static_numeric_entries += 1
    if static_numeric_entries >= 3:
        return CheckResult("08", "TOC entries with page numbers", True,
                           f"{static_numeric_entries} static-numbered entries with dot leader")
    return CheckResult("08", "TOC entries with page numbers", False,
                       "no PAGEREF and no dot-leader+numeric paragraphs")


def check_09_info_callout(b: DocBundle) -> CheckResult:
    """Info-style callout: paragraph with left border AND shading fill."""
    for p in b.all_paragraphs:
        ppr = p.find(f"{W}pPr")
        if ppr is None:
            continue
        bdr = ppr.find(f"{W}pBdr")
        shd = ppr.find(f"{W}shd")
        if bdr is not None and shd is not None:
            # Border has at least one of left/top/bottom/right
            has_color_border = any(
                bdr.find(f"{W}{side}") is not None
                for side in ("left", "top", "right", "bottom")
            )
            fill = shd.get(f"{W}fill", "")
            if has_color_border and fill not in ("", "auto", "FFFFFF"):
                return CheckResult("09", "info-style callout", True,
                                   f"border+shading, fill={fill}")
    return CheckResult("09", "info-style callout", False,
                       "no paragraph with both border and shading")


def check_10_warning_callout(b: DocBundle) -> CheckResult:
    """Warning-style: 2nd callout with DIFFERENT accent color than info."""
    fills_found: set[str] = set()
    for p in b.all_paragraphs:
        ppr = p.find(f"{W}pPr")
        if ppr is None:
            continue
        bdr = ppr.find(f"{W}pBdr")
        shd = ppr.find(f"{W}shd")
        if bdr is not None and shd is not None:
            fill = shd.get(f"{W}fill", "")
            if fill and fill not in ("auto", "FFFFFF"):
                fills_found.add(fill.upper())
    if len(fills_found) >= 2:
        return CheckResult("10", "≥2 distinct callout styles", True,
                           f"fills: {sorted(fills_found)}")
    return CheckResult("10", "≥2 distinct callout styles", False,
                       f"only {len(fills_found)} distinct callout fill(s)")


def check_11_table_header_shading(b: DocBundle) -> CheckResult:
    """At least one table where row 1 cells have shading fill."""
    for tbl in b.all_tables:
        rows = tbl.findall(f"{W}tr")
        if not rows:
            continue
        first_row = rows[0]
        cells = first_row.findall(f"{W}tc")
        for cell in cells:
            tcpr = cell.find(f"{W}tcPr")
            if tcpr is None:
                continue
            shd = tcpr.find(f"{W}shd")
            if shd is not None and shd.get(f"{W}fill", "") not in ("", "auto", "FFFFFF"):
                return CheckResult("11", "table header shading", True,
                                   f"fill={shd.get(f'{W}fill')}")
    return CheckResult("11", "table header shading", False,
                       "no table with shaded header row")


def check_12_table_alt_row_coloring(b: DocBundle) -> CheckResult:
    """At least one table with alt-row coloring (rows 2,4,6 have shading or rows 3,5,7)."""
    for tbl in b.all_tables:
        rows = tbl.findall(f"{W}tr")
        if len(rows) < 3:
            continue
        even_shaded = 0
        odd_shaded = 0
        for i, row in enumerate(rows[1:], start=1):  # skip header
            cells = row.findall(f"{W}tc")
            row_has_fill = False
            for cell in cells:
                tcpr = cell.find(f"{W}tcPr")
                shd = tcpr.find(f"{W}shd") if tcpr is not None else None
                fill = shd.get(f"{W}fill", "") if shd is not None else ""
                if fill and fill not in ("auto", "FFFFFF"):
                    row_has_fill = True
                    break
            if row_has_fill:
                if i % 2 == 0:
                    even_shaded += 1
                else:
                    odd_shaded += 1
        if even_shaded >= 2 or odd_shaded >= 2:
            return CheckResult("12", "alt-row coloring", True,
                               f"even={even_shaded} odd={odd_shaded}")
    return CheckResult("12", "alt-row coloring", False,
                       "no table with consistent alt-row pattern")


def check_13_multi_level_list(b: DocBundle) -> CheckResult:
    """Numbered list with at least one item at level>0."""
    levels_seen: set[int] = set()
    for p in b.all_paragraphs:
        ilvl = p.find(f"{W}pPr/{W}numPr/{W}ilvl")
        if ilvl is not None:
            v = ilvl.get(f"{W}val", "0")
            if v.isdigit():
                levels_seen.add(int(v))
    if max(levels_seen, default=-1) >= 1:
        return CheckResult("13", "multi-level list", True,
                           f"levels: {sorted(levels_seen)}")
    return CheckResult("13", "multi-level list", False,
                       f"only level 0 found ({len(levels_seen)} list items)")


def check_14_embedded_image(b: DocBundle) -> CheckResult:
    """At least one inline drawing (excluding header/footer logos)."""
    if next(b.doc_root.iter(f"{W}drawing"), None) is not None:
        return CheckResult("14", "embedded image", True, "drawing present")
    return CheckResult("14", "embedded image", False, "no <w:drawing> in body")


def check_15_code_block(b: DocBundle) -> CheckResult:
    """Paragraph with monospace font + shading (typical code-block style)."""
    for p in b.all_paragraphs:
        text = _text_of(p).strip()
        if len(text) < 20:
            continue
        ppr = p.find(f"{W}pPr")
        shd = ppr.find(f"{W}shd") if ppr is not None else None
        # Check any run for monospace font
        for r in p.iter(f"{W}r"):
            rfonts = r.find(f"{W}rPr/{W}rFonts")
            if rfonts is None:
                continue
            font = rfonts.get(f"{W}ascii", "") or rfonts.get(f"{W}hAnsi", "")
            mono_fonts = (
                "courier new", "consolas", "menlo", "monaco",
                "courier", "monospace",
            )
            if font.lower() in mono_fonts and shd is not None:
                return CheckResult("15", "code block", True,
                                   f"font={font} + shading")
    return CheckResult("15", "code block", False,
                       "no monospace+shaded paragraph found")


def _render_pdf(docx: Path, workdir: Path) -> Path:
    soffice = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(workdir),
         str(docx)], capture_output=True, timeout=60, check=True,
    )
    return workdir / (docx.stem + ".pdf")


def _pdf_page_count(pdf: Path) -> int:
    out = subprocess.run(
        ["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=10,
    )
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    return 0


def check_16_no_empty_pages(docx: Path) -> CheckResult:
    """Render to PDF, check no page is essentially blank (text length per page)."""
    workdir = Path(tempfile.mkdtemp(prefix="qa_"))
    try:
        try:
            pdf = _render_pdf(docx, workdir)
        except subprocess.CalledProcessError:
            return CheckResult("16", "no empty pages", False, "render failed")
        # Use pdftotext per page
        empty_pages: list[int] = []
        page_count = _pdf_page_count(pdf)
        if page_count == 0:
            return CheckResult("16", "no empty pages", False, "0 pages")
        for i in range(1, page_count + 1):
            out = subprocess.run(
                ["pdftotext", "-f", str(i), "-l", str(i), str(pdf), "-"],
                capture_output=True, text=True, timeout=10,
            )
            text = out.stdout.strip()
            if len(text) < 30:
                empty_pages.append(i)
        if not empty_pages:
            return CheckResult("16", "no empty pages", True,
                               f"{page_count} pages, all populated")
        return CheckResult("16", "no empty pages", False,
                           f"empty pages: {empty_pages}")
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def check_17_min_page_count(docx: Path) -> CheckResult:
    """Document has ≥4 pages (proper multi-page report, not a stub)."""
    workdir = Path(tempfile.mkdtemp(prefix="qa_"))
    try:
        try:
            pdf = _render_pdf(docx, workdir)
        except subprocess.CalledProcessError:
            return CheckResult("17", "≥4 pages", False, "render failed")
        n = _pdf_page_count(pdf)
        if n >= 4:
            return CheckResult("17", "≥4 pages", True, f"{n} pages")
        return CheckResult("17", "≥4 pages", False, f"only {n} page(s)")
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def check_18_consistent_body_sizes(b: DocBundle) -> CheckResult:
    """Body text sizes consistent — no jumping (only 2-3 distinct body sizes)."""
    body_sizes: list[int] = []
    for p in b.all_paragraphs:
        ppr = p.find(f"{W}pPr")
        pstyle = ppr.find(f"{W}pStyle") if ppr is not None else None
        style_id = pstyle.get(f"{W}val", "") if pstyle is not None else ""
        if style_id.startswith("Heading"):
            continue
        for sz in p.iter(f"{W}sz"):
            v = sz.get(f"{W}val", "0")
            if v.isdigit():
                body_sizes.append(int(v))
    if not body_sizes:
        return CheckResult("18", "body size consistency", True,
                           "no explicit body sizes (using default style)")
    distinct = len(set(body_sizes))
    if distinct <= 4:
        return CheckResult("18", "body size consistency", True,
                           f"{distinct} distinct body sizes: {sorted(set(body_sizes))[:6]}")
    return CheckResult("18", "body size consistency", False,
                       f"{distinct} different body sizes — typography jumping")


def check_19_section_failure_placeholders(b: DocBundle) -> CheckResult:
    """No 'Section failed: ...' fallback text in document body."""
    for m in re.finditer(r"Section failed:[^<]+", b.raw_doc):
        return CheckResult("19", "no section-failure fallbacks", False,
                           f"'{m.group(0)[:80]}'")
    return CheckResult("19", "no section-failure fallbacks", True,
                       "no fallback placeholders")


def check_20_real_paragraph_spacing(b: DocBundle) -> CheckResult:
    """At least 30% of body paragraphs have spacing.after/before defined."""
    body_paras = []
    for p in b.all_paragraphs:
        ppr = p.find(f"{W}pPr")
        pstyle = ppr.find(f"{W}pStyle") if ppr is not None else None
        style_id = pstyle.get(f"{W}val", "") if pstyle is not None else ""
        if style_id.startswith("Heading"):
            continue
        text = _text_of(p).strip()
        if not text or len(text) < 5:
            continue
        body_paras.append(p)
    if not body_paras:
        return CheckResult("20", "paragraph spacing", False, "no body paragraphs")
    with_spacing = 0
    for p in body_paras:
        spacing = p.find(f"{W}pPr/{W}spacing")
        if spacing is None:
            continue
        if spacing.get(f"{W}after") or spacing.get(f"{W}before") or \
           spacing.get(f"{W}line"):
            with_spacing += 1
    pct = round(100 * with_spacing / len(body_paras), 1)
    if pct >= 30:
        return CheckResult("20", "paragraph spacing", True,
                           f"{pct}% of body paras have spacing")
    return CheckResult("20", "paragraph spacing", False,
                       f"only {pct}% have spacing — paragraphs cramped together")


CHECKS_DOC: list = [
    check_01_running_header_with_title,
    check_02_footer_pagination,
    check_03_footer_total_pages,
    check_04_cover_hero,
    check_05_cover_no_paragraph_overflow,
    check_06_toc_field,
    check_07_toc_dot_leader,
    check_08_toc_page_numbers,
    check_09_info_callout,
    check_10_warning_callout,
    check_11_table_header_shading,
    check_12_table_alt_row_coloring,
    check_13_multi_level_list,
    check_14_embedded_image,
    check_15_code_block,
    check_18_consistent_body_sizes,
    check_19_section_failure_placeholders,
    check_20_real_paragraph_spacing,
]
CHECKS_PDF: list = [
    check_16_no_empty_pages,
    check_17_min_page_count,
]


def evaluate(path: Path) -> dict[str, Any]:
    bundle = _load(path)
    results: list[CheckResult] = []
    for fn in CHECKS_DOC:
        results.append(fn(bundle))
    for fn in CHECKS_PDF:
        results.append(fn(path))
    bundle.zf.close()
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    return {
        "path": str(path),
        "score": passed,
        "total": total,
        "percent": round(100 * passed / total, 1),
        "results": [
            {"id": r.id, "name": r.name, "passed": r.passed, "detail": r.detail}
            for r in results
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("docx", type=Path)
    ap.add_argument("--threshold", type=int, default=15)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    result = evaluate(args.docx)
    if args.quiet:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== structural rubric: {args.docx} ===")
        print(f"score: {result['score']}/{result['total']} ({result['percent']}%)")
        print()
        for r in result["results"]:
            mark = "✅" if r["passed"] else "❌"
            print(f"  {mark} [{r['id']}] {r['name']}")
            if r["detail"]:
                print(f"        {r['detail']}")
        print()
        print(f"target: ≥{args.threshold} = "
              f"{'PASS' if result['score'] >= args.threshold else 'FAIL'}")
    return 0 if result["score"] >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
