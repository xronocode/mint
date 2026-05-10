# FILE: tests/integration/test_mp_mcp_resources.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-MCP-RESOURCES verification — covers scenarios 1-5 of
#     the FastMCP resource handlers (mint://template/<name>,
#     mint://preset/<name>) and the symmetric list_presets / get_preset
#     tools.
#   SCOPE: Integration tests — exercise URI parsing, content
#     serialization, the templated @server.resource handlers, and the
#     tool wrappers against a tmp_path-isolated registry.
#   DEPENDS: pytest, mint_python.mcp.resources, mint_python.core.style,
#     mint_python.templates.registry, tests._helpers.fake_mcp_context.
# END_MODULE_CONTRACT
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from mint_python.core.style import BUILTIN_PRESETS
from mint_python.mcp.resources import (
    ResourceDescriptor,
    ResourceNotFound,
    ResourceUriInvalid,
    _list_mint_resources,
    _parse_uri,
    _preset_resource_content,
    _template_resource_content,
    get_preset,
    list_presets,
    preset_resource,
    template_resource,
)
from mint_python.templates.registry import reset_default_registry
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def fixture_templates_dir(tmp_path: Path) -> Path:
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in ("memo.yaml", "letter.yaml"):
        (fixtures / name).write_text(
            (REPO_TEMPLATES / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return fixtures


@pytest.fixture(autouse=True)
def _registry_over_fixture(
    fixture_templates_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """All resource lookups go through the lazy default registry; point
    it at the fixture dir so tests don't depend on the live repo state
    being identical across runs."""
    from mint_python.templates import registry as reg_module

    monkeypatch.setattr(reg_module, "_TEMPLATES_DIR", fixture_templates_dir)
    reset_default_registry()
    yield fixture_templates_dir
    reset_default_registry()


# --------------------------------------------------------------------------- #
# Scenario-1: enumerator returns 7 entries (memo + letter + 5 presets);
# each entry carries uri, name, mimeType, description.
# --------------------------------------------------------------------------- #


def test_scenario_1_list_mint_resources_enumerates_templates_and_presets() -> None:
    descriptors = _list_mint_resources()
    uris = {d.uri for d in descriptors}

    # Expected: 2 templates + 5 builtin presets.
    assert "mint://template/memo" in uris
    assert "mint://template/letter" in uris
    assert "mint://preset/klawd" in uris
    assert "mint://preset/claret_serif" in uris
    assert "mint://preset/alga_corporate" in uris
    assert "mint://preset/minimal" in uris
    assert "mint://preset/compact" in uris
    assert len(descriptors) == 7

    # Each entry has all four fields populated.
    for d in descriptors:
        assert d.uri
        assert d.name
        assert d.mime_type
        assert d.description


def test_scenario_1_b_descriptor_to_dict_shape() -> None:
    """ResourceDescriptor.to_dict matches MCP resource schema keys —
    note `mimeType` (camelCase) per the protocol spec, not Python's
    snake_case `mime_type` dataclass field."""
    descriptor = ResourceDescriptor(
        uri="mint://template/memo",
        name="memo",
        mime_type="application/x-yaml",
        description="...",
    )
    out = descriptor.to_dict()
    assert set(out.keys()) == {"uri", "name", "mimeType", "description"}
    assert out["mimeType"] == "application/x-yaml"


# --------------------------------------------------------------------------- #
# Scenario-2: resources/read mint://template/memo returns YAML content.
# --------------------------------------------------------------------------- #


def test_scenario_2_template_resource_returns_yaml_content() -> None:
    content = _template_resource_content("memo")
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert parsed["name"] == "memo"
    assert parsed["version"] == "1.0"
    assert parsed["doc_type"] == "memo"
    assert "required_fields" in parsed
    assert "layout" in parsed


@pytest.mark.asyncio
async def test_scenario_2_b_template_resource_handler_callable_directly() -> None:
    """The @server.resource-decorated handler is callable in tests
    (FastMCP's decorator preserves the underlying coroutine)."""
    content = await template_resource("memo")
    parsed = yaml.safe_load(content)
    assert parsed["name"] == "memo"


def test_preset_resource_returns_raw_text() -> None:
    """Preset resources return the raw file content (YAML or JSON),
    preserving canonical $schema URLs and author comments."""
    klawd = _preset_resource_content("klawd")
    assert "name: klawd" in klawd
    assert "$schema:" in klawd  # canonical schema URL preserved

    minimal = _preset_resource_content("minimal")
    parsed = json.loads(minimal)
    assert parsed["name"] == "minimal"


@pytest.mark.asyncio
async def test_preset_resource_handler_callable_directly() -> None:
    content = await preset_resource("klawd")
    assert "name: klawd" in content


# --------------------------------------------------------------------------- #
# Scenario-3: list_presets / get_preset tools mirror the resources
# protocol for clients without resources/list support.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_list_presets_tool_matches_resource_listing() -> None:
    ctx = FakeMCPContext(answers={})
    tool_listing = await list_presets(ctx=ctx)

    # Same set of URIs as the preset entries from _list_mint_resources.
    tool_uris = {entry["uri"] for entry in tool_listing}
    enumerator_uris = {
        d.uri for d in _list_mint_resources()
        if d.uri.startswith("mint://preset/")
    }
    assert tool_uris == enumerator_uris
    # Each entry has the MCP-schema keys.
    for entry in tool_listing:
        assert set(entry.keys()) == {"uri", "name", "mimeType", "description"}


@pytest.mark.asyncio
async def test_scenario_3_b_get_preset_tool_returns_content_and_mime() -> None:
    ctx = FakeMCPContext(answers={})
    out = await get_preset("klawd", ctx=ctx)
    assert out["name"] == "klawd"
    assert out["uri"] == "mint://preset/klawd"
    assert out["mimeType"] == "application/x-yaml"
    assert "name: klawd" in out["content"]

    # JSON preset gets application/json.
    out_minimal = await get_preset("minimal", ctx=ctx)
    assert out_minimal["mimeType"] == "application/json"
    assert json.loads(out_minimal["content"])["name"] == "minimal"


# --------------------------------------------------------------------------- #
# Scenario-4: resources/read mint://template/nonexistent → RESOURCE_NOT_FOUND.
# --------------------------------------------------------------------------- #


def test_scenario_4_unknown_template_raises_resource_not_found() -> None:
    with pytest.raises(ResourceNotFound, match="RESOURCE_NOT_FOUND"):
        _template_resource_content("nonexistent")


def test_scenario_4_b_unknown_preset_raises_resource_not_found() -> None:
    with pytest.raises(ResourceNotFound, match="RESOURCE_NOT_FOUND") as exc_info:
        _preset_resource_content("nonexistent")
    # Available presets enumerated in the message.
    msg = str(exc_info.value)
    for known in ("klawd", "claret_serif", "minimal", "compact", "alga_corporate"):
        assert known in msg


@pytest.mark.asyncio
async def test_scenario_4_c_get_preset_tool_unknown_raises() -> None:
    ctx = FakeMCPContext(answers={})
    with pytest.raises(ResourceNotFound, match="RESOURCE_NOT_FOUND"):
        await get_preset("nonexistent", ctx=ctx)


# --------------------------------------------------------------------------- #
# Scenario-5: malformed URI → RESOURCE_URI_INVALID, message names
# accepted schemes.
# --------------------------------------------------------------------------- #


def test_scenario_5_malformed_uri_lists_supported_schemes() -> None:
    bad_uris = [
        "mint://document/memo",  # wrong scheme component
        "http://template/memo",  # wrong protocol
        "mint:/template/memo",  # missing slash
        "mint://template/",  # empty name
        "mint://template/memo/extra",  # path traversal attempt
        "mint://preset/../../etc/passwd",  # forbidden-1: no filesystem reach
    ]
    for uri in bad_uris:
        with pytest.raises(ResourceUriInvalid, match="RESOURCE_URI_INVALID") as exc_info:
            _parse_uri(uri)
        msg = str(exc_info.value)
        assert "mint://template/<name>" in msg
        assert "mint://preset/<name>" in msg


# --------------------------------------------------------------------------- #
# Forbidden-1: resource handlers MUST NOT expose anything outside
# templates/ and presets/. Verified structurally — the regex set IS
# the entire reachable surface, and a representative path-traversal
# attempt fails parsing rather than reaching the filesystem.
# --------------------------------------------------------------------------- #


def test_forbidden_1_no_filesystem_reach_via_path_traversal() -> None:
    """Attempted URI with `../` cannot match either regex — the URI
    parser blocks it before any filesystem operation."""
    with pytest.raises(ResourceUriInvalid):
        _parse_uri("mint://template/../core/style.py")


def test_forbidden_1_b_no_other_uri_schemes_accepted() -> None:
    """Schemes outside the two we explicitly support don't match."""
    with pytest.raises(ResourceUriInvalid):
        _parse_uri("file:///etc/passwd")
    with pytest.raises(ResourceUriInvalid):
        _parse_uri("mint://config/server.yaml")


# --------------------------------------------------------------------------- #
# parse_uri positive cases.
# --------------------------------------------------------------------------- #


def test_parse_uri_template_returns_template_kind() -> None:
    kind, name = _parse_uri("mint://template/memo")
    assert kind == "template"
    assert name == "memo"


def test_parse_uri_preset_returns_preset_kind() -> None:
    kind, name = _parse_uri("mint://preset/klawd")
    assert kind == "preset"
    assert name == "klawd"


def test_parse_uri_accepts_dashes_and_underscores_in_names() -> None:
    """Names with underscores or dashes round-trip — claret_serif preset
    name and any future hyphenated doc_types remain reachable."""
    kind, name = _parse_uri("mint://preset/claret_serif")
    assert kind == "preset"
    assert name == "claret_serif"
    kind, name = _parse_uri("mint://template/cover-letter")
    assert kind == "template"
    assert name == "cover-letter"


# --------------------------------------------------------------------------- #
# Logging: BLOCK_RESOURCE_READ fires once per resource read with kind+name.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resource_read_log_marker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="mint_python.mcp.resources"):
        await template_resource("memo")
        await preset_resource("klawd")

    msgs = [
        r.getMessage()
        for r in caplog.records
        if "BLOCK_RESOURCE_READ" in r.getMessage()
    ]
    assert len(msgs) == 2
    assert "[MP-McpResources]" in msgs[0]
    assert "kind=template" in msgs[0]
    assert "name=memo" in msgs[0]
    assert "kind=preset" in msgs[1]
    assert "name=klawd" in msgs[1]


# --------------------------------------------------------------------------- #
# Versioned siblings collapse — _list_mint_resources surfaces ONE entry
# per template name (latest version), not per (name, version) tuple.
# --------------------------------------------------------------------------- #


def test_list_mint_resources_collapses_versioned_siblings(
    fixture_templates_dir: Path,
) -> None:
    """After update_template adds memo_v1.1.yaml, the resources listing
    still has one mint://template/memo entry (latest); explicit
    version pinning is the get_template tool's job."""
    from mint_python.templates.registry import TemplateRegistry

    NEW_YAML = (
        "name: memo\nversion: \"0.0\"\ndescription: \"v1.1\"\n"
        "required_fields: [a]\nlayout: []\n"
    )
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    registry.update("memo", NEW_YAML, author="Claude")
    reset_default_registry()  # force the lazy singleton to re-discover

    descriptors = _list_mint_resources()
    template_uris = [d.uri for d in descriptors if d.uri.startswith("mint://template/")]
    assert template_uris.count("mint://template/memo") == 1
    # And the served content is the bumped version.
    parsed = yaml.safe_load(_template_resource_content("memo"))
    assert parsed["version"] == "1.1"
