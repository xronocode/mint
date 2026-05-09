# FILE: tests/unit/test_mp_sdk.py
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-SDK scenarios 1-4 covering the public re-export surface.
#     Confirms §3 names import, the presets registry is the read-only
#     Phase-7 three, with_style_from returns self for chaining, and
#     re-exports are identity (not duplicates).
#   SCOPE: Pure-Python unit tests; no docx I/O. Uses mp_clean_env autouse.
#   DEPENDS: pytest, mint_python.sdk, mint_python.core.* (identity check).
#   LINKS: docs/verification-plan.xml#V-MP-SDK,
#     docs/development-plan.xml#MP-SDK
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_scenario_1_all_section_3_names_import - V-MP-SDK scenario-1
#   test_scenario_2_presets_registry_contains_phase7_three - scenario-2
#   test_scenario_3_with_style_from_loads_and_returns_self_for_chaining - scenario-3
#   test_scenario_4_re_exports_are_identity - scenario-4
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-5 - initial provisioning: V-MP-SDK scenarios 1-4.
# END_CHANGE_SUMMARY
from __future__ import annotations

import json
from pathlib import Path

import pytest


# START_BLOCK_TEST_SCENARIO_1
def test_scenario_1_all_section_3_names_import() -> None:
    """V-MP-SDK scenario-1: all 9 §3 names resolve via `from mint_python.sdk import …`."""
    from mint_python.sdk import (
        TOC,
        ColorPalette,
        Document,
        Image,
        Pt,
        Section,
        Style,
        Table,
        presets,
    )

    # Each must be a non-None bound symbol.
    for sym in (Document, Section, Table, Style, Image, TOC, Pt, ColorPalette, presets):
        assert sym is not None
# END_BLOCK_TEST_SCENARIO_1


# START_BLOCK_TEST_SCENARIO_2
def test_scenario_2_presets_registry_contains_phase7_three() -> None:
    """V-MP-SDK scenario-2: presets is the Phase-7 three and is read-only."""
    from mint_python.sdk import presets

    assert frozenset(presets.keys()) == {"alga_corporate", "minimal", "compact"}

    # MappingProxyType disallows mutation.
    with pytest.raises(TypeError):
        presets["new"] = Path("/tmp/x.json")  # type: ignore[index]
# END_BLOCK_TEST_SCENARIO_2


# START_BLOCK_TEST_SCENARIO_3
def test_scenario_3_with_style_from_loads_and_returns_self_for_chaining(
    tmp_path: Path,
) -> None:
    """V-MP-SDK scenario-3: Document.with_style_from(path) returns self for chaining.

    Builds a custom JSON preset (copy of alga_corporate's body with a renamed
    name field), loads via the public Document.with_style_from path, and
    asserts the returned object IS the original instance (fluent contract).
    """
    from mint_python.core.style import BUILTIN_PRESETS
    from mint_python.sdk import Document

    custom = tmp_path / "custom.json"
    payload = json.loads(BUILTIN_PRESETS["alga_corporate"].read_text())
    payload["name"] = "custom"
    custom.write_text(json.dumps(payload))

    doc = Document(format="docx", title="X")
    returned = doc.with_style_from(custom)
    assert returned is doc
    assert isinstance(returned, Document)
# END_BLOCK_TEST_SCENARIO_3


# START_BLOCK_TEST_SCENARIO_4
def test_scenario_4_re_exports_are_identity() -> None:
    """V-MP-SDK scenario-4: SDK names ARE the core names (no duplicate classes)."""
    import mint_python.core.content
    import mint_python.core.document
    import mint_python.core.section
    import mint_python.core.style
    import mint_python.core.table
    import mint_python.sdk as sdk

    assert sdk.Document is mint_python.core.document.Document
    assert sdk.Section is mint_python.core.section.Section
    assert sdk.Table is mint_python.core.table.Table
    assert sdk.Style is mint_python.core.style.Style
    assert sdk.Image is mint_python.core.content.Image
    assert sdk.Pt is mint_python.core.style.Pt
    assert sdk.ColorPalette is mint_python.core.style.ColorPalette
    assert sdk.presets is mint_python.core.style.BUILTIN_PRESETS
# END_BLOCK_TEST_SCENARIO_4
