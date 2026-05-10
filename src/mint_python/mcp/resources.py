# FILE: src/mint_python/mcp/resources.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-14 W4 (MP-MCP-RESOURCES) — wire FastMCP @server
#     .resource handlers exposing `mint://template/<name>` and
#     `mint://preset/<name>` URI schemes, and symmetric @server.tool
#     wrappers `list_presets` / `get_preset` for clients that don't
#     speak the MCP resources protocol (Claude Desktop today). Closes
#     audit's Priority-1 — design tokens + governed templates as
#     discoverable MCP resources.
#   SCOPE: Public surface = templated resource handlers (registered on
#     the shared mcp.document.server), list_presets / get_preset MCP
#     tools, ResourceNotFound / ResourceUriInvalid errors, helpers for
#     enumeration + parsing that tests exercise directly.
#   DEPENDS: mint_python.mcp.document (shared FastMCP server),
#     mint_python.templates.registry (template lookup),
#     mint_python.core.style (BUILTIN_PRESETS map).
#   LINKS: docs/development-plan.xml#MP-MCP-RESOURCES,
#     docs/verification-plan.xml#V-MP-MCP-RESOURCES,
#     docs/knowledge-graph.xml#MP-MCP-RESOURCES
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ResourceNotFound      - raised when read targets an unknown name
#   ResourceUriInvalid    - raised when a URI doesn't match either
#                           supported scheme (helps the model retry
#                           with a valid form)
#   _TEMPLATE_URI_RE /    - URI parsers; the regex set is the ENTIRE
#   _PRESET_URI_RE          accepted surface (forbidden-1: nothing
#                           outside templates/ + presets/ is reachable)
#   _parse_uri            - dispatcher returning ("template"|"preset", name)
#   _template_resource_content - serialize the latest TemplateSchema as
#                           YAML for resources/read
#   _preset_resource_content   - read the BUILTIN_PRESETS file and
#                           return its raw text (json or yaml)
#   _list_mint_resources  - one ResourceDescriptor per (latest)
#                           template + per preset; the source of truth
#                           the templated resource handlers AND the
#                           list_presets tool both project from
#   list_presets          - @server.tool async; returns preset
#                           ResourceDescriptors (subset of _list_*)
#   get_preset            - @server.tool async; returns preset content +
#                           mimeType for clients without resources/read
#   template_resource     - @server.resource templated handler for
#                           mint://template/{name}
#   preset_resource       - @server.resource templated handler for
#                           mint://preset/{name}
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-14 W4 initial implementation per
#     V-MP-MCP-RESOURCES scenarios 1-5 + forbidden-1.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastmcp import Context

from mint_python.core.style import BUILTIN_PRESETS
from mint_python.mcp.document import server
from mint_python.templates.registry import (
    TemplateNotFound,
    get_default_registry,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class MintResourceError(Exception):
    """Base for MP-MCP-RESOURCES errors."""


class ResourceNotFound(MintResourceError):  # noqa: N818 — error code RESOURCE_NOT_FOUND mirrors class name; suffix omitted intentionally
    """resources/read targeted a URI whose scheme is valid but whose name
    has no backing template / preset on disk."""


class ResourceUriInvalid(MintResourceError):  # noqa: N818 — error code RESOURCE_URI_INVALID mirrors class name; suffix omitted intentionally
    """URI didn't match any of the supported schemes
    (mint://template/<name>, mint://preset/<name>). Message names the
    accepted forms so the connected model can retry with a valid one."""


# --------------------------------------------------------------------------- #
# URI parsing
# --------------------------------------------------------------------------- #


# These regexes ARE the accepted surface. forbidden-1 — nothing outside
# templates/ and presets/ is reachable through the resource scheme — is
# enforced structurally by what the regexes accept (no path traversal,
# no scheme variants, no wildcards).
_TEMPLATE_URI_RE = re.compile(r"^mint://template/(?P<name>[a-zA-Z0-9_-]+)$")
_PRESET_URI_RE = re.compile(r"^mint://preset/(?P<name>[a-zA-Z0-9_-]+)$")

_SUPPORTED_URI_FORMS = "mint://template/<name>, mint://preset/<name>"


def _parse_uri(uri: str) -> tuple[str, str]:
    """Map a mint:// URI to (kind, name). kind is 'template' or 'preset'.

    Raises:
        ResourceUriInvalid: URI matches neither scheme. Message names
            the accepted forms.
    """
    m = _TEMPLATE_URI_RE.match(uri)
    if m:
        return ("template", m.group("name"))
    m = _PRESET_URI_RE.match(uri)
    if m:
        return ("preset", m.group("name"))
    raise ResourceUriInvalid(
        f"RESOURCE_URI_INVALID: {uri!r} does not match any supported scheme. "
        f"Accepted: {_SUPPORTED_URI_FORMS}"
    )


# --------------------------------------------------------------------------- #
# Resource content
# --------------------------------------------------------------------------- #


_YAML_MIME = "application/x-yaml"
_JSON_MIME = "application/json"


def _preset_mime_for_path(path: Path) -> str:
    """JSON presets get application/json, YAML get application/x-yaml.
    Mirrors the dispatch already used by core.style._parse_preset_text."""
    return _JSON_MIME if path.suffix == ".json" else _YAML_MIME


def _template_resource_content(name: str) -> str:
    """Serialize the latest version of `name` as YAML text.

    Versioned siblings authored via update_template are NOT separately
    exposed as resources — the resource scheme exposes one URI per
    template name (latest). Clients that need pinned-version access go
    through the get_template tool with version='1.0' / version='1.1'.

    Raises:
        ResourceNotFound: name not in the registry.
    """
    try:
        schema = get_default_registry().get(name)
    except TemplateNotFound as exc:
        raise ResourceNotFound(
            f"RESOURCE_NOT_FOUND: no template for mint://template/{name}: {exc}"
        ) from exc

    payload: dict[str, Any] = {
        "name": schema.name,
        "version": schema.version,
        "doc_type": schema.doc_type,
        "description": schema.description,
        "required_fields": list(schema.required_fields),
        "layout": [dict(entry) for entry in schema.layout],
    }
    if schema.author:
        payload["_authored_by"] = schema.author
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _preset_resource_content(name: str) -> str:
    """Read the BUILTIN_PRESETS file for `name` and return its raw text.

    Raw text rather than parsed-and-reserialized — preserves the
    canonical $schema URL and any author comments in the file.

    Raises:
        ResourceNotFound: name not in BUILTIN_PRESETS.
    """
    if name not in BUILTIN_PRESETS:
        available = sorted(BUILTIN_PRESETS.keys())
        raise ResourceNotFound(
            f"RESOURCE_NOT_FOUND: no preset for mint://preset/{name}. "
            f"Available: {', '.join(available)}"
        )
    return BUILTIN_PRESETS[name].read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Resource enumeration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResourceDescriptor:
    """One entry in resources/list — uri + the metadata picker UIs need
    to render the choice without a roundtrip to resources/read."""

    uri: str
    name: str
    mime_type: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "uri": self.uri,
            "name": self.name,
            "mimeType": self.mime_type,
            "description": self.description,
        }


def _preset_description(name: str) -> str:
    """Best-effort one-line summary of a preset; reads the file and
    extracts the `description` field if present (both YAML and JSON
    presets use that key per docs/style-preset-schema.md)."""
    path = BUILTIN_PRESETS[name]
    # Defensive against malformed presets; the tested presets all parse
    # successfully so the except branch is unreachable in practice.
    try:
        raw = path.read_text(encoding="utf-8")
        # Both JSON and YAML parse via yaml.safe_load (yaml is a JSON
        # superset). load → dict → grab description.
        parsed = yaml.safe_load(raw)
        if isinstance(parsed, dict):
            return str(parsed.get("description", "")) or f"{name} preset"
    except Exception:  # pragma: no cover
        pass
    return f"{name} preset"  # pragma: no cover — all tested presets carry a description


def _list_mint_resources() -> list[ResourceDescriptor]:
    """One descriptor per (latest) template + one per preset.

    Source of truth that the resource handlers AND the list_presets
    tool both project from. Versioned template siblings collapse into
    a single entry (latest version) so picker UIs aren't spammed —
    the get_template tool is the right surface for explicit version
    enumeration.
    """
    out: list[ResourceDescriptor] = []
    seen_template_names: set[str] = set()
    for summary in get_default_registry().summaries():
        if summary.name in seen_template_names:
            continue
        seen_template_names.add(summary.name)
        # summaries() is sorted by name → ascending semver, but we want
        # the LATEST per name — fetch through get('latest').
        latest = get_default_registry().get(summary.name)
        out.append(
            ResourceDescriptor(
                uri=f"mint://template/{latest.name}",
                name=latest.name,
                mime_type=_YAML_MIME,
                description=latest.description or f"{latest.name} template",
            )
        )
    for preset_name in sorted(BUILTIN_PRESETS):
        path = BUILTIN_PRESETS[preset_name]
        out.append(
            ResourceDescriptor(
                uri=f"mint://preset/{preset_name}",
                name=preset_name,
                mime_type=_preset_mime_for_path(path),
                description=_preset_description(preset_name),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# FastMCP resource handlers (templated URIs)
# --------------------------------------------------------------------------- #


@server.resource(
    "mint://template/{name}",
    mime_type=_YAML_MIME,
    description="MINT document templates (governed YAML; versioned via update_template)",
)
async def template_resource(name: str) -> str:
    """Serve resources/read mint://template/<name>. Returns the latest
    version of the template as YAML text."""
    # START_BLOCK_RESOURCE_READ
    logger.info(
        "[MP-McpResources][read][BLOCK_RESOURCE_READ] kind=template name=%s",
        name,
    )
    # END_BLOCK_RESOURCE_READ
    return _template_resource_content(name)


@server.resource(
    "mint://preset/{name}",
    description="MINT design-token presets (klawd, claret_serif, alga_corporate, minimal, compact)",
)
async def preset_resource(name: str) -> str:
    """Serve resources/read mint://preset/<name>. Returns the preset's
    raw file content (YAML or JSON, whichever the preset uses)."""
    # START_BLOCK_RESOURCE_READ
    logger.info(
        "[MP-McpResources][read][BLOCK_RESOURCE_READ] kind=preset name=%s",
        name,
    )
    # END_BLOCK_RESOURCE_READ
    return _preset_resource_content(name)


# --------------------------------------------------------------------------- #
# Symmetric MCP tools — for clients without resources/list (Claude Desktop)
# --------------------------------------------------------------------------- #


@server.tool(name="mint_list_presets")
async def list_presets(ctx: Context) -> list[dict[str, str]]:
    """Enumerate the design-token presets the server can apply when
    generating a document. Mirrors the resources/list surface for
    clients that don't speak the MCP resources protocol.

    Each entry: uri, name, mimeType, description.
    """
    del ctx
    return [
        descriptor.to_dict()
        for descriptor in _list_mint_resources()
        if descriptor.uri.startswith("mint://preset/")
    ]


@server.tool(name="mint_get_preset")
async def get_preset(name: str, *, ctx: Context) -> dict[str, str]:
    """Return the raw content + mimeType of a preset.

    Mirrors resources/read for clients without the resources protocol.
    """
    del ctx
    if name not in BUILTIN_PRESETS:
        # Same error class as the resource handler so callers across
        # both surfaces see one consistent error code.
        available = sorted(BUILTIN_PRESETS.keys())
        raise ResourceNotFound(
            f"RESOURCE_NOT_FOUND: no preset {name!r}. "
            f"Available: {', '.join(available)}"
        )
    path = BUILTIN_PRESETS[name]
    return {
        "name": name,
        "uri": f"mint://preset/{name}",
        "mimeType": _preset_mime_for_path(path),
        "content": path.read_text(encoding="utf-8"),
    }


__all__ = [
    "MintResourceError",
    "ResourceDescriptor",
    "ResourceNotFound",
    "ResourceUriInvalid",
    "get_preset",
    "list_presets",
    "preset_resource",
    "template_resource",
]
