# FILE: tests/integration/test_mp_templates_registry.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-TEMPLATES-REGISTRY verification — covers scenarios 1-5
#     of the read-only registry over templates/. Build a TemplateRegistry
#     against tmp_path-isolated fixture templates so the global
#     templates/ directory is not under test (per autonomy-assessment:
#     SWARM-SAFE / no shared state).
#   SCOPE: Integration tests — exercise the registry directly and
#     through the @server.tool wrappers (list_templates / get_template).
#   DEPENDS: pytest, mint_python.templates.registry,
#     tests._helpers.fake_mcp_context.
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mint_python.templates.registry import (
    TemplateInvalidSchema,
    TemplateNotFound,
    TemplateRegistry,
    get_default_registry,
    get_template,
    list_templates,
    reset_default_registry,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def fixture_templates_dir(tmp_path: Path) -> Path:
    """A clean templates/ snapshot for the registry under test. Includes
    the canonical memo.yaml + letter.yaml from the repo so the
    registry tests don't drift if the on-disk templates change."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in ("memo.yaml", "letter.yaml"):
        (fixtures / name).write_text(
            (REPO_TEMPLATES / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return fixtures


@pytest.fixture
def server_registry_over_fixture(
    fixture_templates_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the lazy default registry at the fixture dir (used by the
    @server.tool wrappers) and reset between tests so each gets a fresh
    load. Returns the dir for tests that need direct file access."""
    from mint_python.templates import registry as reg_module

    monkeypatch.setattr(reg_module, "_TEMPLATES_DIR", fixture_templates_dir)
    reset_default_registry()
    yield fixture_templates_dir
    reset_default_registry()


# --------------------------------------------------------------------------- #
# Scenario-1: list_templates returns ≥2 entries (memo + letter); each
# carries name, version, doc_type, description, last_modified.
# --------------------------------------------------------------------------- #


def test_scenario_1_registry_lists_memo_and_letter(
    fixture_templates_dir: Path,
) -> None:
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    summaries = registry.summaries()

    names = sorted(s.name for s in summaries)
    assert names == ["letter", "memo"]

    by_name = {s.name: s for s in summaries}
    for name in ("memo", "letter"):
        s = by_name[name]
        assert s.version == "1.0"
        assert s.doc_type == name
        assert s.description  # non-empty
        assert s.last_modified  # ISO-8601 timestamp string


# --------------------------------------------------------------------------- #
# Scenario-2: get_template('memo') matches the YAML on disk; required_fields
# matches MEMO_REQUIRED_FIELDS verbatim.
# --------------------------------------------------------------------------- #


def test_scenario_2_get_memo_matches_yaml_and_required_fields(
    fixture_templates_dir: Path,
) -> None:
    from mint_python.mcp.document import MEMO_REQUIRED_FIELDS

    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    schema = registry.get("memo")

    assert schema.name == "memo"
    assert schema.version == "1.0"
    assert schema.doc_type == "memo"
    assert schema.required_fields == MEMO_REQUIRED_FIELDS
    # Layout is non-empty and the first entry is a heading (canonical
    # memo template starts with MEMORANDUM).
    assert len(schema.layout) > 0
    assert schema.layout[0].get("kind") == "heading"


# --------------------------------------------------------------------------- #
# Scenario-3: get_template('nonexistent') raises TEMPLATE_NOT_FOUND with
# the available templates listed in the message.
# --------------------------------------------------------------------------- #


def test_scenario_3_unknown_template_raises_with_available_list(
    fixture_templates_dir: Path,
) -> None:
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)

    with pytest.raises(TemplateNotFound, match="TEMPLATE_NOT_FOUND") as exc_info:
        registry.get("nonexistent")
    msg = str(exc_info.value)
    assert "memo" in msg
    assert "letter" in msg


# --------------------------------------------------------------------------- #
# Scenario-4: malformed YAML in templates/ surfaces as
# TEMPLATE_INVALID_SCHEMA at registry-load (fail-fast), not at first
# create_document call.
# --------------------------------------------------------------------------- #


def test_scenario_4_malformed_template_fails_at_load_not_at_use(
    fixture_templates_dir: Path,
) -> None:
    """A YAML missing 'required_fields' is invalid by schema; the registry
    must reject it at __init__ rather than letting create_document
    silently use a half-built template."""
    (fixture_templates_dir / "broken.yaml").write_text(
        "name: broken\nversion: \"1.0\"\nlayout: []\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateInvalidSchema, match="TEMPLATE_INVALID_SCHEMA"):
        TemplateRegistry(templates_dir=fixture_templates_dir)


def test_scenario_4_b_non_mapping_yaml_rejected(
    fixture_templates_dir: Path,
) -> None:
    """A YAML that parses to a list (not a mapping) also fails fast."""
    (fixture_templates_dir / "list.yaml").write_text(
        "- this is\n- not a mapping\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateInvalidSchema, match="did not parse to a mapping"):
        TemplateRegistry(templates_dir=fixture_templates_dir)


def test_scenario_4_c_wrong_field_types_rejected(
    fixture_templates_dir: Path,
) -> None:
    """required_fields must be a list of strings; 'layout' must be a list
    of mappings. Both type errors raise at load."""
    (fixture_templates_dir / "bad_required.yaml").write_text(
        "name: bad\nversion: \"1.0\"\nrequired_fields: \"not a list\"\nlayout: []\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateInvalidSchema, match="required_fields"):
        TemplateRegistry(templates_dir=fixture_templates_dir)
    (fixture_templates_dir / "bad_required.yaml").unlink()

    (fixture_templates_dir / "bad_layout.yaml").write_text(
        "name: bad\nversion: \"1.0\"\nrequired_fields: [a]\nlayout: \"not a list\"\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateInvalidSchema, match="layout"):
        TemplateRegistry(templates_dir=fixture_templates_dir)


def test_scenario_4_d_non_string_name_rejected(fixture_templates_dir: Path) -> None:
    """name + version must be strings. Non-string name (e.g. int) fails."""
    (fixture_templates_dir / "bad_name.yaml").write_text(
        "name: 42\nversion: \"1.0\"\nrequired_fields: []\nlayout: []\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateInvalidSchema, match="must be strings"):
        TemplateRegistry(templates_dir=fixture_templates_dir)


# --------------------------------------------------------------------------- #
# Scenario-5: BLOCK_LOAD_REGISTRY log marker fires once at registry init
# with payload {templates_count, doc_types, total_versions}.
# --------------------------------------------------------------------------- #


def test_scenario_5_load_registry_log_marker(
    fixture_templates_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="mint_python.templates.registry"):
        TemplateRegistry(templates_dir=fixture_templates_dir)

    load_msgs = [
        r.getMessage()
        for r in caplog.records
        if "BLOCK_LOAD_REGISTRY" in r.getMessage()
    ]
    assert len(load_msgs) == 1, f"expected exactly one BLOCK_LOAD_REGISTRY, got {load_msgs}"
    msg = load_msgs[0]
    assert "[MP-Templates]" in msg
    assert "templates_count=2" in msg
    assert "letter" in msg and "memo" in msg
    assert "total_versions=2" in msg


# --------------------------------------------------------------------------- #
# Forbidden-1: registry MUST NOT mutate templates/ on read paths. Confirm
# by snapshotting mtimes before/after a list+get cycle.
# --------------------------------------------------------------------------- #


def test_forbidden_1_read_paths_do_not_mutate_templates_dir(
    fixture_templates_dir: Path,
) -> None:
    before = {p: p.stat().st_mtime_ns for p in fixture_templates_dir.iterdir()}
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    registry.summaries()
    registry.get("memo")
    registry.get("letter")
    after = {p: p.stat().st_mtime_ns for p in fixture_templates_dir.iterdir()}
    assert before == after
    # Also: no new files appeared.
    assert set(before.keys()) == set(after.keys())


# --------------------------------------------------------------------------- #
# MCP tool surface: list_templates / get_template via the @server.tool
# wrappers exercise the lazy default registry over the fixture dir.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_templates_tool_returns_summary_dicts(
    server_registry_over_fixture: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    result = await list_templates(ctx=ctx)
    assert isinstance(result, list)
    by_name = {entry["name"]: entry for entry in result}
    assert set(by_name.keys()) == {"memo", "letter"}
    for entry in result:
        # All five expected keys present per V-MP-TEMPLATES-REGISTRY scenario-1.
        assert set(entry.keys()) == {
            "name",
            "version",
            "doc_type",
            "description",
            "last_modified",
        }


@pytest.mark.asyncio
async def test_get_template_tool_returns_full_parsed_yaml(
    server_registry_over_fixture: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    result = await get_template("memo", ctx=ctx)
    assert result["name"] == "memo"
    assert result["version"] == "1.0"
    assert result["doc_type"] == "memo"
    # Full layout + required_fields both surface (unlike list_templates'
    # summary projection).
    assert isinstance(result["required_fields"], list)
    assert isinstance(result["layout"], list)
    assert len(result["layout"]) > 0


@pytest.mark.asyncio
async def test_get_template_tool_unknown_name_raises(
    server_registry_over_fixture: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    with pytest.raises(TemplateNotFound, match="TEMPLATE_NOT_FOUND"):
        await get_template("nonexistent", ctx=ctx)


def test_default_registry_is_lazy_singleton(
    server_registry_over_fixture: Path,
) -> None:
    """get_default_registry returns the same instance across calls until
    reset_default_registry clears it. Lets the @server.tool wrappers
    avoid re-walking templates/ on every invocation."""
    a = get_default_registry()
    b = get_default_registry()
    assert a is b
    reset_default_registry()
    c = get_default_registry()
    assert c is not a
