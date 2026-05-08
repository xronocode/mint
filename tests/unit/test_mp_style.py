# FILE: tests/unit/test_mp_style.py
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-STYLE verification — covers scenarios 1-13 + forbidden-1..4
#     for mint_python.core.style (Style, Pt, ColorPalette, load_preset,
#     BUILTIN_PRESETS) per docs/verification-plan.xml#V-MP-STYLE and
#     docs/style-preset-schema.md.
#   SCOPE: Unit-level tests only. Reuses central fixtures from
#     tests/unit/conftest.py (mp_clean_env, caplog_at_info, marker_counter,
#     schema_violation_factory). Does NOT touch mint_python.sdk (Wave-7-5).
#   DEPENDS: pytest, mint_python.core.style, tests/unit/conftest.py fixtures,
#     tests/unit/_mp_helpers.py (extract_marker imported only when explicitly
#     useful — marker_counter fixture is the primary path).
#   LINKS: docs/verification-plan.xml#V-MP-STYLE,
#     docs/style-preset-schema.md, docs/development-plan.xml#MP-STYLE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_pt_*                     - V-MP-STYLE scenario-1
#   test_color_palette_*          - scenario-2
#   test_load_preset_alga_*       - scenario-3
#   test_load_preset_from_path_*  - scenario-4 + forbidden-3 (registry not mutated)
#   test_load_preset_unknown_*    - scenario-5
#   test_load_preset_schema_*     - scenario-6 (parametrized via schema_violation_factory)
#   test_style_frozen_*           - scenario-7 + forbidden-1
#   test_load_preset_marker_*     - scenario-8 (BLOCK_LOAD_PRESET emit format)
#   test_palette_token_*          - scenario-9
#   test_dangling_token_*         - scenario-10
#   test_stylespec_defaults_*     - scenario-11
#   test_version_mismatch_*       - scenario-12
#   test_registry_integrity_*     - scenario-13 + forbidden-4
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-1 (MP-STYLE): initial test suite for scenarios 1-13.
# END_CHANGE_SUMMARY
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from mint_python.core.style import (
    BUILTIN_PRESETS,
    STYLE_PRESET_INVALID_SCHEMA,
    STYLE_PRESET_NOT_FOUND,
    ColorPalette,
    Pt,
    Style,
    load_preset,
)
from tests.unit._mp_helpers import extract_marker

_REQUIRED_TYPOGRAPHY_KEYS = (
    "heading1",
    "heading2",
    "heading3",
    "body",
    "table_header",
    "caption",
)


# ---------------------------------------------------------------------------
# scenario-1: Pt twentieths-of-a-point
# ---------------------------------------------------------------------------


def test_pt_returns_twentieths_for_integer():
    assert Pt(12) == 240


def test_pt_returns_twentieths_for_fraction():
    assert Pt(0.5) == 10


def test_pt_zero_returns_zero():
    assert Pt(0) == 0


# ---------------------------------------------------------------------------
# scenario-2: ColorPalette.resolve happy path + KeyError
# ---------------------------------------------------------------------------


def test_color_palette_resolves_alga_primary():
    palette = ColorPalette("alga_corporate")
    assert palette.resolve("primary") == "#0F4C81"


def test_color_palette_unknown_key_raises_naming_palette_and_key():
    palette = ColorPalette("alga_corporate")
    with pytest.raises(KeyError) as excinfo:
        palette.resolve("does_not_exist")
    msg = str(excinfo.value)
    assert "alga_corporate" in msg
    assert "does_not_exist" in msg


# ---------------------------------------------------------------------------
# scenario-3: load_preset('alga_corporate') populates required keys
# ---------------------------------------------------------------------------


def test_load_preset_alga_returns_namespace_with_required_styles():
    ns = load_preset("alga_corporate")
    assert isinstance(ns, SimpleNamespace)
    for key in _REQUIRED_TYPOGRAPHY_KEYS:
        style = getattr(ns, key)
        assert isinstance(style, Style)
        # Frozen Style instances carry the schema's required fields.
        assert style.font
        assert style.size_pt > 0
        assert style.color_hex.startswith("#") and len(style.color_hex) == 7


# ---------------------------------------------------------------------------
# scenario-4: load_preset(path=tmp_json) loads from arbitrary file
# ---------------------------------------------------------------------------


def _write_preset(tmp_path: Path, data: dict, filename: str = "custom.json") -> Path:
    p = tmp_path / filename
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_preset_from_path(tmp_path: Path, schema_violation_factory):
    # Use the factory's base preset by triggering a no-op then re-mutating
    # back; simpler — we re-use the alga_corporate registry data.
    alga_path = BUILTIN_PRESETS["alga_corporate"]
    data = json.loads(alga_path.read_text(encoding="utf-8"))
    custom_path = _write_preset(tmp_path, data, "external.json")
    ns = load_preset(path=custom_path)
    assert isinstance(ns, SimpleNamespace)
    for key in _REQUIRED_TYPOGRAPHY_KEYS:
        assert isinstance(getattr(ns, key), Style)


# forbidden-3: load_preset(path=...) MUST NOT mutate BUILTIN_PRESETS
def test_path_load_does_not_mutate_registry(tmp_path: Path):
    before_keys = frozenset(BUILTIN_PRESETS.keys())
    before_paths = {k: BUILTIN_PRESETS[k] for k in before_keys}
    alga_data = json.loads(BUILTIN_PRESETS["alga_corporate"].read_text(encoding="utf-8"))
    # Mutate the on-disk copy to make it visibly distinct (different name).
    alga_data["name"] = "external_clone"
    custom_path = _write_preset(tmp_path, alga_data, "clone.json")
    load_preset(path=custom_path)
    after_keys = frozenset(BUILTIN_PRESETS.keys())
    assert after_keys == before_keys
    for k in before_keys:
        assert BUILTIN_PRESETS[k] == before_paths[k]
    # MappingProxyType prevents direct mutation; verify the type as well.
    with pytest.raises(TypeError):
        BUILTIN_PRESETS["external_clone"] = custom_path  # type: ignore[index]


# ---------------------------------------------------------------------------
# scenario-5: load_preset('does_not_exist') raises STYLE_PRESET_NOT_FOUND
# ---------------------------------------------------------------------------


def test_load_preset_unknown_name_raises_naming_registry_contents():
    with pytest.raises(STYLE_PRESET_NOT_FOUND) as excinfo:
        load_preset("does_not_exist")
    msg = str(excinfo.value)
    assert "does_not_exist" in msg
    # Registry contents named in the message
    for known in BUILTIN_PRESETS:
        assert known in msg


def test_load_preset_path_missing_raises_not_found(tmp_path: Path):
    missing = tmp_path / "nope.json"
    with pytest.raises(STYLE_PRESET_NOT_FOUND) as excinfo:
        load_preset(path=missing)
    assert str(missing) in str(excinfo.value)


def test_load_preset_requires_exactly_one_of_name_or_path():
    with pytest.raises(ValueError):
        load_preset()
    with pytest.raises(ValueError):
        load_preset(name="alga_corporate", path=Path("/tmp/x.json"))


# ---------------------------------------------------------------------------
# scenario-6: parametrized schema violations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("violation_kind", "expected_pointer_part", "constraint_keyword"),
    [
        ("missing-color-palette-primary", "/color_palette/primary", "required"),
        ("bad-hex-format", "/color_palette/primary", "hex"),
        ("size-pt-as-string", "/typography/heading1/size_pt", "number"),
        ("alignment-out-of-enum", "/typography/body/alignment", "left"),
        ("dangling-palette-token", "/typography/heading1/color", "nonexistent"),
        ("version-mismatch-major", "/version", "1"),
    ],
)
def test_load_preset_schema_violations(
    tmp_path: Path,
    schema_violation_factory,
    violation_kind: str,
    expected_pointer_part: str,
    constraint_keyword: str,
):
    broken = schema_violation_factory(violation_kind)
    broken_path = _write_preset(tmp_path, broken, f"broken_{violation_kind}.json")
    with pytest.raises(STYLE_PRESET_INVALID_SCHEMA) as excinfo:
        load_preset(path=broken_path)
    msg = str(excinfo.value)
    assert expected_pointer_part in msg, (
        f"expected pointer fragment {expected_pointer_part!r} in message {msg!r}"
    )
    assert constraint_keyword in msg, (
        f"expected constraint keyword {constraint_keyword!r} in message {msg!r}"
    )


# ---------------------------------------------------------------------------
# scenario-7 + forbidden-1: Style frozen invariant
# ---------------------------------------------------------------------------


def test_style_is_frozen():
    style = Style(font="Inter", size_pt=11, color_hex="#000000")
    with pytest.raises(FrozenInstanceError):
        style.color_hex = "#ffffff"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# scenario-8: BLOCK_LOAD_PRESET marker emit
# ---------------------------------------------------------------------------


def test_load_preset_emits_block_load_preset_for_registry(
    caplog_at_info, marker_counter
):
    load_preset("alga_corporate")
    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_LOAD_PRESET"] == 1
    # Verify payload format: preset=<name> source=registry
    matching = [
        r.getMessage()
        for r in caplog_at_info.records
        if extract_marker(r.getMessage()) == "BLOCK_LOAD_PRESET"
    ]
    assert any(
        "preset=alga_corporate" in m and "source=registry" in m for m in matching
    ), f"missing payload in marker(s): {matching!r}"


def test_load_preset_emits_block_load_preset_for_path(
    tmp_path: Path, caplog_at_info, marker_counter
):
    alga_data = json.loads(BUILTIN_PRESETS["alga_corporate"].read_text(encoding="utf-8"))
    alga_data["name"] = "from_file_test"
    custom = _write_preset(tmp_path, alga_data, "from_file.json")
    load_preset(path=custom)
    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_LOAD_PRESET"] == 1
    matching = [
        r.getMessage()
        for r in caplog_at_info.records
        if extract_marker(r.getMessage()) == "BLOCK_LOAD_PRESET"
    ]
    assert any(
        "preset=from_file_test" in m and "source=file" in m for m in matching
    ), f"missing payload in marker(s): {matching!r}"


# ---------------------------------------------------------------------------
# scenario-9: palette token resolution
# ---------------------------------------------------------------------------


def test_palette_token_resolves_to_literal_hex():
    # alga_corporate's heading1.color is "@primary" in the JSON; after
    # load_preset the Style.color_hex MUST be the resolved literal hex.
    ns = load_preset("alga_corporate")
    assert ns.heading1.color_hex == "#0F4C81"
    # Round-trip integrity: re-loading does not re-tokenize.
    ns2 = load_preset("alga_corporate")
    assert ns2.heading1.color_hex == "#0F4C81"


# ---------------------------------------------------------------------------
# scenario-10: dangling token (also covered by scenario-6 parametrize, but the
# spec calls this out explicitly so we keep a focused test).
# ---------------------------------------------------------------------------


def test_dangling_palette_token_raises_naming_field_and_token(
    tmp_path: Path, schema_violation_factory
):
    broken = schema_violation_factory("dangling-palette-token")
    broken_path = _write_preset(tmp_path, broken, "dangling.json")
    with pytest.raises(STYLE_PRESET_INVALID_SCHEMA) as excinfo:
        load_preset(path=broken_path)
    msg = str(excinfo.value)
    assert "/typography/heading1/color" in msg
    assert "nonexistent" in msg


# ---------------------------------------------------------------------------
# scenario-11: StyleSpec defaults
# ---------------------------------------------------------------------------


def test_stylespec_defaults_when_only_required_fields_supplied(tmp_path: Path):
    minimal_preset = {
        "$schema": "https://mint.dev/schema/style-preset-1.0.json",
        "name": "defaults_test",
        "version": "1.0",
        "color_palette": {
            "primary": "#0F4C81",
            "secondary": "#5B8DBE",
            "accent": "#FFB400",
            "text": "#1A1A1A",
            "text_muted": "#6E6E6E",
            "background": "#FFFFFF",
            "border": "#D4D4D4",
        },
        # Each StyleSpec carries ONLY the three required fields.
        "typography": {
            "heading1": {"font": "Inter", "size_pt": 24, "color": "#0F4C81"},
            "heading2": {"font": "Inter", "size_pt": 18, "color": "#0F4C81"},
            "heading3": {"font": "Inter", "size_pt": 14, "color": "#0F4C81"},
            "body": {"font": "Inter", "size_pt": 11, "color": "#1A1A1A"},
            "table_header": {"font": "Inter", "size_pt": 11, "color": "#FFFFFF"},
            "caption": {"font": "Inter", "size_pt": 9, "color": "#6E6E6E"},
        },
        "spacing": {
            "paragraph_default_before_pt": 0,
            "paragraph_default_after_pt": 6,
            "default_line_height": 1.15,
            "table_cell_padding_pt": 4,
        },
    }
    p = _write_preset(tmp_path, minimal_preset, "defaults.json")
    ns = load_preset(path=p)
    body = ns.body
    assert body.bold is False
    assert body.italic is False
    assert body.alignment == "left"
    assert body.spacing_before_pt == 0
    assert body.spacing_after_pt == 0
    assert body.line_height == 1.15
    assert body.keep_with_next is False


# ---------------------------------------------------------------------------
# scenario-12: version mismatch on major
# ---------------------------------------------------------------------------


def test_version_mismatch_major_raises_invalid_schema(
    tmp_path: Path, schema_violation_factory
):
    broken = schema_violation_factory("version-mismatch-major")
    p = _write_preset(tmp_path, broken, "v2.json")
    with pytest.raises(STYLE_PRESET_INVALID_SCHEMA) as excinfo:
        load_preset(path=p)
    msg = str(excinfo.value)
    assert "version" in msg
    # Phase-7 supports major "1.x"
    assert "1" in msg


# ---------------------------------------------------------------------------
# scenario-13 + forbidden-4: registry integrity at module load
# ---------------------------------------------------------------------------


def test_registry_contains_exactly_three_phase7_presets():
    assert frozenset(BUILTIN_PRESETS.keys()) == frozenset(
        {"alga_corporate", "minimal", "compact"}
    )


def test_every_builtin_preset_validates_and_populates_required_styles():
    # forbidden-4: every built-in preset must validate at module load (here,
    # via load_preset which performs the same validation that is performed at
    # any future module-import-time invocation).
    for name in BUILTIN_PRESETS:
        ns = load_preset(name)
        assert isinstance(ns, SimpleNamespace)
        for key in _REQUIRED_TYPOGRAPHY_KEYS:
            assert isinstance(getattr(ns, key), Style)


def test_registry_is_immutable_view():
    # Defense-in-depth: the registry is exposed as a MappingProxyType so even
    # in-process code can't insert a rogue entry.
    with pytest.raises(TypeError):
        BUILTIN_PRESETS["new"] = Path("/tmp/x.json")  # type: ignore[index]
    with pytest.raises(TypeError):
        del BUILTIN_PRESETS["alga_corporate"]  # type: ignore[attr-defined]
