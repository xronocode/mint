# FILE: src/mint/theme.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Single source of truth for MINT visual design tokens. Parses
#            theme files (TOML) shipped under mint/themes/ into a typed
#            ThemeTokens dataclass that M-CREATE postprocess and M-ASSEMBLE
#            templates consume. Themes can be extracted from reference DOCX
#            files via the extractor (separate module). Default theme is
#            "showcase_v1" — extracted from docs/docx_showcase.docx.
#   SCOPE: ThemeTokens dataclass + nested token groups, theme loader.
#          Does NOT extract themes from DOCX.
#   DEPENDS: M-PATHS
#   LINKS: docs/knowledge-graph.xml#M-THEME,
#          docs/verification-plan.xml#V-M-THEME
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ThemeTokens         - top-level theme dataclass
#   Palette             - color tokens (primary, body, border, alt-row, etc.)
#   CalloutPalette      - per-callout-kind {border, fill} pair
#   Typography          - default font + per-style StyleSpec map
#   StyleSpec           - one role: size (half-points), color, bold,
#                         italic, font
#   Tables              - target_width_dxa + nested table tokens
#   TableMargins        - top/bottom/left/right cell margins (DXA)
#   TableBorders        - size + color
#   TableRowFill        - text color + optional fill
#   Paragraph           - body/heading spacing tokens
#   Cover               - cover-page layout tokens
#   CalloutLayout       - shared callout box layout (border width, indent)
#   load_theme          - load theme by name from mint/themes/
#   DEFAULT_THEME_NAME  - "showcase_v1"
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial M-THEME contract: typed token sheet +
#                TOML loader. Replaces hardcoded literals across
#                M-CREATE/M-ASSEMBLE.
# END_CHANGE_SUMMARY

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mint.paths import THEMES_DIR

DEFAULT_THEME_NAME = "showcase_v1"


# START_BLOCK: token-dataclasses
@dataclass(frozen=True)
class StyleSpec:
    """One typographic role: heading-1, body, code, caption, footer, etc.

    `size` is in half-points (Word convention): 22 = 11pt.
    Colors are uppercase hex without '#'.
    """

    size: int
    color: str = "333333"
    bold: bool = False
    italic: bool = False
    font: str | None = None


@dataclass(frozen=True)
class CalloutPalette:
    border: str
    fill: str


@dataclass(frozen=True)
class Palette:
    primary: str
    primary_text_on: str
    body: str
    muted: str
    border: str
    alt_row: str
    accent: str
    callouts: dict[str, CalloutPalette]


@dataclass(frozen=True)
class Typography:
    default_font: str
    mono_font: str
    styles: dict[str, StyleSpec]

    def style(self, name: str) -> StyleSpec:
        if name not in self.styles:
            raise KeyError(
                f"unknown typography style {name!r}; have {sorted(self.styles)}"
            )
        return self.styles[name]


@dataclass(frozen=True)
class TableMargins:
    top: int
    bottom: int
    left: int
    right: int


@dataclass(frozen=True)
class TableBorders:
    size: int  # quarter-points (4 = 0.5pt)
    color: str


@dataclass(frozen=True)
class TableRowStyle:
    text: str
    fill: str | None = None
    bold: bool = False


@dataclass(frozen=True)
class Tables:
    target_width_dxa: int
    cell_margins: TableMargins
    borders: TableBorders
    header: TableRowStyle
    body: TableRowStyle
    alt_row_fill: str


@dataclass(frozen=True)
class Paragraph:
    body_after: int
    body_line: int
    body_line_rule: str
    heading_before: int
    heading_after: int


@dataclass(frozen=True)
class Cover:
    title_size_cap: int
    hero_size: int
    tagline: str
    tagline_size: int
    metadata_size: int
    accent_bar_size: int


@dataclass(frozen=True)
class CalloutLayout:
    border_width: int  # quarter-points
    border_space: int
    indent_left: int  # DXA


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    description: str
    version: int
    palette: Palette
    typography: Typography
    paragraph: Paragraph
    tables: Tables
    cover: Cover
    callout_layout: CalloutLayout
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
# END_BLOCK: token-dataclasses


# START_BLOCK: theme-loader
def _build_palette(data: dict[str, Any]) -> Palette:
    callouts_in = data.get("callouts", {})
    callouts: dict[str, CalloutPalette] = {}
    for kind, spec in callouts_in.items():
        callouts[kind] = CalloutPalette(
            border=spec["border"].upper(),
            fill=spec["fill"].upper(),
        )
    return Palette(
        primary=data["primary"].upper(),
        primary_text_on=data["primary_text_on"].upper(),
        body=data["body"].upper(),
        muted=data["muted"].upper(),
        border=data["border"].upper(),
        alt_row=data["alt_row"].upper(),
        accent=data.get("accent", data["primary"]).upper(),
        callouts=callouts,
    )


def _build_typography(data: dict[str, Any]) -> Typography:
    styles: dict[str, StyleSpec] = {}
    for name, spec in data.get("styles", {}).items():
        styles[name] = StyleSpec(
            size=int(spec["size"]),
            color=spec.get("color", "333333").upper(),
            bold=bool(spec.get("bold", False)),
            italic=bool(spec.get("italic", False)),
            font=spec.get("font"),
        )
    return Typography(
        default_font=data.get("default_font", "Calibri"),
        mono_font=data.get("mono_font", "Consolas"),
        styles=styles,
    )


def _build_tables(data: dict[str, Any]) -> Tables:
    margins = data["cell_margins"]
    borders = data["borders"]
    header = data["header"]
    body = data["body"]
    return Tables(
        target_width_dxa=int(data["target_width_dxa"]),
        cell_margins=TableMargins(
            top=int(margins["top"]),
            bottom=int(margins["bottom"]),
            left=int(margins["left"]),
            right=int(margins["right"]),
        ),
        borders=TableBorders(
            size=int(borders["size"]),
            color=borders["color"].upper(),
        ),
        header=TableRowStyle(
            text=header["text"].upper(),
            fill=(header.get("fill") or "").upper() or None,
            bold=bool(header.get("bold", True)),
        ),
        body=TableRowStyle(
            text=body["text"].upper(),
            fill=(body.get("fill") or "").upper() or None,
            bold=bool(body.get("bold", False)),
        ),
        alt_row_fill=data["alt_row_fill"].upper(),
    )


def _build_paragraph(data: dict[str, Any]) -> Paragraph:
    return Paragraph(
        body_after=int(data["body_after"]),
        body_line=int(data["body_line"]),
        body_line_rule=data.get("body_line_rule", "auto"),
        heading_before=int(data["heading_before"]),
        heading_after=int(data["heading_after"]),
    )


def _build_cover(data: dict[str, Any]) -> Cover:
    return Cover(
        title_size_cap=int(data["title_size_cap"]),
        hero_size=int(data["hero_size"]),
        tagline=str(data["tagline"]),
        tagline_size=int(data["tagline_size"]),
        metadata_size=int(data["metadata_size"]),
        accent_bar_size=int(data["accent_bar_size"]),
    )


def _build_callout_layout(data: dict[str, Any]) -> CalloutLayout:
    return CalloutLayout(
        border_width=int(data["border_width"]),
        border_space=int(data["border_space"]),
        indent_left=int(data["indent_left"]),
    )


def parse_theme(data: dict[str, Any]) -> ThemeTokens:
    """Parse a TOML-loaded dict into a ThemeTokens.

    Raises KeyError for missing required fields (loud failure on bad theme).
    """
    meta = data.get("meta", {})
    return ThemeTokens(
        name=meta.get("name", "unnamed"),
        description=meta.get("description", ""),
        version=int(meta.get("version", 1)),
        palette=_build_palette(data["palette"]),
        typography=_build_typography(data["typography"]),
        paragraph=_build_paragraph(data["paragraph"]),
        tables=_build_tables(data["tables"]),
        cover=_build_cover(data["cover"]),
        callout_layout=_build_callout_layout(data["callout_layout"]),
        raw=data,
    )


def load_theme(
    name: str = DEFAULT_THEME_NAME, *, themes_dir: Path | None = None
) -> ThemeTokens:
    """Load a theme by short name from `mint/themes/<name>.toml`.

    The default theme is `showcase_v1`, extracted from
    `docs/docx_showcase.docx`.
    """
    base = themes_dir or THEMES_DIR
    path = base / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"theme {name!r} not found at {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)
    return parse_theme(data)
# END_BLOCK: theme-loader
