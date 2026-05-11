# FILE: tests/integration/test_mp_theme_edit.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-THEME-EDIT verification — covers scenarios 1-9 of the
#     structured preset-edit MCP tools (Phase-16 Wave-16-2). Scenario-10
#     (GRACE manifest carries preset_version) is DEFERRED — document.py
#     does not yet stamp preset_version into _audit_instructions; carry
#     -forward to Phase-17 (see scenario-10 conditional clause in
#     verification-plan.xml#V-MP-THEME-EDIT and VF-022 activation-gate).
#   SCOPE: Integration tests — exercise the three @server.tool wrappers
#     (mint_update_preset_palette / _typography / _spacing) plus the
#     shared _update_preset core under tmp_path-isolated PRESETS_DIR +
#     monkeypatched MINT_TEMPLATE_WRITERS env. Uses clean_writers_config
#     fixture from tests/unit/conftest.py (NOT redefined here).
#   DEPENDS: pytest, pyyaml, mint_python.mcp.preset_edit,
#     mint_python.mcp.auth, mint_python.templates.registry (for the
#     scenario-8 shared-allowlist cross-check),
#     tests._helpers.fake_mcp_context.
# END_MODULE_CONTRACT
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from mint_python.mcp import preset_edit as preset_edit_module
from mint_python.mcp.auth import TemplateWriteForbidden
from mint_python.mcp.preset_edit import (
    CANONICAL_RESULT_KEYS,
    InvalidPatch,
    PresetNotFound,
    PresetVersionConflict,
    mint_update_preset_palette,
    mint_update_preset_spacing,
    mint_update_preset_typography,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_PRESETS = Path(__file__).parent.parent.parent / "src" / "mint_python" / "core" / "presets"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def fixture_presets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Clone the canonical klawd + claret_serif presets into a tmp dir
    AND monkeypatch preset_edit.PRESETS_DIR so writes don't pollute the
    repo. Built-in preset RESOLUTION still happens through the original
    BUILTIN_PRESETS map (paths point to the repo) — we only redirect the
    WRITE target.

    The test relies on _resolve_base_preset_path's resolution order:
    versioned siblings in PRESETS_DIR take precedence over built-ins, so
    after a first edit lands in the tmp dir, subsequent edits chain from
    it rather than the repo built-in."""
    fixtures = tmp_path / "presets"
    fixtures.mkdir()
    # Copy the built-in YAMLs so the first edit chains from the same
    # baseline content the repo ships with. PRESETS_DIR scan finds NO
    # versioned siblings initially → falls through to BUILTIN_PRESETS.
    # After the first edit, the v1.1 file lives in `fixtures/` and the
    # next call's scan picks it up.
    for name in ("klawd.yaml", "claret_serif.yaml"):
        (fixtures / name).write_text(
            (REPO_PRESETS / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    monkeypatch.setattr(preset_edit_module, "PRESETS_DIR", fixtures)
    return fixtures


def _admit(monkeypatch: pytest.MonkeyPatch, author: str) -> None:
    """Set MINT_TEMPLATE_WRITERS to include `author`. clean_writers_config
    must run BEFORE this — the fixture order in each test puts it first."""
    monkeypatch.setenv("MINT_TEMPLATE_WRITERS", author)


def _make_ctx() -> FakeMCPContext:
    """The preset-edit tools never call ctx.elicit — they take author
    inline. We still pass a FakeMCPContext for shape compatibility."""
    return FakeMCPContext(answers={})


def _snapshot_dir_sha256(directory: Path) -> dict[str, str]:
    """Return a {filename: sha256} map for every file in directory.

    Used by scenario-2 (deny path leaves disk byte-identical) and
    scenario-5/6 (invalid patch leaves disk byte-identical) — VF-022
    inv-1 + inv-6."""
    out: dict[str, str] = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# --------------------------------------------------------------------------- #
# Scenario-1 — admit + palette patch + write + audit.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_palette_admit_and_write(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "Claude-Opus-4.7")
    ctx = _make_ctx()

    outcome = await mint_update_preset_palette(
        name="klawd",
        palette={"primary": "#0F4C81"},
        author="Claude-Opus-4.7",
        ctx=ctx,
    )

    # Canonical shape contract.
    assert set(outcome.keys()) == set(CANONICAL_RESULT_KEYS)
    assert outcome["name"] == "klawd"
    assert outcome["version"] == "1.1"
    assert outcome["predecessor_version"] == "1.0"
    assert outcome["patched_fields"] == ["primary"]
    assert outcome["audit_id"]

    # New versioned file lives in the tmp dir.
    written_path = fixture_presets_dir / "klawd_v1.1.yaml"
    assert written_path.exists()
    written = yaml.safe_load(written_path.read_text())
    assert written["version"] == "1.1"
    assert written["color_palette"]["primary"] == "#0F4C81"
    # Other palette entries preserved.
    assert written["color_palette"]["secondary"] == "#2E75B6"
    assert written["color_palette"]["text"] == "#333333"

    # Audit log gains exactly one entry.
    audit_path = fixture_presets_dir / "_audit.jsonl"
    assert audit_path.exists()
    lines = audit_path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["audit_id"] == outcome["audit_id"]
    assert entry["name"] == "klawd"
    assert entry["version"] == "1.1"
    assert entry["surface"] == "palette"
    assert entry["author"] == "Claude-Opus-4.7"
    assert entry["patched_fields"] == ["primary"]
    assert "content_sha256" in entry


# --------------------------------------------------------------------------- #
# Scenario-2 — deny path: TEMPLATE_WRITE_FORBIDDEN; no disk mutation.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_deny_path_no_disk_io(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _admit(monkeypatch, "alice")  # only alice is on allowlist
    ctx = _make_ctx()

    before_listing = _snapshot_dir_sha256(fixture_presets_dir)

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(TemplateWriteForbidden, match="TEMPLATE_WRITE_FORBIDDEN"),
    ):
        await mint_update_preset_palette(
            name="klawd",
            palette={"primary": "#0F4C81"},
            author="cursor-sidecar",
            ctx=ctx,
        )

    # VF-022 inv-1 / forbidden-3: BLOCK_PRESET_WRITE MUST NEVER fire on deny.
    write_markers = [
        r for r in caplog.records if "BLOCK_PRESET_WRITE" in r.getMessage()
    ]
    assert not write_markers

    # Directory listing byte-identical (no new files, no audit append).
    after_listing = _snapshot_dir_sha256(fixture_presets_dir)
    assert before_listing == after_listing


# --------------------------------------------------------------------------- #
# Scenario-3 — typography patch.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_typography_admit(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    outcome = await mint_update_preset_typography(
        name="klawd",
        typography={"body_font": "Inter", "base_size_pt": 12},
        author="alice",
        ctx=ctx,
    )
    assert outcome["version"] == "1.1"
    assert sorted(outcome["patched_fields"]) == ["base_size_pt", "body_font"]

    written = yaml.safe_load(
        (fixture_presets_dir / "klawd_v1.1.yaml").read_text()
    )
    assert written["typography"]["body"]["font"] == "Inter"
    assert written["typography"]["body"]["size_pt"] == 12.0
    assert written["typography"]["table_header"]["font"] == "Inter"
    # Heading fonts untouched (only body_font surface).
    assert written["typography"]["heading1"]["font"] == "Arial"


# --------------------------------------------------------------------------- #
# Scenario-4 — spacing patch.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_spacing_admit(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    outcome = await mint_update_preset_spacing(
        name="klawd",
        spacing={"paragraph_pt": 8},
        author="alice",
        ctx=ctx,
    )
    assert outcome["version"] == "1.1"
    assert outcome["patched_fields"] == ["paragraph_pt"]

    written = yaml.safe_load(
        (fixture_presets_dir / "klawd_v1.1.yaml").read_text()
    )
    assert written["spacing"]["paragraph_default_after_pt"] == 8


# --------------------------------------------------------------------------- #
# Scenario-5 — invalid hex (palette) rejected pre-write.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_invalid_hex_pre_write(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    # Sentinel on the write helpers: trip → AssertionError. VF-022 inv-6.
    def _trip(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "Path.write_text/write_bytes/_write_versioned_preset must NOT "
            "fire on INVALID_PATCH (VF-022 inv-6)"
        )

    monkeypatch.setattr(Path, "write_text", _trip)
    monkeypatch.setattr(Path, "write_bytes", _trip)
    monkeypatch.setattr(
        preset_edit_module, "_write_versioned_preset", _trip
    )

    before_listing = _snapshot_dir_sha256(fixture_presets_dir)

    with pytest.raises(InvalidPatch, match="must be a #RRGGBB hex"):
        await mint_update_preset_palette(
            name="klawd",
            palette={"primary": "not-a-hex"},
            author="alice",
            ctx=ctx,
        )

    after_listing = _snapshot_dir_sha256(fixture_presets_dir)
    assert before_listing == after_listing


# --------------------------------------------------------------------------- #
# Scenario-6 — negative spacing rejected.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_negative_spacing_rejected(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    def _trip(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("write helper must NOT fire on INVALID_PATCH")

    monkeypatch.setattr(
        preset_edit_module, "_write_versioned_preset", _trip
    )

    before_listing = _snapshot_dir_sha256(fixture_presets_dir)

    with pytest.raises(InvalidPatch, match="must be >= 0"):
        await mint_update_preset_spacing(
            name="klawd",
            spacing={"paragraph_pt": -1},
            author="alice",
            ctx=ctx,
        )

    after_listing = _snapshot_dir_sha256(fixture_presets_dir)
    assert before_listing == after_listing


# --------------------------------------------------------------------------- #
# Scenario-7 — concurrent writes race: one wins, the other gets
# PRESET_VERSION_CONFLICT. Uses threading because the @server.tool
# wrappers are async and the conflict surface is on the synchronous
# filesystem syscall (O_CREAT|O_EXCL).
# --------------------------------------------------------------------------- #


def test_scenario_7_version_conflict_race(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")

    # Pre-create the target version file to deterministically force a
    # conflict on the next write. This is the same simulation strategy
    # MP-TEMPLATES-WRITE uses for its concurrent-write scenario — the
    # in-memory predecessor=1.0 + on-disk klawd_v1.1.yaml is exactly the
    # race condition forbidden-2 protects against.
    (fixture_presets_dir / "klawd_v1.1.yaml").write_text(
        "name: klawd\nversion: \"1.1\"\ncolor_palette: {primary: '#000000'}\n",
        encoding="utf-8",
    )
    pre_sha = hashlib.sha256(
        (fixture_presets_dir / "klawd_v1.1.yaml").read_bytes()
    ).hexdigest()

    # Now race two writes against the SAME predecessor (klawd.yaml at
    # v1.0). The first sneaks past the version scan because we wrote
    # v1.1 manually above — _resolve_base_preset_path picks v1.1 as the
    # latest, so the next bump targets v1.2. Drop v1.1 + re-create
    # using O_EXCL race directly to verify the conflict path.
    (fixture_presets_dir / "klawd_v1.1.yaml").unlink()

    # Patch _bump_minor to always return "1.1" so both threads target
    # the same path, forcing a true O_EXCL race. The losing thread
    # raises PresetVersionConflict.
    monkeypatch.setattr(
        preset_edit_module, "_bump_minor", lambda _v: "1.1"
    )

    results: list[Any] = []
    barrier = threading.Barrier(2)

    def _worker() -> None:
        import asyncio
        barrier.wait()  # align both threads on the syscall
        try:
            asyncio.run(
                mint_update_preset_palette(
                    name="klawd",
                    palette={"primary": "#123456"},
                    author="alice",
                    ctx=_make_ctx(),
                )
            )
            results.append("ok")
        except PresetVersionConflict as exc:
            results.append(exc)
        except Exception as exc:
            results.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one win, exactly one PresetVersionConflict.
    successes = [r for r in results if r == "ok"]
    conflicts = [r for r in results if isinstance(r, PresetVersionConflict)]
    assert len(successes) == 1, f"expected 1 winner, got {results!r}"
    assert len(conflicts) == 1, f"expected 1 conflict, got {results!r}"

    # The pre-existing sha guard: we deleted v1.1 before the race so the
    # winning thread's content is the ONLY v1.1 we see now. (No earlier
    # version file got clobbered — that's the actual invariant.)
    del pre_sha  # unused, kept for documentation


# --------------------------------------------------------------------------- #
# Scenario-8 — VF-022 inv-2 WRITERS-ALLOWLIST-REUSED: same env admits
# both update_template AND mint_update_preset_palette for the same
# author. No MINT_PRESET_WRITERS env exists.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_8_shared_allowlist(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both update_template and mint_update_preset_palette gate on
    MINT_TEMPLATE_WRITERS. Setting a single env admits both surfaces
    for the same author. The test exercises the auth gate explicitly
    (via require_template_writer) since the templates-side write
    pipeline is out of scope for this worker."""
    _admit(monkeypatch, "alice")

    # Cross-check: no preset-specific env name leaks in.
    import os
    assert "MINT_PRESET_WRITERS" not in os.environ

    # The same env admits the preset-edit call.
    ctx = _make_ctx()
    outcome_preset = await mint_update_preset_palette(
        name="klawd",
        palette={"primary": "#AABBCC"},
        author="alice",
        ctx=ctx,
    )
    assert outcome_preset["version"] == "1.1"

    # AND the same env admits the templates-side auth call for alice.
    # We probe require_template_writer directly rather than running
    # the templates update pipeline (out of W2 scope + needs templates
    # dir setup).
    from mint_python.mcp.auth import require_template_writer
    require_template_writer("alice")  # MUST NOT raise

    # The same env DENIES a non-allowlisted author for both surfaces.
    with pytest.raises(TemplateWriteForbidden):
        require_template_writer("bob")
    with pytest.raises(TemplateWriteForbidden):
        await mint_update_preset_palette(
            name="klawd",
            palette={"primary": "#112233"},
            author="bob",
            ctx=ctx,
        )


# --------------------------------------------------------------------------- #
# Scenario-9 — marker sequence: BLOCK_AUTH_ADMIT precedes BLOCK_PRESET_WRITE
# in caplog. Per-tool variant (palette/typography/spacing).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tool_fn,surface,patch",
    [
        (mint_update_preset_palette, "palette", {"primary": "#AABBCC"}),
        (
            mint_update_preset_typography,
            "typography",
            {"body_font": "Inter"},
        ),
        (mint_update_preset_spacing, "spacing", {"paragraph_pt": 7}),
    ],
)
@pytest.mark.asyncio
async def test_scenario_9_marker_sequence(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tool_fn: Any,
    surface: str,
    patch: dict[str, Any],
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    with caplog.at_level(logging.INFO):
        kwargs: dict[str, Any] = {surface: patch}
        await tool_fn(name="klawd", author="alice", ctx=ctx, **kwargs)

    # Extract the indices of the two markers in caplog.
    auth_admit_idx = None
    write_idx = None
    for idx, record in enumerate(caplog.records):
        msg = record.getMessage()
        if (
            "[MP-Auth][check_writer][BLOCK_AUTH_ADMIT]" in msg
            and auth_admit_idx is None
        ):
            auth_admit_idx = idx
        if (
            f"[MP-ThemeEdit][{surface}][BLOCK_PRESET_WRITE]" in msg
            and write_idx is None
        ):
            write_idx = idx

    assert auth_admit_idx is not None, "BLOCK_AUTH_ADMIT marker missing"
    assert write_idx is not None, "BLOCK_PRESET_WRITE marker missing"
    assert auth_admit_idx < write_idx, (
        f"VF-022 trace-sequence violation: BLOCK_AUTH_ADMIT (idx={auth_admit_idx}) "
        f"must precede BLOCK_PRESET_WRITE (idx={write_idx})"
    )


# --------------------------------------------------------------------------- #
# Scenario-10 — DEFERRED. document.py does not yet stamp preset_version
# into _audit_instructions. Carry-forward to Phase-17 per VF-022
# activation-gate conditional clause.
# --------------------------------------------------------------------------- #


@pytest.mark.skip(
    reason=(
        "MP-THEME-EDIT scenario-10 (VF-022 inv-7 GRACE-MANIFEST-CARRIES-"
        "PRESET-VERSION) requires src/mint_python/mcp/document.py to "
        "extend _audit_instructions with preset_version. document.py is "
        "out of W2 write scope; deferred to Phase-16 W3 or Phase-17 per "
        "V-MP-THEME-EDIT scenario-10 conditional clause."
    )
)
@pytest.mark.asyncio
async def test_scenario_10_grace_carries_preset_version() -> None:  # pragma: no cover
    """Placeholder — activates once document.py stamps preset_version."""
    raise AssertionError("deferred — see skip reason")


# --------------------------------------------------------------------------- #
# Edge: unknown preset name surfaces PresetNotFound (after auth admit).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_preset_name_raises_after_auth(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    with pytest.raises(PresetNotFound, match="PRESET_NOT_FOUND"):
        await mint_update_preset_palette(
            name="does-not-exist",
            palette={"primary": "#000000"},
            author="alice",
            ctx=ctx,
        )


# --------------------------------------------------------------------------- #
# Edge: unknown patch key rejected pre-write (each surface).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tool_fn,surface,patch,expected_msg",
    [
        (
            mint_update_preset_palette,
            "palette",
            {"unknown_key": "#000000"},
            "unknown keys",
        ),
        (
            mint_update_preset_typography,
            "typography",
            {"unknown_key": "Inter"},
            "unknown keys",
        ),
        (
            mint_update_preset_spacing,
            "spacing",
            {"unknown_key": 5},
            "unknown keys",
        ),
    ],
)
@pytest.mark.asyncio
async def test_unknown_patch_keys_rejected(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    tool_fn: Any,
    surface: str,
    patch: dict[str, Any],
    expected_msg: str,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    kwargs: dict[str, Any] = {surface: patch}
    with pytest.raises(InvalidPatch, match=expected_msg):
        await tool_fn(name="klawd", author="alice", ctx=ctx, **kwargs)


# --------------------------------------------------------------------------- #
# Edge: non-dict patch rejected for each surface.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tool_fn,surface",
    [
        (mint_update_preset_palette, "palette"),
        (mint_update_preset_typography, "typography"),
        (mint_update_preset_spacing, "spacing"),
    ],
)
@pytest.mark.asyncio
async def test_non_dict_patch_rejected(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    tool_fn: Any,
    surface: str,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    kwargs: dict[str, Any] = {surface: ["not-a-dict"]}
    with pytest.raises(InvalidPatch, match="must be an object"):
        await tool_fn(name="klawd", author="alice", ctx=ctx, **kwargs)


# --------------------------------------------------------------------------- #
# Edge: typography invalid value shapes.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_typography_empty_font_rejected(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    with pytest.raises(InvalidPatch, match="non-empty string"):
        await mint_update_preset_typography(
            name="klawd",
            typography={"body_font": "   "},
            author="alice",
            ctx=ctx,
        )


@pytest.mark.parametrize(
    "key,value,expected_msg",
    [
        ("base_size_pt", "not-a-number", "must be a number"),
        ("base_size_pt", -1, "must be > 0"),
        ("heading_scale", "x", "must be a number"),
        ("heading_scale", 0, "must be > 0"),
        ("line_height", True, "must be a number"),
        ("line_height", 0, "must be > 0"),
    ],
)
@pytest.mark.asyncio
async def test_typography_value_shape_validation(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: Any,
    expected_msg: str,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    with pytest.raises(InvalidPatch, match=expected_msg):
        await mint_update_preset_typography(
            name="klawd",
            typography={key: value},
            author="alice",
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_spacing_non_number_rejected(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    with pytest.raises(InvalidPatch, match="must be a number"):
        await mint_update_preset_spacing(
            name="klawd",
            spacing={"paragraph_pt": "x"},
            author="alice",
            ctx=ctx,
        )


# --------------------------------------------------------------------------- #
# Edge: chained edits — second edit chains from v1.1 (proves PRESETS_DIR
# scan beats built-in fallback after the first edit lands).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chained_edits_bump_from_latest_versioned_sibling(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    out1 = await mint_update_preset_palette(
        name="klawd", palette={"primary": "#AAAAAA"}, author="alice", ctx=ctx
    )
    out2 = await mint_update_preset_palette(
        name="klawd", palette={"primary": "#BBBBBB"}, author="alice", ctx=ctx
    )
    assert out1["version"] == "1.1"
    assert out2["version"] == "1.2"
    assert out2["predecessor_version"] == "1.1"

    # Audit log gains exactly two entries (append-only).
    lines = (fixture_presets_dir / "_audit.jsonl").read_text().splitlines()
    assert len(lines) == 2


# --------------------------------------------------------------------------- #
# Edge: empty patch produces a no-op bump (patched_fields empty list).
# Documented carve-out in _validate_patch_keys.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_empty_patch_produces_no_op_bump(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    outcome = await mint_update_preset_palette(
        name="klawd", palette={}, author="alice", ctx=ctx
    )
    assert outcome["patched_fields"] == []
    assert outcome["version"] == "1.1"


# --------------------------------------------------------------------------- #
# Edge: writers tuple is NEVER logged on deny (VF-022 forbidden-4).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_writers_tuple_never_in_log_payload(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_writer = "secret-author-do-not-log"
    monkeypatch.setenv("MINT_TEMPLATE_WRITERS", secret_writer)

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(TemplateWriteForbidden),
    ):
        await mint_update_preset_palette(
            name="klawd",
            palette={"primary": "#000000"},
            author="impostor",
            ctx=_make_ctx(),
        )

    # The allowlisted secret_writer MUST NOT appear in any log message.
    for record in caplog.records:
        assert secret_writer not in record.getMessage(), (
            f"writers tuple leaked into log: {record.getMessage()!r}"
        )


# --------------------------------------------------------------------------- #
# Edge: heading-scale alone (without base_size_pt) recomputes from current.
# Exercises the heading_scale-only branch in _apply_typography_patch.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_heading_scale_only_recomputes_from_current_base(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    outcome = await mint_update_preset_typography(
        name="klawd",
        typography={"heading_scale": 1.5},
        author="alice",
        ctx=ctx,
    )
    assert outcome["patched_fields"] == ["heading_scale"]
    written = yaml.safe_load(
        (fixture_presets_dir / "klawd_v1.1.yaml").read_text()
    )
    # body.size_pt was 11 (klawd default). New heading1 = 11 * 1.5^2 = 24.75.
    assert written["typography"]["heading1"]["size_pt"] == 24.75
    assert written["typography"]["heading3"]["size_pt"] == 16.5


# --------------------------------------------------------------------------- #
# Edge: line_height-only patch updates both body and default_line_height.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_line_height_only_updates_body_and_default(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    outcome = await mint_update_preset_typography(
        name="klawd",
        typography={"line_height": 1.6},
        author="alice",
        ctx=ctx,
    )
    assert outcome["patched_fields"] == ["line_height"]
    written = yaml.safe_load(
        (fixture_presets_dir / "klawd_v1.1.yaml").read_text()
    )
    assert written["typography"]["body"]["line_height"] == 1.6
    assert written["spacing"]["default_line_height"] == 1.6


# --------------------------------------------------------------------------- #
# Edge: heading_font-only patch — proves _set_font hits 3 heading targets.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_heading_font_only_updates_three_headings(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    outcome = await mint_update_preset_typography(
        name="klawd",
        typography={"heading_font": "Georgia"},
        author="alice",
        ctx=ctx,
    )
    assert outcome["patched_fields"] == ["heading_font"]
    written = yaml.safe_load(
        (fixture_presets_dir / "klawd_v1.1.yaml").read_text()
    )
    assert written["typography"]["heading1"]["font"] == "Georgia"
    assert written["typography"]["heading2"]["font"] == "Georgia"
    assert written["typography"]["heading3"]["font"] == "Georgia"
    # Body fonts unchanged.
    assert written["typography"]["body"]["font"] == "Arial"


# --------------------------------------------------------------------------- #
# Edge: same-value patch produces empty patched_fields (idempotent no-op).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_same_value_patch_idempotent(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()
    # klawd.primary = "#1B3A5C" in the baseline.
    outcome = await mint_update_preset_palette(
        name="klawd",
        palette={"primary": "#1B3A5C"},
        author="alice",
        ctx=ctx,
    )
    assert outcome["patched_fields"] == []
    # File still created (version bump is mandatory — auditable no-op).
    assert (fixture_presets_dir / "klawd_v1.1.yaml").exists()


# --------------------------------------------------------------------------- #
# Edge: malformed-preset defensive guards — patch _load_current_preset_raw
# to return a preset where a top-level sub-tree is not a mapping. Each
# tool surface raises InvalidPatch rather than corrupting data.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tool_fn,surface,patch,broken_key,broken_value",
    [
        (
            mint_update_preset_palette,
            "palette",
            {"primary": "#000000"},
            "color_palette",
            ["not", "a", "dict"],
        ),
        (
            mint_update_preset_typography,
            "typography",
            {"body_font": "Inter"},
            "typography",
            "not-a-mapping",
        ),
        (
            mint_update_preset_spacing,
            "spacing",
            {"paragraph_pt": 5},
            "spacing",
            42,
        ),
    ],
)
@pytest.mark.asyncio
async def test_malformed_preset_subtree_raises_invalid_patch(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    tool_fn: Any,
    surface: str,
    patch: dict[str, Any],
    broken_key: str,
    broken_value: Any,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    def _broken_loader(name: str) -> tuple[dict[str, Any], str, Path]:
        return {broken_key: broken_value}, "1.0", Path("/tmp/fake")

    monkeypatch.setattr(
        preset_edit_module, "_load_current_preset_raw", _broken_loader
    )

    kwargs: dict[str, Any] = {surface: patch}
    with pytest.raises(InvalidPatch, match="is not a mapping"):
        await tool_fn(name="klawd", author="alice", ctx=ctx, **kwargs)


# --------------------------------------------------------------------------- #
# Edge: PresetNotFound when _load_current_preset_raw resolves to a non-
# mapping YAML (defensive guard inside _load_current_preset_raw itself).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_load_current_preset_rejects_non_mapping_yaml(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A versioned sibling that parses to a YAML list (not a dict)
    triggers _load_current_preset_raw's defensive PresetNotFound branch."""
    _admit(monkeypatch, "alice")
    # Drop a versioned sibling whose body is a YAML list — outranks
    # built-in klawd by virtue of living in PRESETS_DIR.
    (fixture_presets_dir / "klawd_v9.9.yaml").write_text(
        "- just\n- a\n- list\n", encoding="utf-8"
    )
    ctx = _make_ctx()
    with pytest.raises(PresetNotFound, match="did not"):
        await mint_update_preset_palette(
            name="klawd",
            palette={"primary": "#000000"},
            author="alice",
            ctx=ctx,
        )


# --------------------------------------------------------------------------- #
# Edge: BLOCK_PRESET_WRITE payload carries expected slots.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_block_preset_write_marker_payload(
    fixture_presets_dir: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _admit(monkeypatch, "alice")
    ctx = _make_ctx()

    with caplog.at_level(logging.INFO):
        outcome = await mint_update_preset_palette(
            name="klawd",
            palette={"primary": "#AABBCC"},
            author="alice",
            ctx=ctx,
        )

    write_msgs = [
        r.getMessage()
        for r in caplog.records
        if "BLOCK_PRESET_WRITE" in r.getMessage()
    ]
    assert len(write_msgs) == 1
    msg = write_msgs[0]
    assert "[MP-ThemeEdit][palette][BLOCK_PRESET_WRITE]" in msg
    assert "name=klawd" in msg
    assert "version=1.1" in msg
    assert "predecessor_version=1.0" in msg
    assert "patched_fields=['primary']" in msg
    assert outcome["audit_id"] in msg
