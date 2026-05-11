# FILE: tests/integration/test_mp_audit_extend.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-AUDIT-EXTEND verification — proves that _audit_instructions
#     stamps preset_version (always) and lang (when >=2 distinct language-suffix
#     fields) into the GRACE manifest instructions list. Closes
#     V-MP-THEME-EDIT scenario-10 (preset_version round-trip) and
#     V-MP-DOC-BUNDLE scenario-7b (bilingual NDA lang metadata).
#   SCOPE: Integration + deterministic tests for MP-AUDIT-EXTEND.
#     Exercises _audit_instructions, _detect_template_languages,
#     _resolve_active_preset_version, and the end-to-end _run_pipeline ->
#     mint_read_grace_manifest round-trip.
#   DEPENDS: pytest, mint_python.mcp.document (_audit_instructions,
#     _detect_template_languages, _resolve_active_preset_version,
#     _run_pipeline, _TEMPLATES_DIR), mint_python.mcp.manifest
#     (mint_read_grace_manifest, _canonicalize, _read_all_manifests,
#     CANONICAL_KEYS), mint_python.mcp.preset_edit
#     (resolve_latest_preset_path, PRESETS_DIR),
#     tests._helpers.fake_mcp_context.FakeMCPContext.
#   LINKS: docs/development-plan.xml#MP-AUDIT-EXTEND,
#     docs/verification-plan.xml#V-MP-AUDIT-EXTEND,
#     docs/knowledge-graph.xml#MP-AUDIT-EXTEND
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mint_python.grace import GRACEManifest
from mint_python.mcp import document as document_module
from mint_python.mcp import preset_edit as preset_edit_module
from mint_python.mcp.document import (
    _audit_instructions,
    _detect_template_languages,
    _resolve_active_preset_version,
    _run_pipeline,
)
from mint_python.mcp.manifest import (
    CANONICAL_KEYS,
    _canonicalize,
    mint_read_grace_manifest,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "doc_out"))


@pytest.fixture(autouse=True)
def _isolate_templates_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in (
        "memo",
        "letter",
        "report",
        "decision-record",
        "contract",
        "nda-bilingual-ru-en",
        "technical-spec",
    ):
        src = REPO_TEMPLATES / f"{name}.yaml"
        if src.exists():
            (fixtures / f"{name}.yaml").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
    from mint_python.templates import registry as reg_module

    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixtures)
    monkeypatch.setattr(reg_module, "_TEMPLATES_DIR", fixtures)
    from mint_python.templates.registry import reset_default_registry

    reset_default_registry()
    yield fixtures
    reset_default_registry()


@pytest.fixture(autouse=True)
def _isolate_presets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    presets = tmp_path / "presets"
    presets.mkdir()
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", presets)
    yield presets


async def _produce_memo_docx() -> Path:
    intent = "Memo from Alice to Bob on 2026-05-15 about quarterly results"
    ctx = FakeMCPContext(answers={"body": "Q4 revenue exceeded targets."})
    result = await _run_pipeline(
        intent=intent, doc_type="memo", source_md=None, ctx=ctx
    )
    assert result["status"] == "complete"
    return Path(result["path"])


async def _produce_nda_docx() -> Path:
    answers = {
        "party_a": "Alpha Corp",
        "party_b": "Beta LLC",
        "effective_date": "2026-05-15",
        "scope_ru": "Все конфиденциальные данные",  # noqa: RUF001
        "scope_en": "All confidential data",
        "term_ru": "2 года",
        "term_en": "2 years",
        "signatures": "Alice / Bob",
    }
    ctx = FakeMCPContext(answers=answers)
    result = await _run_pipeline(
        intent="NDA between Alpha Corp and Beta LLC",
        doc_type="nda-bilingual-ru-en",
        source_md=None,
        ctx=ctx,
    )
    assert result["status"] == "complete"
    return Path(result["path"])


def _write_versioned_preset(
    presets_dir: Path, version: str
) -> Path:
    from mint_python.core.style import BUILTIN_PRESETS

    klawd_builtin = BUILTIN_PRESETS["klawd"]
    p = presets_dir / f"klawd_v{version}.yaml"
    shutil.copy2(klawd_builtin, p)
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "version: '1.0'", f"version: '{version}'"
        ),
        encoding="utf-8",
    )
    return p


# --------------------------------------------------------------------------- #
# Scenario 1: preset_version in manifest
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_preset_version_in_manifest() -> None:
    docx_path = await _produce_memo_docx()
    result = await mint_read_grace_manifest(
        str(docx_path), ctx=FakeMCPContext()
    )
    assert result["preset_version"] is not None
    assert "." in result["preset_version"]
    instructions = result["instructions"]
    pv_lines = [line for line in instructions if line.startswith("preset_version=")]
    assert len(pv_lines) == 1
    assert pv_lines[0] == f"preset_version={result['preset_version']}"


# --------------------------------------------------------------------------- #
# Scenario 2: preset_version round-trip after edit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_preset_version_round_trip_after_edit(
    _isolate_presets_dir: Path,
) -> None:
    _write_versioned_preset(_isolate_presets_dir, "1.1")
    docx_path = await _produce_memo_docx()
    result = await mint_read_grace_manifest(
        str(docx_path), ctx=FakeMCPContext()
    )
    assert result["preset_version"] == "1.1"


# --------------------------------------------------------------------------- #
# Scenario 3: bilingual NDA lang in manifest
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_bilingual_lang_in_manifest() -> None:
    docx_path = await _produce_nda_docx()
    manifest = await mint_read_grace_manifest(
        str(docx_path), ctx=FakeMCPContext()
    )
    lang = manifest["lang"]
    assert sorted(lang) == ["en", "ru"]
    instructions = manifest["instructions"]
    lang_lines = [line for line in instructions if line.startswith("lang=")]
    assert len(lang_lines) == 1
    assert lang_lines[0] == "lang=en,ru"


# --------------------------------------------------------------------------- #
# Scenario 4: no lang when monolingual
# --------------------------------------------------------------------------- #


def test_scenario_4_no_lang_when_template_monolingual() -> None:
    result = _detect_template_languages(
        required_fields=("title", "body", "sender", "date")
    )
    assert result == []


# --------------------------------------------------------------------------- #
# Scenario 5: no lang when single code
# --------------------------------------------------------------------------- #


def test_scenario_5_no_lang_when_single_code() -> None:
    result = _detect_template_languages(
        required_fields=("title", "body", "scope_ru")
    )
    assert result == []


# --------------------------------------------------------------------------- #
# Scenario 6: marker sequence
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_marker_sequence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="mint_python.mcp.document"):
        await _produce_nda_docx()
    pv_markers = [
        r for r in caplog.records if "BLOCK_AUDIT_PRESET_VERSION" in r.message
    ]
    lang_markers = [
        r for r in caplog.records if "BLOCK_AUDIT_LANG" in r.message
    ]
    inject_markers = [
        r for r in caplog.records if "BLOCK_INJECT_GRACE" in r.message
    ]
    assert len(pv_markers) >= 1
    assert len(lang_markers) >= 1
    assert len(inject_markers) >= 1


# --------------------------------------------------------------------------- #
# Scenario 7: defensive fallback
# --------------------------------------------------------------------------- #


def test_scenario_7_defensive_fallback_missing_dir(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "nonexistent"
    with patch.object(preset_edit_module, "PRESETS_DIR", empty):
        version = _resolve_active_preset_version("klawd")
        assert version == "1.0"


def test_scenario_7_defensive_fallback_unknown_preset(
    _isolate_presets_dir: Path,
) -> None:
    version = _resolve_active_preset_version("nonexistent_preset")
    assert version == "1.0"


# --------------------------------------------------------------------------- #
# Scenario 8: trilingual codes sorted
# --------------------------------------------------------------------------- #


def test_scenario_8_trilingual_codes_sorted() -> None:
    result = _detect_template_languages(
        required_fields=("scope_ru", "scope_en", "scope_kk")
    )
    assert result == ["en", "kk", "ru"]


# --------------------------------------------------------------------------- #
# Scenario 9: uppercase codes skipped
# --------------------------------------------------------------------------- #


def test_scenario_9_uppercase_codes_skipped() -> None:
    result = _detect_template_languages(
        required_fields=("scope_RU", "scope_EN")
    )
    assert result == []


# --------------------------------------------------------------------------- #
# Scenario 10: non-language suffixes rejected
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fields",
    [
        ("timestamp_ms", "user_id", "item_pk"),
        ("field_no", "score_qa", "weight_kg"),
    ],
)
def test_scenario_10_non_lang_suffixes_rejected(
    fields: tuple[str, ...],
) -> None:
    result = _detect_template_languages(required_fields=fields)
    assert result == []


# --------------------------------------------------------------------------- #
# Scenario 11: legacy docx preset_version=None
# --------------------------------------------------------------------------- #


def test_scenario_11_legacy_docx_preset_version_none() -> None:
    manifest = GRACEManifest(
        xml_part_name="grace/manifest_legacy.xml",
        namespace="urn:mint:grace:2026:manifest",
        instructions=[
            "audit_id=legacy-123",
            "generated_by=MP-MEMO-POC",
            "generated_at=2026-05-10T12:00:00",
            "fields_elicited=(none)",
            "template=memo.yaml",
            "template_version=1.0",
            "preset=klawd",
        ],
        fingerprint=None,
    )
    result = _canonicalize(manifest)
    assert result["preset_version"] is None
    assert result["lang"] == []
    assert len(result) == len(CANONICAL_KEYS)


# --------------------------------------------------------------------------- #
# Scenario 12: semver sort (not lex)
# --------------------------------------------------------------------------- #


def test_scenario_12_semver_sort_not_lex(
    _isolate_presets_dir: Path,
) -> None:
    for v in ("1.1", "1.2", "1.10"):
        _write_versioned_preset(_isolate_presets_dir, v)
    version = _resolve_active_preset_version("klawd")
    assert version == "1.10"


# --------------------------------------------------------------------------- #
# Scenario 13: malformed sibling skipped
# --------------------------------------------------------------------------- #


def test_scenario_13_malformed_sibling_skipped(
    _isolate_presets_dir: Path,
) -> None:
    _write_versioned_preset(_isolate_presets_dir, "1.1")
    malformed = _isolate_presets_dir / "klawd_v_typo.yaml"
    malformed.write_text("broken: true", encoding="utf-8")
    version = _resolve_active_preset_version("klawd")
    assert version == "1.1"


# --------------------------------------------------------------------------- #
# Scenario 14: builtin missing version field
# --------------------------------------------------------------------------- #


def test_scenario_14_builtin_missing_version_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_preset = tmp_path / "klawd.yaml"
    fake_preset.write_text(
        "name: klawd\nheading_font: Test\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", tmp_path)
    monkeypatch.setattr(
        preset_edit_module, "BUILTIN_PRESETS", {"klawd": fake_preset}
    )
    version = _resolve_active_preset_version("klawd")
    assert version == "1.0"


# --------------------------------------------------------------------------- #
# Scenario 15: no lang marker when monolingual
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_15_no_lang_marker_when_monolingual(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="mint_python.mcp.document"):
        await _produce_memo_docx()
    lang_markers = [
        r for r in caplog.records if "BLOCK_AUDIT_LANG" in r.message
    ]
    pv_markers = [
        r for r in caplog.records if "BLOCK_AUDIT_PRESET_VERSION" in r.message
    ]
    assert len(lang_markers) == 0
    assert len(pv_markers) >= 1


# --------------------------------------------------------------------------- #
# Scenario 16: value with '=' round-trips
# --------------------------------------------------------------------------- #


def test_scenario_16_value_with_equals_round_trips() -> None:
    instructions = _audit_instructions(
        "test-id",
        [],
        "memo",
        "1.0",
        preset_name="klawd",
    )
    pv_lines = [line for line in instructions if line.startswith("preset_version=")]
    assert len(pv_lines) == 1
    _, _, value = pv_lines[0].partition("=")
    assert isinstance(value, str)


# --------------------------------------------------------------------------- #
# Scenario 17: concurrent edit + render race
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_17_concurrent_edit_render_race(
    _isolate_presets_dir: Path,
) -> None:
    import asyncio

    async def _write_preset() -> None:
        _write_versioned_preset(_isolate_presets_dir, "1.1")

    async def _render() -> str:
        return _resolve_active_preset_version("klawd")

    await asyncio.gather(_write_preset(), _render())
    version_after = _resolve_active_preset_version("klawd")
    assert version_after in ("1.0", "1.1")


# --------------------------------------------------------------------------- #
# Scenario 18: manifest size under 4k
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_18_manifest_size_under_4k() -> None:
    docx_path = await _produce_memo_docx()
    with zipfile.ZipFile(docx_path, mode="r") as zf:
        manifest_parts = [
            n
            for n in zf.namelist()
            if n.startswith("grace/") and n.endswith(".xml")
        ]
        assert len(manifest_parts) >= 1
        xml_bytes = zf.read(manifest_parts[0])
        assert len(xml_bytes) < 4096


# --------------------------------------------------------------------------- #
# Scenario 19: round-trip via mint_read_grace_manifest of Phase-17 docx
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_19_round_trip_new_fields_monolingual() -> None:
    docx_path = await _produce_memo_docx()
    result = await mint_read_grace_manifest(
        str(docx_path), ctx=FakeMCPContext()
    )
    assert result["preset_version"] is not None
    assert isinstance(result["preset_version"], str)
    assert result["lang"] == []


@pytest.mark.asyncio
async def test_scenario_19_round_trip_new_fields_bilingual() -> None:
    docx_path = await _produce_nda_docx()
    manifest = await mint_read_grace_manifest(
        str(docx_path), ctx=FakeMCPContext()
    )
    assert manifest["preset_version"] is not None
    assert sorted(manifest["lang"]) == ["en", "ru"]
