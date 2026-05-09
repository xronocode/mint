# FILE: tests/unit/conftest.py
# START_MODULE_CONTRACT
#   PURPOSE: Shared pytest fixtures for Phase-7 (Pure Python Edition Phase 1) MP-* tests.
#     Controller-owned per docs/verification-plan.xml SwarmFixtures/ownership.
#   SCOPE: Provides mp_clean_env autouse + 6 opt-in fixtures consumed by V-MP-* tests
#     and the VF-013 e2e harness. Does NOT redefine the central clean_env fixture in
#     tests/unit/test_config.py — V-M-CONFIG forbidden-4 keeps that as the single
#     chokepoint for MINT_ENGINE / required-LLM env scrubbing.
#   DEPENDS: pytest, mint_python.sdk (lazy; only the fixtures that touch presets
#     import it; absence is tolerated until Wave-7-1 lands MP-STYLE).
#   LINKS: docs/verification-plan.xml#SwarmFixtures, docs/verification-plan.xml#V-MP-STYLE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   mp_clean_env - autouse: scrub MP_E2E_WRITE_BASELINE + restore presets registry
#   tmp_docx_path - tmp_path / "out.docx"
#   mp_minimal_config - frozen MintConfig(engine=PYTHON, severity=LENIENT)
#   caplog_at_info - caplog wrapper with set_level(INFO)
#   marker_counter - callable: caplog -> Counter[BLOCK_NAME -> count]
#   golden_doc_builder - returns _mp_helpers.build_golden_document
#   schema_violation_factory - parametrized broken-preset producer for V-MP-STYLE
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Phase-7 pre-Wave-7-1: initial provisioning per SwarmFixtures/conftest-spec
# END_CHANGE_SUMMARY
from __future__ import annotations

import logging
import sys
from collections import Counter
from pathlib import Path

import pytest


# START_BLOCK_MP_CLEAN_ENV
@pytest.fixture(autouse=True)
def mp_clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub Phase-7 env vars + snapshot/restore mint_python.sdk.presets registry.

    Complementary to (NOT replacing) clean_env in tests/unit/test_config.py.
    See docs/verification-plan.xml#SwarmFixtures/conftest-spec/fixture-1.
    """
    monkeypatch.delenv("MP_E2E_WRITE_BASELINE", raising=False)

    # Snapshot the presets registry if mint_python.sdk has been imported.
    # Restore at teardown — guards V-MP-STYLE forbidden-3 (no registry mutation
    # by load_preset(path=...)) even if a regression slips in.
    sdk_module = sys.modules.get("mint_python.sdk")
    if sdk_module is not None and hasattr(sdk_module, "presets"):
        snapshot = dict(sdk_module.presets)
        yield
        # Phase-7 Wave-7-5: presets is a MappingProxyType alias to
        # BUILTIN_PRESETS (V-MP-SDK scenario-2 mandates read-only). It cannot
        # be cleared/updated. The snapshot/restore is a no-op safety net that
        # only meaningfully runs if a future regression replaces presets with
        # a mutable Mapping. Verify identity preservation as the actual guard.
        if hasattr(sdk_module.presets, "clear") and hasattr(
            sdk_module.presets, "update"
        ):
            sdk_module.presets.clear()
            sdk_module.presets.update(snapshot)
        else:
            assert dict(sdk_module.presets) == snapshot, (
                "mint_python.sdk.presets mutated during test (read-only contract)"
            )
    else:
        yield
# END_BLOCK_MP_CLEAN_ENV


# START_BLOCK_TMP_DOCX_PATH
@pytest.fixture
def tmp_docx_path(tmp_path: Path) -> Path:
    """Standard tmp_path / 'out.docx' — V-MP-DOCUMENT forbidden-3 mandates tmp_path."""
    return tmp_path / "out.docx"
# END_BLOCK_TMP_DOCX_PATH


# START_BLOCK_MP_MINIMAL_CONFIG
@pytest.fixture
def mp_minimal_config(tmp_path: Path):
    """Frozen MintConfig with engine=PYTHON, severity=LENIENT, sentinel LLM fields.

    Used by VF-013 e2e harness to invoke mint.validate.validate(saved, config)
    in-process without going through M-CLI.
    """
    from mint.config import Engine, MintConfig, SeverityMode, Tier

    return MintConfig(
        llm_base_url="http://x:1",
        llm_model="m",
        model_tier=Tier.SMALL,
        severity_mode=SeverityMode.LENIENT,
        sandbox_timeout=30,
        rules_dir=tmp_path / "rules",
        skills_dir=tmp_path / "skills",
        templates_dir=tmp_path / "templates",
        tokens_dir=tmp_path / "tokens",
        engine=Engine.PYTHON,
    )
# END_BLOCK_MP_MINIMAL_CONFIG


# START_BLOCK_CAPLOG_AT_INFO
@pytest.fixture
def caplog_at_info(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """caplog wrapper pre-configured at INFO level so MP-* markers are captured."""
    caplog.set_level(logging.INFO)
    return caplog
# END_BLOCK_CAPLOG_AT_INFO


# START_BLOCK_MARKER_COUNTER
@pytest.fixture
def marker_counter():
    """Returns a callable: count(caplog) -> Counter[BLOCK_NAME -> count].

    Encapsulates the caplog -> marker-name extraction so tests assert
    against a Counter rather than parsing log strings inline.
    """
    from tests.unit._mp_helpers import extract_marker

    def _count(caplog: pytest.LogCaptureFixture) -> Counter[str]:
        return Counter(
            m for m in (extract_marker(r.getMessage()) for r in caplog.records) if m
        )

    return _count
# END_BLOCK_MARKER_COUNTER


# START_BLOCK_GOLDEN_DOC_BUILDER
@pytest.fixture
def golden_doc_builder():
    """Returns _mp_helpers.build_golden_document — VF-013 single source of truth."""
    from tests.unit._mp_helpers import build_golden_document

    return build_golden_document
# END_BLOCK_GOLDEN_DOC_BUILDER


# START_BLOCK_SCHEMA_VIOLATION_FACTORY
@pytest.fixture
def schema_violation_factory():
    """Returns a callable producing minimally-broken preset dicts for V-MP-STYLE scenario-6."""
    base = {
        "$schema": "https://mint.dev/schema/style-preset-1.0.json",
        "name": "broken_test",
        "version": "1.0",
        "description": "intentionally broken for schema-violation tests",
        "color_palette": {
            "primary": "#0F4C81",
            "secondary": "#5B8DBE",
            "accent": "#FFB400",
            "text": "#1A1A1A",
            "text_muted": "#6E6E6E",
            "background": "#FFFFFF",
            "border": "#D4D4D4",
        },
        "typography": {
            "heading1": {"font": "Inter", "size_pt": 24, "color": "#0F4C81"},
            "heading2": {"font": "Inter", "size_pt": 18, "color": "#0F4C81"},
            "heading3": {"font": "Inter", "size_pt": 14, "color": "#0F4C81"},
            "body": {"font": "Inter", "size_pt": 11, "color": "#1A1A1A"},
            "table_header": {"font": "Inter", "size_pt": 11, "color": "#FFFFFF", "bold": True},
            "caption": {"font": "Inter", "size_pt": 9, "color": "#6E6E6E", "italic": True},
        },
        "spacing": {
            "paragraph_default_before_pt": 0,
            "paragraph_default_after_pt": 6,
            "default_line_height": 1.15,
            "table_cell_padding_pt": 4,
        },
    }

    def _factory(violation_kind: str) -> dict:
        import copy

        d = copy.deepcopy(base)
        if violation_kind == "missing-color-palette-primary":
            del d["color_palette"]["primary"]
        elif violation_kind == "bad-hex-format":
            d["color_palette"]["primary"] = "rgb(15, 76, 129)"
        elif violation_kind == "size-pt-as-string":
            d["typography"]["heading1"]["size_pt"] = "24"
        elif violation_kind == "alignment-out-of-enum":
            d["typography"]["body"]["alignment"] = "diagonal"
        elif violation_kind == "dangling-palette-token":
            d["typography"]["heading1"]["color"] = "@nonexistent"
        elif violation_kind == "version-mismatch-major":
            d["version"] = "2.0"
        else:
            raise ValueError(f"unknown violation_kind: {violation_kind}")
        return d

    return _factory
# END_BLOCK_SCHEMA_VIOLATION_FACTORY
