# FILE: src/mint/extract.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Extract design-tokens.json from existing OOXML documents
#   SCOPE: Layer 1 (theme/styles.xml parsing) + Layer 2 (statistical layout analysis)
#   DEPENDS: M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-EXTRACT, docs/verification-plan.xml#V-M-EXTRACT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   DesignTokens - dict[str, Any] for extracted design tokens
#   extract_style - full extraction pipeline entry point
#   parse_theme - Layer 1: parse theme/styles.xml for colors, fonts, dimensions
#   analyze_layouts - Layer 2: statistical analysis of slide/page layouts
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

NAMESPACES = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


class ExtractionFailedError(Exception):
    pass


class UnsupportedFormatError(Exception):
    pass


def _detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "docx"
    if suffix == ".pptx":
        return "pptx"
    raise UnsupportedFormatError(
        f"Unsupported format '{suffix}'. Only .docx and .pptx are supported.",
    )


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
            zf.read(styles_path).decode("utf-8")
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
        "[Extract][parse_theme][BLOCK_PARSE_THEME] "
        "Parsed theme: format=%s, colors=%d, typography=%d",
        doc_format,
        len(colors),
        len(typography),
    )
    return result
# END_BLOCK_PARSE_THEME


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
        "[Extract][analyze_layouts][BLOCK_ANALYZE_LAYOUTS] "
        "Analyzed layouts: format=%s, layouts=%d",
        doc_format,
        len(layouts),
    )
    return layouts
# END_BLOCK_ANALYZE_LAYOUTS


# START_BLOCK_EXTRACT_STYLE
def extract_style(document_path: Path) -> dict[str, Any]:
    if not document_path.is_file():
        raise ExtractionFailedError(f"File not found: {document_path}")

    try:
        doc_format = _detect_format(document_path)
    except UnsupportedFormatError as e:
        raise ExtractionFailedError(str(e)) from e

    try:
        with zipfile.ZipFile(document_path, "r") as zf:
            tokens = parse_theme(doc_format, zf)
            layouts = analyze_layouts(doc_format, zf)
            if layouts:
                tokens["detected_layouts"] = layouts
    except zipfile.BadZipFile as e:
        raise ExtractionFailedError(
            f"Invalid OOXML file: {document_path}",
        ) from e

    return tokens
# END_BLOCK_EXTRACT_STYLE
