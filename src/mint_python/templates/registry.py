# FILE: src/mint_python/templates/registry.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Read-only registry over templates/. Walks the directory at
#     init, parses each YAML file, validates against TemplateSchema (fail-
#     fast — malformed file raises TemplateInvalidSchema at load, not on
#     first create_document call), and exposes list_templates /
#     get_template MCP tools so connected clients can discover what
#     doc_types are available without having to know the filesystem
#     layout. Phase-14 W2 (MP-TEMPLATES-REGISTRY) — first half of the
#     mcp-audit Priority-2 (templates as discoverable resources). The
#     write side (update_template + audit log + GRACE lineage) lands in
#     W3 (MP-TEMPLATES-WRITE).
#   SCOPE: Public surface = TemplateRegistry class, TemplateSchema /
#     TemplateSummary dataclasses, TemplateNotFound /
#     TemplateInvalidSchema errors, list_templates / get_template
#     FastMCP tools registered on the shared mcp.document.server.
#   DEPENDS: pyyaml, mint_python.mcp.document (shared FastMCP server +
#     _TEMPLATES_DIR base path).
#   LINKS: docs/development-plan.xml#MP-TEMPLATES-REGISTRY,
#     docs/verification-plan.xml#V-MP-TEMPLATES-REGISTRY,
#     docs/knowledge-graph.xml#MP-TEMPLATES-REGISTRY
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TemplateSchema           - dataclass for parsed + validated template
#   TemplateSummary          - lightweight (name, version, doc_type,
#                              description, last_modified) tuple for
#                              list_templates output
#   TemplateRegistry         - load + query class; one instance per
#                              templates_dir
#   TemplateNotFound         - get_template against an unknown name
#   TemplateInvalidSchema    - YAML present but missing required keys
#                              or wrong types
#   list_templates           - @server.tool async; returns list of
#                              TemplateSummary as plain dicts
#   get_template             - @server.tool async; returns full parsed
#                              YAML dict for name + version
#   _default_registry        - lazily-built singleton over the shared
#                              templates dir; reset_default_registry
#                              clears it so tests can rebuild over
#                              tmp_path or after templates change.
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-14 W2 initial implementation per
#     V-MP-TEMPLATES-REGISTRY scenarios 1-5.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastmcp import Context

from mint_python.mcp.document import _TEMPLATES_DIR, server

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class TemplateRegistryError(Exception):
    """Base for registry errors."""


class TemplateNotFound(TemplateRegistryError):  # noqa: N818 — error code TEMPLATE_NOT_FOUND mirrors class name; suffix omitted intentionally
    """get_template called with a name not in the registry."""


class TemplateInvalidSchema(TemplateRegistryError):  # noqa: N818 — error code TEMPLATE_INVALID_SCHEMA mirrors class name; suffix omitted intentionally
    """A YAML file in templates/ is missing required keys or has wrong types.

    Surfaced at registry-load time (fail-fast) rather than on the first
    create_document call, so a misconfigured template is caught at server
    startup instead of partway through a user dialog."""


# --------------------------------------------------------------------------- #
# Schema dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TemplateSchema:
    """Validated template content. Generic — replaces the memo-flavored
    DocumentTemplate used by mcp.document for backwards-compat. Both shapes
    coexist during W2; W3+ may consolidate once update_template lands."""

    name: str
    version: str
    description: str
    doc_type: str
    required_fields: tuple[str, ...]
    layout: tuple[dict[str, Any], ...]
    source_path: Path

    @property
    def last_modified(self) -> str:
        """ISO-8601 mtime of the source YAML — surfaces in TemplateSummary
        so list_templates consumers can detect template churn without an
        extra get_template round-trip."""
        ts = datetime.fromtimestamp(self.source_path.stat().st_mtime, tz=UTC)
        return ts.isoformat()


@dataclass(frozen=True)
class TemplateSummary:
    """Lightweight projection of TemplateSchema for list_templates results.

    Excludes the layout payload (often hundreds of lines) so clients can
    paint a picker UI without paying the bandwidth of every layout
    description. Clients that want the full template fetch via
    get_template by name."""

    name: str
    version: str
    doc_type: str
    description: str
    last_modified: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "doc_type": self.doc_type,
            "description": self.description,
            "last_modified": self.last_modified,
        }


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


_REQUIRED_KEYS = ("name", "version", "required_fields", "layout")


def _validate(raw: Any, source_path: Path) -> TemplateSchema:
    """Coerce a parsed YAML dict into a validated TemplateSchema.

    Required: name (str), version (str), required_fields (list[str]),
    layout (list[dict]). Optional: description (str, default ""), doc_type
    (str, default = file stem — matches the create_document(doc_type=...)
    contract). Anything else raises TemplateInvalidSchema with a message
    naming the path + the failure mode so the human author knows where
    to look."""
    if not isinstance(raw, dict):
        raise TemplateInvalidSchema(
            f"TEMPLATE_INVALID_SCHEMA: {source_path} did not parse to a mapping "
            f"(got {type(raw).__name__})"
        )
    for key in _REQUIRED_KEYS:
        if key not in raw:
            raise TemplateInvalidSchema(
                f"TEMPLATE_INVALID_SCHEMA: {source_path} missing required key {key!r}"
            )
    name = raw["name"]
    version = raw["version"]
    required_fields = raw["required_fields"]
    layout = raw["layout"]
    if not isinstance(name, str) or not isinstance(version, str):
        raise TemplateInvalidSchema(
            f"TEMPLATE_INVALID_SCHEMA: {source_path} 'name' and 'version' must be strings"
        )
    if not isinstance(required_fields, list) or not all(
        isinstance(item, str) for item in required_fields
    ):
        raise TemplateInvalidSchema(
            f"TEMPLATE_INVALID_SCHEMA: {source_path} 'required_fields' must be a list of strings"
        )
    if not isinstance(layout, list) or not all(isinstance(item, dict) for item in layout):
        raise TemplateInvalidSchema(
            f"TEMPLATE_INVALID_SCHEMA: {source_path} 'layout' must be a list of mappings"
        )
    return TemplateSchema(
        name=str(name),
        version=str(version),
        description=str(raw.get("description", "")),
        doc_type=str(raw.get("doc_type", source_path.stem)),
        required_fields=tuple(required_fields),
        layout=tuple(layout),
        source_path=source_path,
    )


@dataclass
class TemplateRegistry:
    """Discover + validate + serve templates from a directory.

    Construction is the heavy work — every YAML file is parsed and
    validated up-front. Subsequent list/get queries are O(1) lookups.
    Tests construct registries over tmp_path; the @server.tool wrappers
    use a lazy module-level singleton over the shared `_TEMPLATES_DIR`.

    Versioned siblings (`<name>_v<semver>.yaml`) land in W3
    (MP-TEMPLATES-WRITE). For W2 the layout is one file per doc_type,
    so name and doc_type collapse to the same string.
    """

    templates_dir: Path
    _templates: dict[str, TemplateSchema] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load()

    def _load(self) -> None:
        if not self.templates_dir.exists():  # pragma: no cover — repo layout invariant
            self._templates = {}
            return
        loaded: dict[str, TemplateSchema] = {}
        for path in sorted(self.templates_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            schema = _validate(raw, path)
            loaded[schema.name] = schema
        self._templates = loaded

        # START_BLOCK_LOAD_REGISTRY
        logger.info(
            "[MP-Templates][load][BLOCK_LOAD_REGISTRY] "
            "templates_count=%d doc_types=%s total_versions=%d",
            len(loaded),
            sorted(s.doc_type for s in loaded.values()),
            # W3 splits versions across siblings; until then total_versions
            # equals templates_count — surfacing the field now keeps the
            # log-payload contract stable across phases.
            len(loaded),
        )
        # END_BLOCK_LOAD_REGISTRY

    def list(self) -> list[TemplateSummary]:
        return [
            TemplateSummary(
                name=schema.name,
                version=schema.version,
                doc_type=schema.doc_type,
                description=schema.description,
                last_modified=schema.last_modified,
            )
            for schema in self._templates.values()
        ]

    def get(self, name: str, version: str = "latest") -> TemplateSchema:
        """Return the parsed schema for `name`. version='latest' is the
        only path implemented in W2 (one file per name); explicit version
        pinning lands in W3 alongside semver-bumped siblings.

        Raises:
            TemplateNotFound: name not in the registry. Message lists the
                names that ARE registered so the connected model can
                retry with a valid one.
        """
        del version  # W3 wires this up; reserved for explicit version pinning
        if name not in self._templates:
            available = sorted(self._templates.keys())
            raise TemplateNotFound(
                f"TEMPLATE_NOT_FOUND: no template named {name!r}. "
                f"Available: {', '.join(available) if available else '(none)'}"
            )
        return self._templates[name]


# --------------------------------------------------------------------------- #
# Default registry — lazy module-level singleton
# --------------------------------------------------------------------------- #


_default_registry: TemplateRegistry | None = None


def get_default_registry() -> TemplateRegistry:
    """Lazy singleton over the shared `_TEMPLATES_DIR`. Built on first call
    so registry load fires deterministically at first MCP tool invocation
    rather than at module import time (which would couple test
    parametrization to import order)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = TemplateRegistry(templates_dir=_TEMPLATES_DIR)
    return _default_registry


def reset_default_registry() -> None:
    """Force the next get_default_registry() to rebuild. Tests use this
    after monkeypatching _TEMPLATES_DIR or after writing fixture
    templates so the @server.tool wrappers see the updated state."""
    global _default_registry
    _default_registry = None


# --------------------------------------------------------------------------- #
# MCP tools
# --------------------------------------------------------------------------- #


@server.tool
async def list_templates(ctx: Context) -> list[dict[str, str]]:
    """Enumerate the templates the server can author documents from.

    Each entry is a dict with name, version, doc_type, description, and
    last_modified (ISO-8601). Connected clients use this to populate a
    picker before invoking create_document.
    """
    del ctx  # unused; the registry is read-only and stateless per tool call
    return [summary.to_dict() for summary in get_default_registry().list()]


@server.tool
async def get_template(
    name: str,
    *,
    ctx: Context,
    version: str = "latest",
) -> dict[str, Any]:
    """Return the full parsed YAML content of a template.

    Useful when a client wants to inspect a template's layout (for
    preview UI) or required_fields list (to pre-fill known values into
    create_document's intent argument). version='latest' is the only
    pinning supported in W2; explicit semver pinning ships with W3.
    """
    del ctx  # unused; reads stable registry state
    schema = get_default_registry().get(name, version=version)
    return {
        "name": schema.name,
        "version": schema.version,
        "doc_type": schema.doc_type,
        "description": schema.description,
        "required_fields": list(schema.required_fields),
        "layout": [dict(entry) for entry in schema.layout],
    }


__all__ = [
    "TemplateInvalidSchema",
    "TemplateNotFound",
    "TemplateRegistry",
    "TemplateRegistryError",
    "TemplateSchema",
    "TemplateSummary",
    "get_default_registry",
    "get_template",
    "list_templates",
    "reset_default_registry",
]
