# FILE: tests/unit/test_mcp_resources_unit.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Cover uncovered branches in mint_python.mcp.resources
#     (_parse_uri, _template_resource_content, template_resource,
#      preset_resource) so the package hits 100 % line coverage
#     without depending on the full integration harness.
#   SCOPE: Unit-level tests for pure/async helpers.
#   DEPENDS: mint_python.mcp.resources
#   LINKS: docs/verification-plan.xml#V-MP-MCP-RESOURCES
# END_MODULE_CONTRACT

from __future__ import annotations

import pytest

from mint_python.mcp.resources import (
    ResourceNotFound,
    ResourceUriInvalid,
    _parse_uri,
    _preset_resource_content,
    _template_resource_content,
    preset_resource,
    template_resource,
)


class TestParseUri:
    def test_template(self) -> None:
        kind, name = _parse_uri("mint://template/memo")
        assert kind == "template"
        assert name == "memo"

    def test_preset(self) -> None:
        kind, name = _parse_uri("mint://preset/klawd")
        assert kind == "preset"
        assert name == "klawd"

    def test_invalid_scheme_raises(self) -> None:
        with pytest.raises(ResourceUriInvalid):
            _parse_uri("https://example.com")

    def test_path_traversal_raises(self) -> None:
        with pytest.raises(ResourceUriInvalid):
            _parse_uri("mint://template/../core/style.py")


class TestTemplateResourceContent:
    def test_known_template_returns_yaml(self) -> None:
        content = _template_resource_content("memo")
        assert "name: memo" in content
        assert "version:" in content

    def test_unknown_template_raises(self) -> None:
        with pytest.raises(ResourceNotFound):
            _template_resource_content("nonexistent_template_xyz")


class TestPresetResourceContent:
    def test_known_preset_returns_text(self) -> None:
        content = _preset_resource_content("klawd")
        assert "name:" in content

    def test_unknown_preset_raises(self) -> None:
        with pytest.raises(ResourceNotFound):
            _preset_resource_content("nonexistent_preset_xyz")


@pytest.mark.asyncio
class TestResourceHandlers:
    async def test_template_resource_handler(self) -> None:
        content = await template_resource("memo")
        assert "name: memo" in content

    async def test_preset_resource_handler(self) -> None:
        content = await preset_resource("klawd")
        assert "name:" in content
