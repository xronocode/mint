# FILE: tests/integration/test_mp_mcp_resources_versioned.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-MCP-RESOURCES-VERSIONED verification (Phase-17 W17-2)
#     — exercises the chain from `mint_update_preset_palette/typography/
#     spacing` writes through to `mint://preset/<name>` reads,
#     `mint_list_presets` summaries, and `mint_get_preset` tool calls.
#     Closes the Phase-16 W2-discovered gap: external MCP clients
#     (Cursor, OpenWebUI, Claude Desktop) used to see only the BUILTIN
#     baseline even after a successful preset edit. Now the read path
#     resolves the latest versioned sibling via
#     `preset_edit.resolve_latest_preset_path` (W17-0 public).
#   SCOPE: Integration tests — 14 scenarios per V-MP-MCP-RESOURCES-
#     VERSIONED (1-7 original brief + 8-14 deepening additions).
#     Monkeypatches `preset_edit.PRESETS_DIR` to a tmp_path-isolated
#     fixture dir so write-and-read chains don't pollute the repo.
#   DEPENDS: pytest, pyyaml, mint_python.mcp.resources,
#     mint_python.mcp.preset_edit, tests._helpers.fake_mcp_context.
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from mint_python.mcp import preset_edit as preset_edit_module
from mint_python.mcp.resources import (
    ResourceNotFound,
    _preset_resource_content,
    _resolve_preset_for_read,
    get_preset,
    list_presets,
    preset_resource,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_PRESETS = (
    Path(__file__).parent.parent.parent / "src" / "mint_python" / "core" / "presets"
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def isolated_presets_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """tmp_path-isolated PRESETS_DIR — no built-in YAML/JSON files seeded.

    Tests that need the BUILTIN fallback to fire stay here (the resolver
    finds nothing in the glob, falls through to BUILTIN_PRESETS[name]
    which still points at the repo presets dir). Tests that need a chain
    of versioned siblings plant them directly into tmp_path.
    """
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def klawd_version_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Plant base + 3 valid versioned siblings + negatives.

    Layout:
      - klawd_v1.1.yaml    ← valid sibling
      - klawd_v1.2.yaml    ← valid sibling
      - klawd_v1.10.yaml   ← valid sibling, MUST win (semver 1.10 > 1.2)
      - klawd_v_typo.yaml  ← NEGATIVE: missing digit suffix
      - Klawd_v1.5.yaml    ← NEGATIVE: capital K (case-sensitive reject)

    BUILTIN_PRESETS['klawd'] still points at the repo's klawd.yaml so
    the version chain rolls back to '1.0' on the BUILTIN side.
    """
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", tmp_path)
    (tmp_path / "klawd_v1.1.yaml").write_text(
        "name: klawd\nversion: '1.1'\ncolor_palette:\n  primary: '#111111'\n"
    )
    (tmp_path / "klawd_v1.2.yaml").write_text(
        "name: klawd\nversion: '1.2'\ncolor_palette:\n  primary: '#222222'\n"
    )
    (tmp_path / "klawd_v1.10.yaml").write_text(
        "name: klawd\nversion: '1.10'\ncolor_palette:\n  primary: '#101010'\n"
    )
    (tmp_path / "klawd_v_typo.yaml").write_text("malformed\n")
    (tmp_path / "Klawd_v1.5.yaml").write_text(
        "name: klawd\nversion: '1.5'\ncolor_palette:\n  primary: '#555555'\n"
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# Scenario-1 — BUILTIN fallback when no versioned siblings.
# --------------------------------------------------------------------------- #


def test_scenario_1_no_versions_falls_back_to_builtin(
    isolated_presets_dir: Path,
) -> None:
    """Empty PRESETS_DIR → resolver returns BUILTIN_PRESETS['klawd'] +
    `_preset_resource_content` returns the canonical repo content."""
    content = _preset_resource_content("klawd")
    assert "name: klawd" in content
    # Real repo file carries the canonical schema URL header — proves
    # we're reading from BUILTIN_PRESETS, not a synthesized fallback.
    assert "$schema:" in content


# --------------------------------------------------------------------------- #
# Scenario-2 — versioned sibling wins after a preset edit.
# --------------------------------------------------------------------------- #


def test_scenario_2_versioned_wins_over_builtin(
    isolated_presets_dir: Path,
) -> None:
    """Single sibling klawd_v1.1.yaml → mint://preset/klawd returns the
    new content, not the BUILTIN baseline."""
    (isolated_presets_dir / "klawd_v1.1.yaml").write_text(
        "name: klawd\nversion: '1.1'\ncolor_palette:\n  primary: '#0F4C81'\n"
    )
    content = _preset_resource_content("klawd")
    parsed = yaml.safe_load(content)
    assert parsed["version"] == "1.1"
    assert parsed["color_palette"]["primary"] == "#0F4C81"


# --------------------------------------------------------------------------- #
# Scenario-3 — semver sort: 1.10 > 1.2 (NOT lexicographic).
# --------------------------------------------------------------------------- #


def test_scenario_3_semver_sort_not_lexicographic(
    klawd_version_chain: Path,
) -> None:
    """klawd_v1.10.yaml must win over klawd_v1.2.yaml — semver, not lex.
    Under lexicographic sort '1.10' < '1.2' which would silently pick
    the wrong file. forbidden-1 regression test."""
    content = _preset_resource_content("klawd")
    parsed = yaml.safe_load(content)
    assert parsed["version"] == "1.10"
    assert parsed["color_palette"]["primary"] == "#101010"


# --------------------------------------------------------------------------- #
# Scenario-4 — list_presets summary includes latest_version +
# predecessor_versions when versioned siblings exist.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_list_presets_shows_chain(
    klawd_version_chain: Path,
) -> None:
    """klawd entry surfaces the full edit chain. predecessor_versions is
    the ascending list up to but excluding the latest."""
    ctx = FakeMCPContext(answers={})
    entries = await list_presets(ctx=ctx)

    klawd_entries = [e for e in entries if e["name"] == "klawd"]
    assert len(klawd_entries) == 1
    klawd = klawd_entries[0]

    assert klawd["latest_version"] == "1.10"
    # Full chain = [1.0, 1.1, 1.2, 1.10]; predecessors = list[:-1].
    assert klawd["predecessor_versions"] == ["1.0", "1.1", "1.2"]
    # Base 4-key shape still present.
    assert klawd["uri"] == "mint://preset/klawd"
    assert klawd["mimeType"] == "application/x-yaml"


# --------------------------------------------------------------------------- #
# Scenario-5 — BLOCK_RESOLVE_VERSIONED log marker.
# --------------------------------------------------------------------------- #


def test_scenario_5_block_resolve_versioned_marker(
    klawd_version_chain: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every resolve call emits one marker carrying name + resolved
    filename + predecessor count."""
    with caplog.at_level(logging.INFO, logger="mint_python.mcp.resources"):
        _resolve_preset_for_read("klawd")

    markers = [
        r.getMessage()
        for r in caplog.records
        if "BLOCK_RESOLVE_VERSIONED" in r.getMessage()
    ]
    assert len(markers) == 1
    assert "[MP-Resources][preset][BLOCK_RESOLVE_VERSIONED]" in markers[0]
    assert "name=klawd" in markers[0]
    assert "resolved=klawd_v1.10.yaml" in markers[0]
    # Full chain length is 4 (1.0 + 1.1 + 1.2 + 1.10); predecessors = 3.
    assert "predecessors=3" in markers[0]


# --------------------------------------------------------------------------- #
# Scenario-6 — malformed filename ignored (klawd_v_typo.yaml).
# --------------------------------------------------------------------------- #


def test_scenario_6_malformed_filename_ignored(
    klawd_version_chain: Path,
) -> None:
    """klawd_v_typo.yaml (no digit suffix) MUST NOT crash + MUST NOT
    appear in the version chain — falls through to next-valid candidate.
    forbidden-3: resolver never raises on malformed filenames.
    """
    # The chain still picks 1.10; the typo file is silently dropped.
    resolved = _resolve_preset_for_read("klawd")
    assert resolved.name == "klawd_v1.10.yaml"

    # And the chain doesn't smuggle the typo file's version anywhere.
    versions = preset_edit_module.collect_preset_versions("klawd")
    assert "v_typo" not in str(versions)


# --------------------------------------------------------------------------- #
# Scenario-7 — unknown preset → ResourceNotFound, message lists known.
# --------------------------------------------------------------------------- #


def test_scenario_7_unknown_preset_lists_available(
    isolated_presets_dir: Path,
) -> None:
    """`mint://preset/nonexistent` → ResourceNotFound. Message names
    every BUILTIN preset so the model can retry with a valid one."""
    with pytest.raises(ResourceNotFound, match="RESOURCE_NOT_FOUND") as exc_info:
        _preset_resource_content("nonexistent")

    msg = str(exc_info.value)
    for known in ("klawd", "claret_serif", "minimal", "compact", "alga_corporate"):
        assert known in msg


# --------------------------------------------------------------------------- #
# Scenario-8 — case-sensitive name match (Klawd_v1.1.yaml MUST NOT match
# name='klawd' even on macOS HFS+ case-insensitive filesystem).
# --------------------------------------------------------------------------- #


def test_scenario_8_case_sensitive_name_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the capital-K file exists in the fixture; resolver must
    fall back to BUILTIN_PRESETS['klawd'] (NOT the capital-K sibling).
    """
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", tmp_path)
    (tmp_path / "Klawd_v1.1.yaml").write_text(
        "name: klawd\nversion: '1.1'\ncolor_palette:\n  primary: '#555555'\n"
    )

    resolved = _resolve_preset_for_read("klawd")
    # Falls back to the BUILTIN baseline because no lower-case match.
    assert resolved.name == "klawd.yaml"
    # And the version chain is just the base.
    versions = preset_edit_module.collect_preset_versions("klawd")
    assert versions == ["1.0"]


# --------------------------------------------------------------------------- #
# Scenario-9 — extension variants skipped (only .yaml matches).
# --------------------------------------------------------------------------- #


def test_scenario_9_extension_variants_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """klawd_v1.1.yml / .json / .bak / .yaml.swp — only the canonical
    `.yaml` writer-emitted extension matches the resolver glob."""
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", tmp_path)
    # Negatives — all skipped.
    for fname in (
        "klawd_v1.1.yml",      # different extension
        "klawd_v1.1.json",     # writer never emits json
        "klawd_v1.1.bak",      # editor backup
        "klawd_v1.1.yaml.swp",  # vim swap
    ):
        (tmp_path / fname).write_text("name: klawd\nversion: '1.1'\n")

    # No valid .yaml sibling → falls back to BUILTIN.
    resolved = _resolve_preset_for_read("klawd")
    assert resolved.name == "klawd.yaml"  # built-in path basename
    versions = preset_edit_module.collect_preset_versions("klawd")
    assert versions == ["1.0"]


# --------------------------------------------------------------------------- #
# Scenario-10 — malformed-suffix variants: missing-digit, 3-component,
# pre-release — all SKIPPED.
# --------------------------------------------------------------------------- #


def test_scenario_10_malformed_suffix_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boundary cases on the version slot:
       - klawd_v.yaml         → empty version
       - klawd_v1.yaml        → single component
       - klawd_v1.1.2.yaml    → 3-component (Phase-17 out of scope)
       - klawd_v1.0-rc1.yaml  → pre-release suffix
    All MUST be silently skipped — only well-formed MAJOR.MINOR matches.
    """
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", tmp_path)
    for fname in (
        "klawd_v.yaml",
        "klawd_v1.yaml",
        "klawd_v1.1.2.yaml",
        "klawd_v1.0-rc1.yaml",
    ):
        (tmp_path / fname).write_text("name: klawd\nversion: '1.x'\n")

    resolved = _resolve_preset_for_read("klawd")
    # No valid sibling → BUILTIN fallback.
    assert resolved.name == "klawd.yaml"
    # Version chain is base-only.
    versions = preset_edit_module.collect_preset_versions("klawd")
    assert versions == ["1.0"]


# --------------------------------------------------------------------------- #
# Scenario-11 — write-while-glob race: a half-flushed (empty) file in
# PRESETS_DIR MUST NOT crash list_presets / get_preset.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_11_write_while_glob_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O_EXCL guarantees the file appears in the glob before bytes are
    flushed; a concurrent reader may catch the file at zero-length.

    The read path returns raw text (no parse during resolve), so an
    empty file resolves to empty content rather than raising. The
    resolver itself NEVER parses YAML during resolution — only filenames
    are inspected. forbidden-3-aligned behavior.
    """
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", tmp_path)
    # Simulate a half-flushed write: file exists, content is empty.
    (tmp_path / "klawd_v1.1.yaml").write_text("")
    # And a fully-written predecessor.
    (tmp_path / "klawd_v1.2.yaml").write_text(
        "name: klawd\nversion: '1.2'\ncolor_palette:\n  primary: '#222222'\n"
    )

    # list_presets must not raise — yaml.safe_load is not in the resolve
    # path; the empty file is treated as a valid filename with raw text.
    ctx = FakeMCPContext(answers={})
    entries = await list_presets(ctx=ctx)
    klawd_entry = next(e for e in entries if e["name"] == "klawd")
    # Latest still resolves to the highest valid filename (v1.2 since
    # v1.1 is empty but the filename parses cleanly — resolver doesn't
    # validate content, only name).
    assert klawd_entry["latest_version"] in ("1.1", "1.2")

    # get_preset on the same name returns content (possibly empty for the
    # half-flushed file) without raising.
    out = await get_preset("klawd", ctx=ctx)
    assert "content" in out


# --------------------------------------------------------------------------- #
# Scenario-12 — backwards-compat: no siblings → entry key set byte-identical
# to Phase-14 v0.1.0 shape.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_12_summary_shape_backwards_compat(
    isolated_presets_dir: Path,
) -> None:
    """When no versioned siblings exist, list_presets entries carry
    EXACTLY the Phase-14 4-key shape: uri, name, mimeType, description.
    latest_version + predecessor_versions are OMITTED (not None-valued),
    so existing scenario-3 strict-equality assertions still pass."""
    ctx = FakeMCPContext(answers={})
    entries = await list_presets(ctx=ctx)
    assert len(entries) == 5  # 5 BUILTIN presets, no duplicates.
    for entry in entries:
        assert set(entry.keys()) == {"uri", "name", "mimeType", "description"}, (
            f"entry {entry['name']!r} drifted from byte-identical shape: "
            f"got {sorted(entry.keys())}"
        )


# --------------------------------------------------------------------------- #
# Scenario-13 — FastMCP no-stale-cache: a write between two reads MUST
# surface the new content on the second read.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_13_no_stale_cache_after_write(
    isolated_presets_dir: Path,
) -> None:
    """First read returns BUILTIN content; plant a versioned sibling;
    second read returns the new content. The @server.resource handler
    re-resolves every call — no TTL cache."""
    # 1st read: BUILTIN (no siblings yet).
    first = await preset_resource("klawd")
    assert "name: klawd" in first

    # Plant a fresh versioned sibling.
    (isolated_presets_dir / "klawd_v1.1.yaml").write_text(
        "name: klawd\nversion: '1.1'\ncolor_palette:\n  primary: '#FF00FF'\n"
    )

    # 2nd read MUST see the new content.
    second = await preset_resource("klawd")
    parsed = yaml.safe_load(second)
    assert parsed["version"] == "1.1"
    assert parsed["color_palette"]["primary"] == "#FF00FF"


# --------------------------------------------------------------------------- #
# Scenario-14 — canonical names only: 5 BUILTIN names regardless of
# versioned siblings count.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_14_canonical_names_only(
    klawd_version_chain: Path,
) -> None:
    """Three valid siblings + two negatives for klawd → still exactly
    5 entries (one per BUILTIN canonical name). Versioned siblings
    surface as metadata, NEVER as top-level entries. forbidden-5."""
    ctx = FakeMCPContext(answers={})
    entries = await list_presets(ctx=ctx)

    assert len(entries) == 5
    names = sorted(e["name"] for e in entries)
    assert names == sorted(
        ["klawd", "claret_serif", "alga_corporate", "minimal", "compact"]
    )
    # And exactly one klawd entry — even though 3 valid siblings exist.
    assert sum(1 for e in entries if e["name"] == "klawd") == 1


# --------------------------------------------------------------------------- #
# Forbidden-7 — resolver path must stay inside PRESETS_DIR / BUILTIN.
# (Defensive: this is enforced structurally by preset_edit's resolver,
# but we keep a smoke check so a future refactor that breaks the
# invariant fails loudly.)
# --------------------------------------------------------------------------- #


def test_forbidden_7_resolved_path_sandbox(
    klawd_version_chain: Path,
) -> None:
    """Every resolved path must be either BUILTIN_PRESETS[name] OR a
    direct child of PRESETS_DIR. No symlink escape, no path traversal."""
    from mint_python.core.style import BUILTIN_PRESETS as _BUILTIN

    resolved = _resolve_preset_for_read("klawd")
    assert (
        resolved == _BUILTIN["klawd"]
        or resolved.parent == preset_edit_module.PRESETS_DIR
    )


# --------------------------------------------------------------------------- #
# Forbidden-4 — semver / regex logic NOT duplicated in resources.py.
# Mechanical guard: the module source must not contain
# `_VERSIONED_FILENAME_RE` or `_semver_tuple`.
# --------------------------------------------------------------------------- #


def test_forbidden_4_no_duplicated_semver_logic() -> None:
    """Read resources.py and assert the module never DEFINES the
    forbidden semver primitives. References inside comments (e.g.
    "this module deliberately does NOT define `_VERSIONED_FILENAME_RE`")
    are allowed — what we block is an actual second definition that
    would drift from preset_edit's authoritative one.

    The mechanical check: no `<token> =`, no `def <token>(`, no
    `re.compile(` line that captures into a local `_VERSIONED_FILENAME_RE`.
    """
    from mint_python.mcp import resources as resources_module

    src = Path(resources_module.__file__).read_text(encoding="utf-8")

    # Patterns that would constitute a redefinition.
    forbidden_definitions = (
        "_VERSIONED_FILENAME_RE =",
        "_VERSIONED_FILENAME_RE: ",
        "def _semver_tuple",
        "_semver_tuple =",
    )
    found = [token for token in forbidden_definitions if token in src]
    assert not found, (
        f"resources.py must not duplicate semver logic from preset_edit; "
        f"forbidden definitions present: {found!r}. Import "
        f"resolve_latest_preset_path / collect_preset_versions instead."
    )
