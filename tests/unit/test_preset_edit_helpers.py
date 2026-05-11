# FILE: tests/unit/test_preset_edit_helpers.py
# VERSION: 0.1.0
"""Direct unit coverage for the W17-0 public helpers in preset_edit.py.

`resolve_latest_preset_path` is already exercised end-to-end by the
Phase-16 V-MP-THEME-EDIT integration suite (it backs every preset write).
`collect_preset_versions` is new in Phase-17 W17-0 and will be consumed
by MP-MCP-RESOURCES-VERSIONED in Wave-17-2 — this file gives it direct
unit coverage now so the 100% repo-wide coverage gate stays green until
the consumer's integration tests land.

Constraint-8: no `from mint.*` imports.
"""

from __future__ import annotations

import pytest

from mint_python.mcp.preset_edit import (
    PRESETS_DIR,
    PresetNotFound,
    collect_preset_versions,
)


def test_unknown_preset_with_no_siblings_raises_preset_not_found(
    tmp_path, monkeypatch
):
    """No BUILTIN entry + no versioned siblings → PresetNotFound."""
    monkeypatch.setattr("mint_python.mcp.preset_edit.PRESETS_DIR", tmp_path)
    with pytest.raises(PresetNotFound) as exc:
        collect_preset_versions("definitely_does_not_exist")
    assert "PRESET_NOT_FOUND" in str(exc.value)


def test_known_builtin_returns_base_1_0_when_no_versioned_siblings(
    tmp_path, monkeypatch
):
    """klawd has BUILTIN entry; no versioned files → returns ['1.0']."""
    monkeypatch.setattr("mint_python.mcp.preset_edit.PRESETS_DIR", tmp_path)
    versions = collect_preset_versions("klawd")
    assert versions == ["1.0"]


def test_versioned_siblings_included_in_ascending_semver_order(
    tmp_path, monkeypatch
):
    """Versioned siblings + base resolve to a sorted list (semver-tuple, not lex).

    Note: 1.10 > 1.2 by semver-tuple comparison — would be wrong under
    lexicographic sort. This is the same property V-MP-AUDIT-EXTEND
    scenario-12 and V-MP-MCP-RESOURCES-VERSIONED scenario-3 will pin
    when their consumer tests land.
    """
    monkeypatch.setattr("mint_python.mcp.preset_edit.PRESETS_DIR", tmp_path)
    (tmp_path / "klawd_v1.1.yaml").write_text("name: klawd\nversion: '1.1'\n")
    (tmp_path / "klawd_v1.2.yaml").write_text("name: klawd\nversion: '1.2'\n")
    (tmp_path / "klawd_v1.10.yaml").write_text("name: klawd\nversion: '1.10'\n")

    versions = collect_preset_versions("klawd")

    assert versions == ["1.0", "1.1", "1.2", "1.10"]


def test_malformed_filename_silently_skipped(tmp_path, monkeypatch):
    """klawd_v_typo.yaml lacks digit suffix → skipped; klawd_v1.1.yaml kept."""
    monkeypatch.setattr("mint_python.mcp.preset_edit.PRESETS_DIR", tmp_path)
    (tmp_path / "klawd_v_typo.yaml").write_text("malformed\n")
    (tmp_path / "klawd_v1.1.yaml").write_text("name: klawd\nversion: '1.1'\n")

    versions = collect_preset_versions("klawd")

    assert versions == ["1.0", "1.1"]


def test_name_byte_compare_rejects_case_mismatch(tmp_path, monkeypatch):
    """Klawd_v1.1.yaml (capital K) MUST NOT match name='klawd' (macOS HFS+).

    The glob pattern `klawd_v*.yaml` is case-insensitive on HFS+, but the
    `match.group("name") == name` byte-compare in the resolver catches
    the mismatch. Versioned file is dropped from the result list.
    """
    monkeypatch.setattr("mint_python.mcp.preset_edit.PRESETS_DIR", tmp_path)
    (tmp_path / "Klawd_v1.1.yaml").write_text("name: klawd\nversion: '1.1'\n")

    versions = collect_preset_versions("klawd")

    # Only the base 1.0 from BUILTIN_PRESETS — the capital-K file rejected.
    assert versions == ["1.0"]


def test_dedupe_when_base_1_0_already_in_versioned_siblings(
    tmp_path, monkeypatch
):
    """If klawd_v1.0.yaml exists as a versioned sibling, it doesn't duplicate."""
    monkeypatch.setattr("mint_python.mcp.preset_edit.PRESETS_DIR", tmp_path)
    (tmp_path / "klawd_v1.0.yaml").write_text("name: klawd\nversion: '1.0'\n")
    (tmp_path / "klawd_v1.1.yaml").write_text("name: klawd\nversion: '1.1'\n")

    versions = collect_preset_versions("klawd")

    # Exactly two entries — no duplicate '1.0' from BUILTIN_PRESETS append.
    assert versions == ["1.0", "1.1"]


def test_presets_dir_missing_falls_back_to_builtin_when_known(
    tmp_path, monkeypatch
):
    """PRESETS_DIR doesn't exist → BUILTIN_PRESETS still resolves."""
    monkeypatch.setattr(
        "mint_python.mcp.preset_edit.PRESETS_DIR", tmp_path / "nonexistent"
    )

    versions = collect_preset_versions("klawd")

    assert versions == ["1.0"]


def test_resolve_latest_preset_path_exported_publicly():
    """W17-0 promotion: resolve_latest_preset_path is a public name now."""
    from mint_python.mcp import preset_edit

    assert hasattr(preset_edit, "resolve_latest_preset_path")
    # Smoke: callable; raises on unknown.
    with pytest.raises(PresetNotFound):
        preset_edit.resolve_latest_preset_path("definitely_does_not_exist")


def test_module_level_presets_dir_is_path_object():
    """PRESETS_DIR is module-exported as a Path (consumed by future
    MP-MCP-RESOURCES-VERSIONED resolver imports)."""
    from pathlib import Path

    assert isinstance(PRESETS_DIR, Path)
