# FILE: src/mint_python/core/style.py
# VERSION: 0.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Typography, color, and spacing primitives + named style presets
#     ("alga_corporate" etc.) for the Pure Python Edition. Backbone for every
#     other MP-* core type that emits styled OOXML.
#   SCOPE: Public surface = Style (frozen dataclass), Pt (twentieths-of-a-point
#     helper), ColorPalette (named color resolver), load_preset (registry-or-path
#     loader with hand-rolled JSON Schema validator), and BUILTIN_PRESETS (read-
#     only registry mapping name -> JSON Path; Wave-7-5 will alias
#     mint_python.sdk.presets to this dict).
#   DEPENDS: stdlib only (dataclasses, json, logging, pathlib, re, types).
#     Hand-rolled validator avoids a jsonschema dep — schema is small + stable.
#   LINKS: docs/style-preset-schema.md (normative schema),
#     docs/development-plan.xml#MP-STYLE,
#     docs/verification-plan.xml#V-MP-STYLE,
#     docs/knowledge-graph.xml#MP-STYLE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Style                       - frozen dataclass per docs/style-preset-schema.md §2
#   Pt                          - Pt(value) -> int (twentieths of a point)
#   ColorPalette                - frozen dataclass; .resolve(key) -> '#RRGGBB'
#   load_preset                 - load by registry name OR JSON path; validate; -> SimpleNamespace
#   BUILTIN_PRESETS             - module-level read-only dict: name -> Path
#   STYLE_PRESET_NOT_FOUND      - error class
#   STYLE_PRESET_INVALID_SCHEMA - error class
#   SUPPORTED_SCHEMA_MAJOR      - "1" (Phase-7 reads 1.x presets)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-1 (MP-STYLE): initial implementation per
#     docs/style-preset-schema.md + V-MP-STYLE scenarios 1-13 + forbidden-1..4.
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_SCHEMA_MAJOR: str = "1"

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PALETTE_TOKEN_RE = re.compile(r"^@([a-z_]+)$")
_ALIGNMENT_VALUES: frozenset[str] = frozenset({"left", "center", "right", "justify"})

_REQUIRED_TOP_LEVEL: tuple[str, ...] = (
    "name",
    "version",
    "color_palette",
    "typography",
    "spacing",
)
_REQUIRED_PALETTE_KEYS: tuple[str, ...] = (
    "primary",
    "secondary",
    "accent",
    "text",
    "text_muted",
    "background",
    "border",
)
_REQUIRED_TYPOGRAPHY_KEYS: tuple[str, ...] = (
    "heading1",
    "heading2",
    "heading3",
    "body",
    "table_header",
    "caption",
)
_REQUIRED_STYLESPEC_KEYS: tuple[str, ...] = ("font", "size_pt", "color")
_REQUIRED_SPACING_KEYS: tuple[str, ...] = (
    "paragraph_default_before_pt",
    "paragraph_default_after_pt",
    "default_line_height",
    "table_cell_padding_pt",
)

_PRESETS_DIR: Path = Path(__file__).resolve().parent / "presets"

# Module-level read-only registry: name -> JSON path. Tests treat this as
# immutable; load_preset(path=...) MUST NOT mutate it (forbidden-3).
BUILTIN_PRESETS: Mapping[str, Path] = MappingProxyType(
    {
        "alga_corporate": _PRESETS_DIR / "alga_corporate.json",
        "minimal": _PRESETS_DIR / "minimal.json",
        "compact": _PRESETS_DIR / "compact.json",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class STYLE_PRESET_NOT_FOUND(Exception):  # noqa: N801, N818 - error code naming per contract
    """Raised when a preset name is not in BUILTIN_PRESETS or path does not exist."""


class STYLE_PRESET_INVALID_SCHEMA(Exception):  # noqa: N801, N818 - error code naming per contract
    """Raised when a preset JSON fails schema validation.

    Message format: ``"<json_pointer>: <constraint_description>"`` so callers
    can pinpoint the failing field and the constraint that failed.
    """


# ---------------------------------------------------------------------------
# Style dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    """Frozen typography + spacing record per docs/style-preset-schema.md §2.

    color_hex is stored AFTER ``@palette-token`` resolution as a literal
    ``#RRGGBB`` string. Callers therefore never have to re-resolve.
    """

    font: str
    size_pt: float
    color_hex: str
    bold: bool = False
    italic: bool = False
    alignment: str = "left"
    spacing_before_pt: float = 0.0
    spacing_after_pt: float = 0.0
    line_height: float = 1.15
    keep_with_next: bool = False


# ---------------------------------------------------------------------------
# Pt helper
# ---------------------------------------------------------------------------


def Pt(value: float | int) -> int:  # noqa: N802 - mirrors python-docx Pt naming
    """Convert a point value to twentieths of a point (OOXML twips for many attrs).

    Pt(12) -> 240, Pt(0.5) -> 10. Returns int via ``int(round(value * 20))``.

    NOTE on python-docx interop: python-docx ships its own ``docx.shared.Pt``
    that returns an opaque length object whose ``.twips`` equals what we
    return here directly as an int. Wave-7-2 (MP-CONTENT Paragraph.render)
    decides whether to wrap our ints back into ``docx.shared.Pt`` at the
    OOXML emit boundary or to pass twips directly. Either path is compatible
    — our Pt() is the schema-aligned twips value.
    """
    return round(value * 20)


# ---------------------------------------------------------------------------
# ColorPalette
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColorPalette:
    """Named color resolver tied to a preset.

    ``ColorPalette('alga_corporate').resolve('primary')`` returns the documented
    hex (e.g. ``'#0F4C81'``). When constructed with a registry name and no
    explicit ``colors=`` mapping, the palette is loaded from the corresponding
    built-in preset JSON. Unknown keys raise ``KeyError`` whose message names
    BOTH the palette and the missing key.
    """

    name: str
    colors: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # If colors not explicitly supplied AND name matches a built-in preset,
        # hydrate from the preset's color_palette section. This keeps the
        # documented one-arg ergonomic (per V-MP-STYLE scenario-2) without
        # forcing every caller to load the JSON first.
        colors_arg = self.colors
        if not colors_arg and self.name in BUILTIN_PRESETS:
            json_path = BUILTIN_PRESETS[self.name]
            data = json.loads(json_path.read_text(encoding="utf-8"))
            colors_arg = data.get("color_palette", {})
        # Snapshot the colors dict into a read-only mapping so callers can't
        # mutate a palette through a leaked dict reference.
        object.__setattr__(self, "colors", MappingProxyType(dict(colors_arg)))

    def resolve(self, key: str) -> str:
        try:
            return self.colors[key]
        except KeyError:
            available = sorted(self.colors)
            raise KeyError(
                f"palette '{self.name}' has no color '{key}'; available: {available}"
            ) from None


# ---------------------------------------------------------------------------
# Hand-rolled JSON Schema validator
# ---------------------------------------------------------------------------


def _fail(pointer: str, constraint: str) -> STYLE_PRESET_INVALID_SCHEMA:
    """Build a STYLE_PRESET_INVALID_SCHEMA carrying ``<pointer>: <constraint>``."""
    return STYLE_PRESET_INVALID_SCHEMA(f"{pointer}: {constraint}")


def _require_number_nonneg(value: Any, pointer: str) -> None:
    # bools must NOT pass as numbers.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(pointer, f"expected number, got {type(value).__name__}")
    if value < 0:
        raise _fail(pointer, f"expected number >= 0, got {value}")


def _require_number_positive(value: Any, pointer: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(pointer, f"expected number, got {type(value).__name__}")
    if value <= 0:
        raise _fail(pointer, f"expected number > 0, got {value}")


def _require_bool(value: Any, pointer: str) -> None:
    if not isinstance(value, bool):
        raise _fail(pointer, f"expected boolean, got {type(value).__name__}")


def _require_string(value: Any, pointer: str) -> None:
    if not isinstance(value, str):
        raise _fail(pointer, f"expected string, got {type(value).__name__}")


def _validate_hex(value: Any, pointer: str) -> None:
    _require_string(value, pointer)
    if not _HEX_RE.match(value):
        raise _fail(pointer, f"expected hex #RRGGBB, got {value!r}")


def _validate_color_palette(palette: Any, pointer: str) -> None:
    if not isinstance(palette, dict):
        raise _fail(pointer, f"expected object, got {type(palette).__name__}")
    for key in _REQUIRED_PALETTE_KEYS:
        if key not in palette:
            raise _fail(f"{pointer}/{key}", "required color_palette key missing")
        _validate_hex(palette[key], f"{pointer}/{key}")
    # Optional semantic keys: still validated as hex when present.
    for opt in ("success", "warning", "error"):
        if opt in palette:
            _validate_hex(palette[opt], f"{pointer}/{opt}")


def _validate_color_field(value: Any, pointer: str, palette: Mapping[str, str]) -> str:
    """Resolve color literal-or-token; return literal '#RRGGBB' on success."""
    _require_string(value, pointer)
    str_value: str = value  # narrowed by _require_string
    if _HEX_RE.match(str_value):
        return str_value
    token_match = _PALETTE_TOKEN_RE.match(str_value)
    if token_match is not None:
        token_key = token_match.group(1)
        if token_key not in palette:
            raise _fail(
                pointer,
                f"palette token '@{token_key}' does not exist in color_palette",
            )
        return palette[token_key]
    raise _fail(
        pointer,
        f"expected hex #RRGGBB or @palette-token, got {value!r}",
    )


def _validate_stylespec(spec: Any, pointer: str, palette: Mapping[str, str]) -> Style:
    if not isinstance(spec, dict):
        raise _fail(pointer, f"expected object, got {type(spec).__name__}")
    for required in _REQUIRED_STYLESPEC_KEYS:
        if required not in spec:
            raise _fail(f"{pointer}/{required}", "required StyleSpec key missing")

    # font: str
    _require_string(spec["font"], f"{pointer}/font")

    # size_pt: number > 0
    _require_number_positive(spec["size_pt"], f"{pointer}/size_pt")

    # color: hex literal OR @palette-token
    color_hex = _validate_color_field(spec["color"], f"{pointer}/color", palette)

    # Optional fields with defaults
    bold = spec.get("bold", False)
    _require_bool(bold, f"{pointer}/bold")

    italic = spec.get("italic", False)
    _require_bool(italic, f"{pointer}/italic")

    alignment = spec.get("alignment", "left")
    _require_string(alignment, f"{pointer}/alignment")
    if alignment not in _ALIGNMENT_VALUES:
        raise _fail(
            f"{pointer}/alignment",
            f"expected one of {sorted(_ALIGNMENT_VALUES)}, got {alignment!r}",
        )

    spacing_before = spec.get("spacing_before_pt", 0)
    _require_number_nonneg(spacing_before, f"{pointer}/spacing_before_pt")

    spacing_after = spec.get("spacing_after_pt", 0)
    _require_number_nonneg(spacing_after, f"{pointer}/spacing_after_pt")

    line_height = spec.get("line_height", 1.15)
    _require_number_positive(line_height, f"{pointer}/line_height")

    keep_with_next = spec.get("keep_with_next", False)
    _require_bool(keep_with_next, f"{pointer}/keep_with_next")

    return Style(
        font=spec["font"],
        size_pt=float(spec["size_pt"]),
        color_hex=color_hex,
        bold=bold,
        italic=italic,
        alignment=alignment,
        spacing_before_pt=float(spacing_before),
        spacing_after_pt=float(spacing_after),
        line_height=float(line_height),
        keep_with_next=keep_with_next,
    )


def _validate_typography(
    typography: Any, pointer: str, palette: Mapping[str, str]
) -> dict[str, Style]:
    if not isinstance(typography, dict):
        raise _fail(pointer, f"expected object, got {type(typography).__name__}")
    for key in _REQUIRED_TYPOGRAPHY_KEYS:
        if key not in typography:
            raise _fail(f"{pointer}/{key}", "required typography key missing")
    out: dict[str, Style] = {}
    # Materialize ALL typography keys (required + extras) — Phase-7 tests
    # assert only on the six required keys but extras are kept usable.
    for key, spec in typography.items():
        out[key] = _validate_stylespec(spec, f"{pointer}/{key}", palette)
    return out


def _validate_spacing(spacing: Any, pointer: str) -> None:
    if not isinstance(spacing, dict):
        raise _fail(pointer, f"expected object, got {type(spacing).__name__}")
    for key in _REQUIRED_SPACING_KEYS:
        if key not in spacing:
            raise _fail(f"{pointer}/{key}", "required spacing key missing")
    _require_number_nonneg(
        spacing["paragraph_default_before_pt"], f"{pointer}/paragraph_default_before_pt"
    )
    _require_number_nonneg(
        spacing["paragraph_default_after_pt"], f"{pointer}/paragraph_default_after_pt"
    )
    _require_number_positive(
        spacing["default_line_height"], f"{pointer}/default_line_height"
    )
    _require_number_nonneg(
        spacing["table_cell_padding_pt"], f"{pointer}/table_cell_padding_pt"
    )


def _validate_version(version: Any) -> None:
    _require_string(version, "/version")
    parts = version.split(".")
    if not parts or not parts[0].isdigit():
        raise _fail("/version", f"expected SemVer-style 'MAJOR.MINOR', got {version!r}")
    major = parts[0]
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise _fail(
            "/version",
            f"unsupported schema major {major!r}; Phase-7 supports {SUPPORTED_SCHEMA_MAJOR}.x",
        )


def _validate_preset(data: Any) -> dict[str, Style]:
    """Validate a preset dict; return materialized typography styles."""
    if not isinstance(data, dict):
        raise _fail("", f"expected object at root, got {type(data).__name__}")
    for key in _REQUIRED_TOP_LEVEL:
        if key not in data:
            raise _fail(f"/{key}", "required top-level key missing")
    _require_string(data["name"], "/name")
    _validate_version(data["version"])
    _validate_color_palette(data["color_palette"], "/color_palette")
    palette: Mapping[str, str] = data["color_palette"]
    typography = _validate_typography(data["typography"], "/typography", palette)
    _validate_spacing(data["spacing"], "/spacing")
    return typography


# ---------------------------------------------------------------------------
# load_preset
# ---------------------------------------------------------------------------


# START_BLOCK_LOAD_PRESET
def load_preset(
    name: str | None = None, path: Path | None = None
) -> SimpleNamespace:
    """Load a style preset by registry name OR JSON file path.

    Exactly one of ``name`` or ``path`` must be supplied.

    Returns a ``types.SimpleNamespace`` whose attributes are the materialized
    typography styles (``heading1``, ``heading2``, ``heading3``, ``body``,
    ``table_header``, ``caption`` plus any extras present in the preset),
    each a frozen :class:`Style` instance.

    Raises:
        ValueError: when neither or both of name/path supplied.
        STYLE_PRESET_NOT_FOUND: registry miss OR path not found.
        STYLE_PRESET_INVALID_SCHEMA: preset fails schema validation; message
            contains the failing JSON Pointer plus the constraint name.
    """
    if (name is None) == (path is None):
        raise ValueError(
            "load_preset requires exactly one of name= or path= (got "
            f"name={name!r}, path={path!r})"
        )

    if name is not None:
        preset_name = name
        source_token = "registry"
        if name not in BUILTIN_PRESETS:
            available = sorted(BUILTIN_PRESETS)
            raise STYLE_PRESET_NOT_FOUND(
                f"preset {name!r} not in built-in registry; available: {available}"
            )
        json_path: Path = BUILTIN_PRESETS[name]
        try:
            raw = json_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:  # pragma: no cover
            # Should be unreachable for built-ins; guarded for completeness.
            raise STYLE_PRESET_NOT_FOUND(
                f"built-in preset file missing on disk: {json_path}"
            ) from exc
    else:
        # path-load branch — guaranteed non-None by the gate above.
        assert path is not None  # for mypy
        source_token = "file"
        if not path.exists():
            raise STYLE_PRESET_NOT_FOUND(f"preset path does not exist: {path}")
        raw = path.read_text(encoding="utf-8")
        # preset_name reflects the on-disk content; falls back to filename if
        # the JSON is malformed and we never reach the dict.
        preset_name = path.stem

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _fail("", f"invalid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})") from exc

    typography = _validate_preset(data)
    # Use the preset's declared name for the marker payload when present.
    if isinstance(data, dict) and isinstance(data.get("name"), str):
        preset_name = data["name"]

    logger.info(
        "[MP-Style][load_preset][BLOCK_LOAD_PRESET] preset=%s source=%s",
        preset_name,
        source_token,
    )
    return SimpleNamespace(**typography)
# END_BLOCK_LOAD_PRESET


__all__ = [
    "BUILTIN_PRESETS",
    "STYLE_PRESET_INVALID_SCHEMA",
    "STYLE_PRESET_NOT_FOUND",
    "SUPPORTED_SCHEMA_MAJOR",
    "ColorPalette",
    "Pt",
    "Style",
    "load_preset",
]
