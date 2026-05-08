# FILE: src/mint/theme_extract.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Extract a MINT ThemeTokens TOML file from a reference DOCX.
#            Reads styles.xml + the first table (header shading + run colors,
#            cell margins) and produces a theme TOML that matches the
#            visual design of the source document. Powers
#            `mint theme extract <docx> <name>` so users can register their
#            own design as the active theme.
#   SCOPE: ZIP-level inspection of styles.xml and word/document.xml. Does
#          NOT execute any document content; pure offline analysis.
#   DEPENDS: M-THEME, M-PATHS
#   LINKS: docs/knowledge-graph.xml#M-THEMEEXTRACT,
#          docs/verification-plan.xml#V-M-THEMEEXTRACT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   extract_theme       - read DOCX, return dict of theme tokens
#   write_theme_toml    - serialize tokens dict to TOML at given path
#   register_user_theme - high-level: extract + write under mint/themes/
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial DOCX → ThemeTokens extractor.
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree

from mint.paths import THEMES_DIR

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# START_BLOCK: extract-helpers
def _read_xml(zf: zipfile.ZipFile, name: str) -> etree._Element | None:
    try:
        raw = zf.read(name)
    except KeyError:
        return None
    try:
        return etree.fromstring(raw)
    except etree.XMLSyntaxError:
        return None


def _get_attr(el: etree._Element | None, attr: str) -> str | None:
    if el is None:
        return None
    return el.get(f"{{{W_NS}}}{attr}")


def _norm_color(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lstrip("#")
    if len(s) != 6:
        return None
    return s.upper() if re.match(r"^[0-9A-Fa-f]{6}$", s) else None


def _extract_style_runs(
    styles_root: etree._Element,
) -> dict[str, dict[str, Any]]:
    """Map style-id → {size, color, bold, italic, font} from styles.xml."""
    out: dict[str, dict[str, Any]] = {}
    for style in styles_root.findall(f"{{{W_NS}}}style"):
        sid = style.get(f"{{{W_NS}}}styleId")
        if not sid:
            continue
        rpr = style.find(f"{{{W_NS}}}rPr")
        ppr = style.find(f"{{{W_NS}}}pPr")
        spec: dict[str, Any] = {}
        if rpr is not None:
            sz = rpr.find(f"{{{W_NS}}}sz")
            if sz is not None and (v := _get_attr(sz, "val")) and v.isdigit():
                spec["size"] = int(v)
            col = rpr.find(f"{{{W_NS}}}color")
            if col is not None and (c := _norm_color(_get_attr(col, "val"))):
                spec["color"] = c
            if rpr.find(f"{{{W_NS}}}b") is not None:
                spec["bold"] = True
            if rpr.find(f"{{{W_NS}}}i") is not None:
                spec["italic"] = True
            font = rpr.find(f"{{{W_NS}}}rFonts")
            if font is not None:
                ascii_font = _get_attr(font, "ascii")
                if ascii_font:
                    spec["font"] = ascii_font
        if ppr is not None:
            spacing = ppr.find(f"{{{W_NS}}}spacing")
            if spacing is not None:
                for key in ("before", "after", "line", "lineRule"):
                    val = _get_attr(spacing, key)
                    if val:
                        spec.setdefault("paragraph_spacing", {})[key] = val
        out[sid] = spec
    return out


def _first_table_tokens(
    doc_root: etree._Element,
) -> dict[str, Any] | None:
    """Inspect first table: cell margins, header shading, body color."""
    tbl = doc_root.find(f".//{{{W_NS}}}tbl")
    if tbl is None:
        return None
    tokens: dict[str, Any] = {}

    tbl_pr = tbl.find(f"{{{W_NS}}}tblPr")
    if tbl_pr is not None:
        tbl_w = tbl_pr.find(f"{{{W_NS}}}tblW")
        if tbl_w is not None and (w := _get_attr(tbl_w, "w")) and w.isdigit():
            tokens["target_width_dxa"] = int(w)
        cell_mar = tbl_pr.find(f"{{{W_NS}}}tblCellMar")
        if cell_mar is not None:
            margins: dict[str, int] = {}
            for side in ("top", "left", "bottom", "right"):
                el = cell_mar.find(f"{{{W_NS}}}{side}")
                if el is not None and (
                    v := _get_attr(el, "w")
                ) and v.isdigit():
                    margins[side] = int(v)
            if margins:
                tokens["cell_margins"] = margins
        borders = tbl_pr.find(f"{{{W_NS}}}tblBorders")
        if borders is not None:
            top = borders.find(f"{{{W_NS}}}top")
            if top is not None:
                sz = _get_attr(top, "sz")
                col = _norm_color(_get_attr(top, "color"))
                if sz and sz.isdigit():
                    tokens.setdefault("borders", {})["size"] = int(sz)
                if col and col != "AUTO":
                    tokens.setdefault("borders", {})["color"] = col

    rows = tbl.findall(f"{{{W_NS}}}tr")
    if not rows:
        return tokens

    # Header row (first row)
    header_cells = rows[0].findall(f"{{{W_NS}}}tc")
    if header_cells:
        first_hdr = header_cells[0]
        shd = first_hdr.find(f"{{{W_NS}}}tcPr/{{{W_NS}}}shd")
        if shd is not None and (fill := _norm_color(_get_attr(shd, "fill"))):
            tokens.setdefault("header", {})["fill"] = fill
        for r in first_hdr.iter(f"{{{W_NS}}}r"):
            col_el = r.find(f"{{{W_NS}}}rPr/{{{W_NS}}}color")
            if col_el is not None and (
                c := _norm_color(_get_attr(col_el, "val"))
            ):
                tokens.setdefault("header", {})["text"] = c
                break
            else:
                tokens.setdefault("header", {})["text"] = "FFFFFF"
                break

    # Body row
    if len(rows) > 1:
        body_cells = rows[1].findall(f"{{{W_NS}}}tc")
        if body_cells:
            for r in body_cells[0].iter(f"{{{W_NS}}}r"):
                col_el = r.find(f"{{{W_NS}}}rPr/{{{W_NS}}}color")
                if col_el is not None and (
                    c := _norm_color(_get_attr(col_el, "val"))
                ):
                    tokens.setdefault("body", {})["text"] = c
                    break

    return tokens


def _inspect_run_colors(doc_root: etree._Element) -> Counter[str]:
    """Histogram of all run colors used in the document body."""
    colors: Counter[str] = Counter()
    for r in doc_root.iter(f"{{{W_NS}}}r"):
        col_el = r.find(f"{{{W_NS}}}rPr/{{{W_NS}}}color")
        if col_el is not None and (
            c := _norm_color(_get_attr(col_el, "val"))
        ):
            colors[c] += 1
    return colors
# END_BLOCK: extract-helpers


# START_BLOCK: extract-public
def extract_theme(
    docx_path: Path,
    *,
    name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Read a DOCX and produce a theme-tokens dict.

    Falls back to showcase_v1 defaults when the source DOCX lacks specific
    information (e.g. no tables → table tokens borrow defaults).

    The returned dict can be passed to write_theme_toml() or
    parse_theme() (after structural completion).
    """
    if not docx_path.exists():
        raise FileNotFoundError(docx_path)
    with zipfile.ZipFile(docx_path) as zf:
        styles_root = _read_xml(zf, "word/styles.xml")
        doc_root = _read_xml(zf, "word/document.xml")

    if styles_root is None or doc_root is None:
        raise ValueError(
            f"DOCX {docx_path} is missing styles.xml or document.xml"
        )

    style_runs = _extract_style_runs(styles_root)
    table_tokens = _first_table_tokens(doc_root) or {}
    color_hist = _inspect_run_colors(doc_root)

    # Resolve dominant primary color: prefer Heading1 style color, else
    # most-frequent non-body color in run histogram, else fallback.
    primary = (
        style_runs.get("Heading1", {}).get("color")
        or style_runs.get("Title", {}).get("color")
        or table_tokens.get("header", {}).get("fill")
    )
    if primary is None:
        # most-frequent saturated color
        for c, _ in color_hist.most_common():
            if c not in ("000000", "FFFFFF", "333333", "1F1F1F"):
                primary = c
                break
    if primary is None:
        primary = "1B3A5C"

    body_text = (
        style_runs.get("Normal", {}).get("color")
        or table_tokens.get("body", {}).get("text")
        or "333333"
    )

    # Extract per-role typography sizes if available.
    def _role(style_id: str, default: int) -> dict[str, Any]:
        spec = style_runs.get(style_id, {})
        out: dict[str, Any] = {"size": int(spec.get("size", default))}
        if "color" in spec:
            out["color"] = spec["color"]
        if spec.get("bold"):
            out["bold"] = True
        if spec.get("italic"):
            out["italic"] = True
        if "font" in spec:
            out["font"] = spec["font"]
        return out

    typography_styles = {
        "title": _role("Title", 80),
        "heading1": _role("Heading1", 32),
        "heading2": _role("Heading2", 28),
        "heading3": _role("Heading3", 24),
        "body": _role("Normal", 22),
        "code": {"size": 20, "color": body_text, "font": "Consolas"},
        "caption": {"size": 18, "italic": True, "color": "6B7280"},
        "footer": {"size": 16, "color": "6B7280"},
        "subtitle": {"size": 28, "italic": True, "color": "6B7280"},
    }

    header_fill = (
        table_tokens.get("header", {}).get("fill") or primary
    )
    header_text = table_tokens.get("header", {}).get("text") or "FFFFFF"

    return {
        "meta": {
            "name": name,
            "description": description
            or f"Theme extracted from {docx_path.name}",
            "version": 1,
        },
        "palette": {
            "primary": primary,
            "primary_text_on": "FFFFFF",
            "body": body_text,
            "muted": "6B7280",
            "border": table_tokens.get("borders", {}).get("color")
            or "DDDDDD",
            "alt_row": "F3F4F6",
            "accent": "2E75B6",
            "callouts": {
                "warning": {"border": "E8A838", "fill": "FFF8E1"},
                "note": {"border": "2E75B6", "fill": "EBF5FB"},
                "tip": {"border": "2E8B57", "fill": "E8F5E9"},
                "caution": {"border": "C0392B", "fill": "FDECEA"},
            },
        },
        "typography": {
            "default_font": (
                style_runs.get("Normal", {}).get("font", "Calibri")
            ),
            "mono_font": "Consolas",
            "styles": typography_styles,
        },
        "paragraph": {
            "body_after": 120,
            "body_line": 276,
            "body_line_rule": "auto",
            "heading_before": 240,
            "heading_after": 120,
        },
        "tables": {
            "target_width_dxa": int(
                table_tokens.get("target_width_dxa", 9360)
            ),
            "alt_row_fill": "F3F4F6",
            "cell_margins": table_tokens.get(
                "cell_margins",
                {"top": 80, "bottom": 80, "left": 120, "right": 120},
            ),
            "borders": {
                "size": int(
                    table_tokens.get("borders", {}).get("size", 4)
                ),
                "color": table_tokens.get("borders", {}).get(
                    "color", "DDDDDD"
                ),
            },
            "header": {
                "fill": header_fill,
                "text": header_text,
                "bold": True,
            },
            "body": {"text": body_text, "bold": False},
        },
        "cover": {
            "title_size_cap": 56,
            "hero_size": 80,
            "tagline": "Technical Report",
            "tagline_size": 28,
            "metadata_size": 18,
            "accent_bar_size": 18,
        },
        "callout_layout": {
            "border_width": 12,
            "border_space": 8,
            "indent_left": 120,
        },
    }


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    raise TypeError(f"unsupported toml scalar: {type(v).__name__}")


def _emit_section(
    lines: list[str], path: list[str], data: dict[str, Any]
) -> None:
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, dict)]
    if scalars or not data:
        lines.append(f"[{'.'.join(path)}]")
        for k, v in scalars:
            lines.append(f"{k} = {_toml_value(v)}")
        lines.append("")
    for k, v in data.items():
        if isinstance(v, dict):
            _emit_section(lines, [*path, k], v)


def write_theme_toml(tokens: dict[str, Any], path: Path) -> None:
    """Serialize a tokens dict to a TOML file."""
    lines: list[str] = [
        f"# MINT theme: {tokens['meta'].get('name', 'unnamed')}",
        f"# {tokens['meta'].get('description', '')}",
        "",
    ]
    for top_key, sub in tokens.items():
        if isinstance(sub, dict):
            _emit_section(lines, [top_key], sub)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def register_user_theme(
    docx_path: Path,
    name: str,
    *,
    description: str | None = None,
    themes_dir: Path | None = None,
) -> Path:
    """Extract theme tokens from a DOCX and write them under mint/themes/.

    Returns the path of the written TOML file. The theme then becomes
    loadable via load_theme(name).
    """
    if not re.match(r"^[A-Za-z0-9_-]+$", name):
        raise ValueError(
            f"theme name {name!r} must be alphanumeric/underscore/hyphen"
        )
    base = themes_dir or THEMES_DIR
    target = base / f"{name}.toml"
    tokens = extract_theme(docx_path, name=name, description=description)
    write_theme_toml(tokens, target)
    return target
# END_BLOCK: extract-public
