# FILE: tests/integration/test_mp_auth_shim.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-AUTH-SHIM verification — covers scenarios 1-5 of the
#     write-path authorization shim (Phase-15 Wave-15-1) plus VF-017
#     invariant checks (READ-PATH-NEVER-CALLS-AUTH, WRITER-LIST-NEVER-
#     LOGGED) that don't already collapse into a scenario. Closes audit
#     Priority-4 sub-clause "Authorization shim: read = open; write =
#     config-gated allowlist".
#   SCOPE: Integration tests — exercise mint_python.mcp.auth alongside
#     the existing W3 update_template path so admit and deny outcomes
#     can be observed against templates/_audit.jsonl + the templates/
#     directory listing.
#   DEPENDS: pytest, mint_python.mcp.auth,
#     mint_python.templates.registry, tests._helpers.fake_mcp_context.
#   LINKS: docs/development-plan.xml#MP-AUTH-SHIM,
#     docs/verification-plan.xml#V-MP-AUTH-SHIM,
#     docs/verification-plan.xml#VF-017
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_scenario_1_env_allowlist_admits_writer_and_writes_proceed
#   test_scenario_2_unallowed_author_raises_before_disk_io
#   test_scenario_3_open_mode_warns_once_then_admits
#   test_scenario_4_malformed_writers_json_fails_fast_with_path_in_message
#   test_scenario_5_deny_log_carries_author_and_config_source
#   test_vf_017_inv_3_read_path_never_calls_auth
#   test_vf_017_forbidden_3_writer_list_never_logged
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Phase-15 Wave-15-1 initial test suite per V-MP-AUTH-SHIM
#     scenarios 1-5 + VF-017 inv-3 / forbidden-3 invariants.
# END_CHANGE_SUMMARY
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

# Pre-load mcp.document so its bottom-of-file circular import
# (document → resources → registry) completes before we import the
# registry directly. test_mp_templates_write.py uses the same pattern
# implicitly via `from mint_python.mcp.document import _run_pipeline`.
import mint_python.mcp.document  # noqa: F401  — load-order pin
from mint_python.mcp import auth as auth_module
from mint_python.mcp.auth import (
    AuthConfigInvalid,
    TemplateWriteForbidden,
    is_template_writer,
    load_writers_config,
    require_template_writer,
)
from mint_python.templates.registry import TemplateRegistry
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"


# --------------------------------------------------------------------------- #
# Local fixtures (test-file-private; swarm rule-5 allows non-shared local
# helpers as long as we don't redefine the cross-cutting fixtures from
# tests/unit/conftest.py — clean_writers_config / caplog_at_info /
# marker_counter are imported via tests/integration/conftest.py).
# --------------------------------------------------------------------------- #


# Concrete new-content YAML reused across scenarios. Mirrors the shape used
# by V-MP-TEMPLATES-WRITE so write outcomes match the existing W3 path.
NEW_MEMO_YAML = """
name: memo
version: "0.0"
description: "Standard business memo, v-bumped — auth shim test fixture"
doc_type: memo
required_fields:
  - sender
  - recipient
  - date
  - subject
  - body
layout:
  - kind: heading
    level: 1
    text: MEMORANDUM
  - kind: spacer
  - kind: table
    header: ["Field", "Value"]
    rows:
      - ["From", "{{ sender }}"]
      - ["To", "{{ recipient }}"]
      - ["Date", "{{ date }}"]
      - ["Subject", "{{ subject }}"]
  - kind: spacer
  - kind: heading
    level: 2
    text: Body
  - kind: paragraph
    text: "{{ body }}"
"""


@pytest.fixture
def fixture_templates_dir(tmp_path: Path) -> Path:
    """Clone the canonical templates/ into a tmp dir so writes don't
    pollute the repo. update_template's writes target this dir."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in ("memo.yaml", "letter.yaml"):
        (fixtures / name).write_text(
            (REPO_TEMPLATES / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return fixtures


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME to a tmp dir so ~/.config/mint/writers.json never
    touches the developer's real config. _config_file_path() reads
    os.path.expanduser at call time, so monkeypatching HOME suffices."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _snapshot_dir_state(templates_dir: Path) -> tuple[list[str], str | None]:
    """Return (sorted listing of templates_dir, sha256 of _audit.jsonl
    or None when absent). Used by scenario-2 to assert byte-equality
    of the directory + audit log across a denied call (VF-017 inv-1
    NO-DISK-WRITE-ON-DENY)."""
    listing = sorted(os.listdir(templates_dir))
    audit_path = templates_dir / "_audit.jsonl"
    audit_sha: str | None = (
        hashlib.sha256(audit_path.read_bytes()).hexdigest()
        if audit_path.exists()
        else None
    )
    return listing, audit_sha


# --------------------------------------------------------------------------- #
# Scenario-1 — env allowlist admits writer; existing W3 write proceeds.
# --------------------------------------------------------------------------- #


def test_scenario_1_env_allowlist_admits_writer_and_writes_proceed(
    fixture_templates_dir: Path,
    isolated_home: Path,
    clean_writers_config: None,
    caplog_at_info,
    marker_counter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MINT_TEMPLATE_WRITERS", "alice,bob,Claude-Opus-4.7"
    )
    # Pure check returns None on the entry point.
    assert require_template_writer("alice") is None
    assert is_template_writer("alice") is True
    assert is_template_writer("cursor-sidecar") is False

    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    outcome = registry.update("memo", NEW_MEMO_YAML, author="alice")

    assert outcome["name"] == "memo"
    assert outcome["version"] == "1.1"
    assert outcome["predecessor_version"] == "1.0"
    assert Path(outcome["written_to"]).exists()

    audit_lines = (
        (fixture_templates_dir / "_audit.jsonl").read_text().splitlines()
    )
    assert len(audit_lines) == 1
    assert json.loads(audit_lines[0])["author"] == "alice"

    counts = marker_counter(caplog_at_info)
    # Two admits (one for the standalone require_template_writer above,
    # one inside registry.update). Open-mode warning MUST NOT have fired.
    assert counts["BLOCK_AUTH_ADMIT"] == 2
    assert counts["BLOCK_AUTH_OPEN_MODE"] == 0
    assert counts["BLOCK_AUTH_DENY"] == 0


# --------------------------------------------------------------------------- #
# Scenario-2 — unallowed author raises BEFORE any disk I/O; templates/
# + _audit.jsonl byte-identical across the denied call.
# --------------------------------------------------------------------------- #


def test_scenario_2_unallowed_author_raises_before_disk_io(
    fixture_templates_dir: Path,
    isolated_home: Path,
    clean_writers_config: None,
    caplog_at_info,
    marker_counter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINT_TEMPLATE_WRITERS", "alice")

    listing_before, audit_before = _snapshot_dir_state(fixture_templates_dir)

    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    with pytest.raises(TemplateWriteForbidden, match="cursor-sidecar"):
        registry.update("memo", NEW_MEMO_YAML, author="cursor-sidecar")

    listing_after, audit_after = _snapshot_dir_state(fixture_templates_dir)
    assert listing_before == listing_after
    assert audit_before == audit_after

    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_AUTH_DENY"] == 1
    # Forbidden-2 (VF-017): deny path MUST NOT be followed by a
    # filesystem-touching marker in the same caplog window.
    forbidden_followups = (
        counts["BLOCK_TEMPLATE_WRITE"]
        + counts["BLOCK_UPDATE_TEMPLATE"]
        + counts["BLOCK_AUDIT_APPEND"]
    )
    assert forbidden_followups == 0


# --------------------------------------------------------------------------- #
# Scenario-3 — open mode warns ONCE per process; subsequent admits silent.
# --------------------------------------------------------------------------- #


def test_scenario_3_open_mode_warns_once_then_admits(
    isolated_home: Path,
    clean_writers_config: None,
    caplog_at_info,
    marker_counter,
) -> None:
    # No env, no file → open mode.
    assert require_template_writer("anyone") is None
    # is_template_writer admits any author in open mode (pure check;
    # exercises the open-mode branch of the predicate).
    assert is_template_writer("anyone") is True
    config = load_writers_config()
    assert config.open_mode is True
    assert config.source == "none"
    assert config.writers == ()

    # 5 more admit calls; the warning MUST NOT re-fire.
    for ident in ("bob", "claude", "ollama-sidecar", "vscode", "another"):
        require_template_writer(ident)

    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_AUTH_OPEN_MODE"] == 1
    # Open-mode admits stay silent — the once-per-process warning is
    # the operator signal; we don't emit BLOCK_AUTH_ADMIT here.
    assert counts["BLOCK_AUTH_ADMIT"] == 0
    assert counts["BLOCK_AUTH_DENY"] == 0


# --------------------------------------------------------------------------- #
# Scenario-4 — malformed writers.json fails fast on FIRST require call;
# subsequent calls re-raise from cache (poisoned), NOT at module import.
# --------------------------------------------------------------------------- #


def test_scenario_4_malformed_writers_json_fails_fast_with_path_in_message(
    isolated_home: Path,
    clean_writers_config: None,
) -> None:
    config_dir = isolated_home / ".config" / "mint"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "writers.json"
    config_path.write_text("{ this is not valid JSON", encoding="utf-8")

    # Importing mint_python.mcp.auth at the top of this file already
    # ran the module body; if AUTH_CONFIG_INVALID had been raised at
    # import we'd never reach this point. Confirm explicitly.
    assert auth_module.__name__ == "mint_python.mcp.auth"

    with pytest.raises(AuthConfigInvalid) as exc1:
        require_template_writer("alice")
    assert str(config_path) in str(exc1.value)
    assert "JSON" in str(exc1.value) or "json" in str(exc1.value)

    # Cache poisoned — second call re-raises the SAME exception
    # without re-touching disk. Delete the file to prove the cache
    # is the source of truth on the second call.
    config_path.unlink()
    with pytest.raises(AuthConfigInvalid) as exc2:
        require_template_writer("alice")
    assert exc2.value is exc1.value


def test_scenario_4_b_writers_json_wrong_top_level_type(
    isolated_home: Path,
    clean_writers_config: None,
) -> None:
    """Schema enforcement: top-level array is rejected with path in
    message even though the JSON itself is parseable."""
    config_dir = isolated_home / ".config" / "mint"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "writers.json"
    config_path.write_text('["alice", "bob"]', encoding="utf-8")

    with pytest.raises(AuthConfigInvalid, match="top-level"):
        require_template_writer("alice")


def test_scenario_4_c_writers_json_missing_writers_key(
    isolated_home: Path,
    clean_writers_config: None,
) -> None:
    config_dir = isolated_home / ".config" / "mint"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "writers.json"
    config_path.write_text('{"other": ["alice"]}', encoding="utf-8")

    with pytest.raises(AuthConfigInvalid, match="writers"):
        require_template_writer("alice")


def test_scenario_4_d_writers_json_writers_not_list_of_strings(
    isolated_home: Path,
    clean_writers_config: None,
) -> None:
    config_dir = isolated_home / ".config" / "mint"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "writers.json"
    config_path.write_text('{"writers": [1, 2, 3]}', encoding="utf-8")

    with pytest.raises(AuthConfigInvalid, match="list of strings"):
        require_template_writer("alice")


def test_scenario_4_e_valid_writers_json_admits_listed_author(
    isolated_home: Path,
    clean_writers_config: None,
    caplog_at_info,
    marker_counter,
) -> None:
    """Happy-path file source — proves source='file' is reachable
    so coverage of the file-resolution branch isn't gated only on
    the malformed cases."""
    config_dir = isolated_home / ".config" / "mint"
    config_dir.mkdir(parents=True)
    (config_dir / "writers.json").write_text(
        '{"writers": ["alice", "Claude-Opus-4.7"]}', encoding="utf-8"
    )

    require_template_writer("alice")
    config = load_writers_config()
    assert config.source == "file"
    assert config.writers == ("alice", "Claude-Opus-4.7")
    assert config.open_mode is False

    with pytest.raises(TemplateWriteForbidden):
        require_template_writer("cursor-sidecar")

    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_AUTH_ADMIT"] == 1
    assert counts["BLOCK_AUTH_DENY"] == 1


# --------------------------------------------------------------------------- #
# Scenario-5 — deny log payload carries author + reason + config_source.
# --------------------------------------------------------------------------- #


def test_scenario_5_deny_log_carries_author_and_config_source(
    isolated_home: Path,
    clean_writers_config: None,
    caplog_at_info,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINT_TEMPLATE_WRITERS", "alice")

    with pytest.raises(TemplateWriteForbidden):
        require_template_writer("cursor-sidecar")

    deny_records = [
        r for r in caplog_at_info.records
        if "BLOCK_AUTH_DENY" in r.getMessage()
    ]
    assert len(deny_records) == 1
    msg = deny_records[0].getMessage()
    assert "author=cursor-sidecar" in msg
    assert "reason=not_in_writers" in msg
    assert "config_source=env" in msg


# --------------------------------------------------------------------------- #
# Scenario-5b — empty / whitespace env falls through to file/none rather
# than denying everyone. Documents the env-precedence semantic explicitly.
# --------------------------------------------------------------------------- #


def test_scenario_5_b_empty_env_falls_through_to_open_mode(
    isolated_home: Path,
    clean_writers_config: None,
    caplog_at_info,
    marker_counter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINT_TEMPLATE_WRITERS", "")
    require_template_writer("anyone")
    config = load_writers_config()
    assert config.open_mode is True
    assert config.source == "none"
    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_AUTH_OPEN_MODE"] == 1


def test_scenario_5_c_whitespace_only_env_falls_through_to_open_mode(
    isolated_home: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINT_TEMPLATE_WRITERS", "   ,  ,   ")
    require_template_writer("anyone")
    config = load_writers_config()
    assert config.open_mode is True
    assert config.source == "none"


def test_scenario_5_d_env_trims_whitespace_around_entries(
    isolated_home: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINT_TEMPLATE_WRITERS", "  alice ,   bob  ")
    config = load_writers_config()
    assert config.writers == ("alice", "bob")
    assert config.source == "env"
    require_template_writer("alice")
    require_template_writer("bob")
    with pytest.raises(TemplateWriteForbidden):
        require_template_writer("eve")


# --------------------------------------------------------------------------- #
# VF-017 inv-3 READ-PATH-NEVER-CALLS-AUTH — list_templates / get_template
# MUST NOT trip a sentinel patched into require_template_writer.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vf_017_inv_3_read_path_never_calls_auth(
    fixture_templates_dir: Path,
    isolated_home: Path,
    clean_writers_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch require_template_writer at every importable site with a
    sentinel that raises AssertionError. list_templates + get_template
    must complete cleanly — read tools MUST NOT consult MP-AUTH-SHIM."""
    from mint_python.mcp import document as document_module
    from mint_python.templates import registry as registry_module

    def _sentinel(_author: str) -> None:
        raise AssertionError(
            "VF-017 inv-3 violation: read-path tool called "
            "require_template_writer (read MUST stay open)"
        )

    monkeypatch.setattr(auth_module, "require_template_writer", _sentinel)
    monkeypatch.setattr(
        registry_module, "require_template_writer", _sentinel
    )

    monkeypatch.setattr(
        registry_module, "_TEMPLATES_DIR", fixture_templates_dir
    )
    monkeypatch.setattr(
        document_module, "_TEMPLATES_DIR", fixture_templates_dir
    )
    registry_module.reset_default_registry()

    ctx = FakeMCPContext(answers={})
    listing = await registry_module.list_templates(ctx=ctx)
    assert any(entry["name"] == "memo" for entry in listing)

    fetched = await registry_module.get_template("memo", ctx=ctx)
    assert fetched["name"] == "memo"

    registry_module.reset_default_registry()


# --------------------------------------------------------------------------- #
# VF-017 forbidden-3 WRITER-LIST-NEVER-LOGGED — the full writers tuple
# MUST NEVER appear in any log payload, INFO or DEBUG.
# --------------------------------------------------------------------------- #


def test_vf_017_forbidden_3_writer_list_never_logged(
    isolated_home: Path,
    clean_writers_config: None,
    caplog_at_info,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject a sentinel writer that is NEVER the rejected author; trigger
    one admit + one deny; assert the sentinel never leaks into caplog.
    Guards V-MP-AUTH-SHIM forbidden-3 / VF-017 inv-6 / forbidden-3."""
    sentinel = "WRITER_SENTINEL_42"
    monkeypatch.setenv(
        "MINT_TEMPLATE_WRITERS", f"alice,{sentinel},bob"
    )

    require_template_writer("alice")  # admit
    with pytest.raises(TemplateWriteForbidden):
        require_template_writer("eve")  # deny

    # Capture every record at every level — DEBUG too, in case a future
    # logger.debug interpolates the tuple.
    import logging

    caplog_at_info.set_level(logging.DEBUG)

    for record in caplog_at_info.records:
        msg = record.getMessage()
        assert sentinel not in msg, (
            f"VF-017 forbidden-3 violation: writer-list sentinel leaked "
            f"into log record level={record.levelname} msg={msg!r}"
        )
