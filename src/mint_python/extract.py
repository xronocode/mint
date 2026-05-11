# FILE: src/mint_python/extract.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Pure-python port of `src/mint/extract.py`. Extract design-tokens
#     (theme colors, typography, statistical layouts) from .docx / .pptx
#     archives without importing from src/mint/ (Constraint-8). Public API is
#     byte-identical to the legacy module: extract_style, parse_theme,
#     analyze_layouts, plus ExtractionFailedError / UnsupportedFormatError.
#   SCOPE: Layer 1 (theme/styles.xml parsing) + Layer 2 (statistical layout
#     analysis) over OOXML ZIP archives. Read-only — never mutates the source.
#   DEPENDS: stdlib (zipfile, pathlib, logging, xml.etree.ElementTree). The
#     legacy module's NAMESPACES dict and detect_format helper are duplicated
#     inline (small, harmless) per Constraint-8.
#   LINKS: docs/knowledge-graph.xml#MP-EXTRACT, docs/verification-plan.xml#V-MP-EXTRACT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ExtractionFailedError - raised on missing file / BadZipFile / unparseable archive
#   UnsupportedFormatError - raised when path suffix is neither .docx nor .pptx
#   NAMESPACES - OOXML namespace prefix → URI map (duplicated from mint._xml_ns)
#   parse_theme - Layer 1: parse theme1.xml for color scheme + font scheme
#   analyze_layouts - Layer 2: shape/paragraph counts grouped into layout buckets
#   extract_style - full extraction pipeline entry point
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-16-1 initial port — mirrors legacy mint.extract behavior
#     verbatim (porting-parity oracle), adds BLOCK_EXTRACT_DONE INFO log marker
#     for V-MP-EXTRACT scenario-6, duplicates the two needed helpers from
#     mint._xml_ns inline to satisfy Constraint-8.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-Extract"

# Duplicated inline from mint._xml_ns — Constraint-8 forbids `from mint.*`.
# The legacy module uses these constants for ElementTree namespace lookups.
NAMESPACES: dict[str, str] = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}


class ExtractionFailedError(Exception):
    """Raised when the OOXML archive is missing, corrupt, or unparseable."""


class UnsupportedFormatError(Exception):
    """Raised when the path suffix is neither .docx nor .pptx."""


def _detect_format(path: Path) -> str:
    """Return ``'docx'`` or ``'pptx'`` based on file extension.

    Mirrors mint._xml_ns.detect_format but raises UnsupportedFormatError
    instead of ValueError to match the legacy mint.extract surface.
    """
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "docx"
    if suffix == ".pptx":
        return "pptx"
    raise UnsupportedFormatError(f"Unsupported format: {suffix}")


def _extract_color_value(elem: ET.Element) -> str | None:
    srgb = elem.find("a:srgbClr", NAMESPACES)
    if srgb is not None:
        return f"#{srgb.get('val', '')}"
    sys_clr = elem.find("a:sysClr", NAMESPACES)
    if sys_clr is not None:
        val = sys_clr.get("lastClr", "")
        if val:
            return f"#{val}"
    return None


# START_CONTRACT: parse_theme
#   PURPOSE: Parse theme1.xml color/font scheme into a flat tokens dict
#   INPUTS: { doc_format: 'docx'|'pptx', zf: open zipfile.ZipFile }
#   OUTPUTS: { dict with keys: colors, typography, format, xml_sources }
#   SIDE_EFFECTS: emits INFO log; reads from zf (does not close it)
# END_CONTRACT: parse_theme
# START_BLOCK_PARSE_THEME
def parse_theme(doc_format: str, zf: zipfile.ZipFile) -> dict[str, Any]:
    colors: dict[str, Any] = {}
    typography: dict[str, Any] = {}
    xml_sources: list[str] = []

    if doc_format == "docx":
        theme_path = "word/theme/theme1.xml"
        styles_path = "word/styles.xml"
    else:
        theme_path = "ppt/theme/theme1.xml"
        styles_path = ""

    try:
        theme_xml = zf.read(theme_path).decode("utf-8")
        xml_sources.append(theme_path)
    except KeyError:
        theme_xml = ""

    if theme_xml:
        root = ET.fromstring(theme_xml)
        clr_scheme = root.find(".//a:clrScheme", NAMESPACES)
        if clr_scheme is not None:
            for tag_name, attr in [
                ("dk1", "dark1"),
                ("dk2", "dark2"),
                ("lt1", "light1"),
                ("lt2", "light2"),
                ("accent1", None),
                ("accent2", None),
                ("accent3", None),
            ]:
                elem = clr_scheme.find(f"a:{tag_name}", NAMESPACES)
                if elem is None:
                    continue
                rgb = _extract_color_value(elem)
                if rgb is None:
                    continue
                if attr:
                    colors[attr] = rgb
                else:
                    colors.setdefault("accent", []).append(rgb)

        font_scheme = root.find(".//a:fontScheme", NAMESPACES)
        if font_scheme is not None:
            major = font_scheme.find(".//a:majorFont", NAMESPACES)
            minor = font_scheme.find(".//a:minorFont", NAMESPACES)
            if major is not None:
                latin = major.find("a:latin", NAMESPACES)
                if latin is not None and latin.get("typeface"):
                    typography["headingFont"] = latin.get("typeface", "")
            if minor is not None:
                latin = minor.find("a:latin", NAMESPACES)
                if latin is not None and latin.get("typeface"):
                    typography["bodyFont"] = latin.get("typeface", "")

    if styles_path:
        try:
            zf.getinfo(styles_path)
            xml_sources.append(styles_path)
        except KeyError:
            pass

    result: dict[str, Any] = {
        "colors": colors,
        "typography": typography,
        "format": doc_format,
        "xml_sources": xml_sources,
    }
    logger.info(
        f"[{_LOG_PREFIX}][parse_theme][BLOCK_PARSE_THEME] "
        f"Parsed theme: format={doc_format}, colors={len(colors)}, "
        f"typography={len(typography)}"
    )
    return result
# END_BLOCK_PARSE_THEME


# START_CONTRACT: analyze_layouts
#   PURPOSE: Statistical layout summary from slide shapes (pptx) or table /
#     paragraph counts (docx)
#   INPUTS: { doc_format: 'docx'|'pptx', zf: open zipfile.ZipFile }
#   OUTPUTS: { list[dict {type, count}] sorted by type }
#   SIDE_EFFECTS: emits INFO log
# END_CONTRACT: analyze_layouts
# START_BLOCK_ANALYZE_LAYOUTS
def analyze_layouts(doc_format: str, zf: zipfile.ZipFile) -> list[dict[str, Any]]:
    layouts: list[dict[str, Any]] = []
    layout_counts: dict[str, int] = {}

    if doc_format == "pptx":
        slide_files = sorted(
            n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )
        for slide_name in slide_files:
            try:
                slide_xml = zf.read(slide_name).decode("utf-8")
                root = ET.fromstring(slide_xml)
                sp_count = len(root.findall(".//p:sp", NAMESPACES))
                layout_type = f"shapes_{sp_count}"
                layout_counts[layout_type] = layout_counts.get(layout_type, 0) + 1
            except (ET.ParseError, KeyError):
                continue
    elif doc_format == "docx":
        try:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            root = ET.fromstring(doc_xml)
            table_count = len(root.findall(".//w:tbl", NAMESPACES))
            para_count = len(root.findall(".//w:p", NAMESPACES))
            if table_count > 0:
                layout_counts["table"] = table_count
            layout_counts["paragraph"] = para_count
        except (KeyError, ET.ParseError):
            pass

    for ltype, count in sorted(layout_counts.items()):
        layouts.append({"type": ltype, "count": count})

    logger.info(
        f"[{_LOG_PREFIX}][analyze_layouts][BLOCK_ANALYZE_LAYOUTS] "
        f"Analyzed layouts: format={doc_format}, layouts={len(layouts)}"
    )
    return layouts
# END_BLOCK_ANALYZE_LAYOUTS


# START_CONTRACT: extract_style
#   PURPOSE: Full extraction pipeline — opens .docx/.pptx ZIP, parses theme +
#     layouts, returns flat design-tokens dict byte-identical to legacy
#     mint.extract.extract_style.
#   INPUTS: { document_path: str | Path }
#   OUTPUTS: { dict with keys colors, typography, format, xml_sources,
#              and optionally detected_layouts when layouts list is non-empty }
#   SIDE_EFFECTS: reads filesystem, emits INFO log including
#     [MP-Extract][run][BLOCK_EXTRACT_DONE] for V-MP-EXTRACT scenario-6
# END_CONTRACT: extract_style
# START_BLOCK_EXTRACT_STYLE
def extract_style(document_path: str | Path) -> dict[str, Any]:
    path = Path(document_path)
    if not path.is_file():
        raise ExtractionFailedError(f"File not found: {path}")

    try:
        doc_format = _detect_format(path)
    except UnsupportedFormatError as exc:
        raise ExtractionFailedError(str(exc)) from exc

    try:
        with zipfile.ZipFile(path, "r") as zf:
            tokens = parse_theme(doc_format, zf)
            layouts = analyze_layouts(doc_format, zf)
            if layouts:
                tokens["detected_layouts"] = layouts
    except zipfile.BadZipFile as exc:
        raise ExtractionFailedError(f"Invalid OOXML file: {path}") from exc

    theme_keys_count = len(tokens.get("colors", {})) + len(tokens.get("typography", {}))
    layouts_count = len(tokens.get("detected_layouts", []))
    logger.info(
        f"[{_LOG_PREFIX}][run][BLOCK_EXTRACT_DONE] "
        f"format={tokens['format']} theme_keys_count={theme_keys_count} "
        f"layouts_count={layouts_count}"
    )
    return tokens
# END_BLOCK_EXTRACT_STYLE
