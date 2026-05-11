# FILE: src/mint_python/mcp/resources.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-14 W4 (MP-MCP-RESOURCES) — wire FastMCP @server
#     .resource handlers exposing `mint://template/<name>` and
#     `mint://preset/<name>` URI schemes, and symmetric @server.tool
#     wrappers `list_presets` / `get_preset` for clients that don't
#     speak the MCP resources protocol (Claude Desktop today). Closes
#     audit's Priority-1 — design tokens + governed templates as
#     discoverable MCP resources.
#
#     Phase-17 W17-2 (MP-MCP-RESOURCES-VERSIONED) — chain the 3 preset
#     handlers (resource read + list_presets + get_preset) to versioned
#     preset YAML files written by mint_update_preset_palette/
#     typography/spacing. Resolution + version-listing live in
#     mint_python.mcp.preset_edit (single source of truth for "what is
#     the latest version"): resources.py imports the W17-0-public
#     `resolve_latest_preset_path` + `collect_preset_versions` and MUST
#     NOT duplicate semver / regex logic (V-MP-MCP-RESOURCES-VERSIONED
#     forbidden-4, mechanically grep-checked).
#   SCOPE: Public surface = templated resource handlers (registered on
#     the shared mcp.document.server), list_presets / get_preset MCP
#     tools, ResourceNotFound / ResourceUriInvalid errors, helpers for
#     enumeration + parsing that tests exercise directly.
#   DEPENDS: mint_python.mcp.document (shared FastMCP server),
#     mint_python.templates.registry (template lookup),
#     mint_python.core.style (BUILTIN_PRESETS map),
#     mint_python.mcp.preset_edit (PRESETS_DIR + PresetNotFound +
#     resolve_latest_preset_path + collect_preset_versions — W17-0
#     public surface; the read-path's single source of truth for
#     versioned preset resolution).
#   LINKS: docs/development-plan.xml#MP-MCP-RESOURCES,
#     docs/development-plan.xml#MP-MCP-RESOURCES-VERSIONED,
#     docs/verification-plan.xml#V-MP-MCP-RESOURCES,
#     docs/verification-plan.xml#V-MP-MCP-RESOURCES-VERSIONED,
#     docs/knowledge-graph.xml#MP-MCP-RESOURCES,
#     docs/knowledge-graph.xml#MP-MCP-RESOURCES-VERSIONED
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
#   _preset_resource_content   - read the LATEST resolved preset file
#                           (versioned sibling OR BUILTIN_PRESETS
#                           fallback) and return its raw text. Path
#                           resolution delegates to
#                           preset_edit.resolve_latest_preset_path; we
#                           never duplicate semver logic locally
#                           (forbidden-4).
#   _resolve_preset_for_read   - thin wrapper around
#                           resolve_latest_preset_path that surfaces
#                           PresetNotFound as the existing
#                           ResourceNotFound error and emits the
#                           BLOCK_RESOLVE_VERSIONED log marker per call.
#                           Also enforces forbidden-7 (resolved path
#                           must be inside PRESETS_DIR OR equal to
#                           BUILTIN_PRESETS[name] — no symlink escape).
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
#   LAST_CHANGE: v0.2.0 — Phase-17 W17-2 (MP-MCP-RESOURCES-VERSIONED):
#     _preset_resource_content + list_presets + get_preset now call
#     preset_edit.resolve_latest_preset_path (W17-0 public) instead of
#     reading BUILTIN_PRESETS[name] directly, so external MCP clients
#     see latest versioned content after every preset edit. list_presets
#     summary gains `latest_version` + `predecessor_versions` keys when
#     versioned siblings exist (additive only; no-sibling shape stays
#     byte-identical to v0.1.0 per scenario-12). New BLOCK_RESOLVE_
#     VERSIONED log marker per resolve call. Semver / regex logic
#     remains exclusively in preset_edit.py (forbidden-4, mechanically
#     grep-checked).
#   v0.1.0 — Phase-14 W4 initial implementation per V-MP-MCP-RESOURCES
#     scenarios 1-5 + forbidden-1.
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

# NOTE: `mint_python.mcp.preset_edit` is imported lazily inside the helper
# functions below to break a circular import — preset_edit imports
# `mcp.document`, which transitively imports this module at register-time
# to wire the @server.resource handlers. The lazy import means we pay one
# attribute lookup per resolve call (microseconds; resolution is already
# I/O-bound on the PRESETS_DIR.glob). See V-MP-MCP-RESOURCES-VERSIONED
# forbidden-4 — duplicating semver logic is FAR worse than this indirection.

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


def _resolve_preset_for_read(name: str) -> Path:
    """Return the on-disk path of the latest preset version for `name`.

    Thin wrapper around `preset_edit.resolve_latest_preset_path` that:
      - re-raises `PresetNotFound` as the existing `ResourceNotFound`
        contract surface so the resource handlers + tools see ONE error
        code regardless of where it originated;
      - enforces forbidden-7 (the resolved path MUST equal
        BUILTIN_PRESETS[name] or be a direct child of
        preset_edit.PRESETS_DIR — no symlink escape, no path traversal);
      - emits the BLOCK_RESOLVE_VERSIONED log marker per resolve call so
        trace analysis can correlate every external preset read to a
        concrete file + predecessor count.

    Semver / regex logic stays exclusively in preset_edit.py
    (V-MP-MCP-RESOURCES-VERSIONED forbidden-4). Do NOT inline a second
    versioned-filename regex or semver-tuple helper here — see the
    forbidden-4 grep gate (mechanically enforced); duplication is the
    silent-1.10-vs-1.2 regression hazard the gate was added to catch.
    """
    # Local import deferred to call time — (1) breaks the circular
    # import preset_edit → mcp.document → resources, and (2) ensures
    # tests that monkeypatch `mint_python.mcp.preset_edit.PRESETS_DIR`
    # see the patched value via the live module attribute.
    from mint_python.mcp import preset_edit as _preset_edit

    try:
        resolved = _preset_edit.resolve_latest_preset_path(name)
    except _preset_edit.PresetNotFound as exc:
        available = sorted(BUILTIN_PRESETS.keys())
        raise ResourceNotFound(
            f"RESOURCE_NOT_FOUND: no preset for mint://preset/{name}. "
            f"Available: {', '.join(available)}"
        ) from exc

    # forbidden-7: resolved path must satisfy
    #   path == BUILTIN_PRESETS[name]  OR  path.parent == PRESETS_DIR.
    # Guards against a malicious `klawd_v1.1.yaml` symlink pointing
    # outside the presets dir; both legs are read-only by contract.
    builtin_path = BUILTIN_PRESETS.get(name)
    if not (resolved == builtin_path or resolved.parent == _preset_edit.PRESETS_DIR):
        # Defensive — should be unreachable because resolve_latest_
        # preset_path only returns paths from PRESETS_DIR.glob OR
        # BUILTIN_PRESETS[name]. If a future refactor breaks that
        # invariant we want a loud failure here, not silent leak.
        raise ResourceNotFound(  # pragma: no cover
            f"RESOURCE_NOT_FOUND: resolved path for {name!r} escapes "
            f"the preset sandbox (got {resolved!r}); refusing to read."
        )

    # Compute predecessor count for the log marker. Cheap (1 extra
    # filesystem scan via collect_preset_versions — same dir, same
    # glob). Kept here rather than in collect_preset_versions's caller
    # so the marker is consistent across every consumer.
    try:
        versions = _preset_edit.collect_preset_versions(name)
        predecessor_count = max(len(versions) - 1, 0)
    except _preset_edit.PresetNotFound:  # pragma: no cover — resolve_latest succeeded above
        predecessor_count = 0

    # START_BLOCK_RESOLVE_VERSIONED
    logger.info(
        "[MP-Resources][preset][BLOCK_RESOLVE_VERSIONED] "
        "name=%s resolved=%s predecessors=%d",
        name,
        resolved.name,
        predecessor_count,
    )
    # END_BLOCK_RESOLVE_VERSIONED
    return resolved


def _preset_resource_content(name: str) -> str:
    """Read the latest preset file for `name` and return its raw text.

    Resolution order (handled by `_resolve_preset_for_read` →
    `preset_edit.resolve_latest_preset_path`):
      1. Highest-semver `<name>_v<X.Y>.yaml` in
         `preset_edit.PRESETS_DIR` (versioned sibling, written by
         mint_update_preset_* tools).
      2. `BUILTIN_PRESETS[name]` baseline (untouched preset).
      3. Raise `ResourceNotFound`.

    Raw text rather than parsed-and-reserialized — preserves the
    canonical $schema URL and any author comments in the file. The
    new VERSIONED-aware read closes the Phase-16 W2 gap where
    external MCP clients (Cursor, OpenWebUI, Claude Desktop) saw
    only the built-in baseline even after a successful preset edit.

    Raises:
        ResourceNotFound: name not in BUILTIN_PRESETS AND no
            versioned siblings in PRESETS_DIR.
    """
    resolved = _resolve_preset_for_read(name)
    return resolved.read_text(encoding="utf-8")


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
    presets use that key per docs/style-preset-schema.md).

    Reads the BUILTIN_PRESETS baseline rather than the latest versioned
    sibling — the description tracks the canonical preset identity, not
    individual edits. Keeps list_presets output stable across the edit
    chain (predecessor_versions surfaces edit history separately).
    """
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
async def list_presets(ctx: Context) -> list[dict[str, Any]]:
    """Enumerate the design-token presets the server can apply when
    generating a document. Mirrors the resources/list surface for
    clients that don't speak the MCP resources protocol.

    Base entry keys (always present): uri, name, mimeType, description.
    Versioned-aware additive keys (present only when at least one
    versioned sibling exists alongside the BUILTIN baseline):
      - latest_version (str): semver string of the highest version on
        disk for this canonical name.
      - predecessor_versions (list[str]): every version ASCENDING up to
        but excluding the latest. Empty for a fresh edit (one sibling)
        because the full chain = ['1.0', '1.1'] and predecessors = ['1.0'].

    Backwards-compat invariant (V-MP-MCP-RESOURCES-VERSIONED
    scenario-12): when NO versioned siblings exist for `name`, the
    returned dict's key set is byte-identical to the Phase-14 v0.1.0
    shape — additive fields are omitted entirely, not None-valued, so
    `set(entry) == {"uri", "name", "mimeType", "description"}` still
    holds for the Phase-14 existing-tests path.

    Returns ONE entry per BUILTIN canonical name (5 entries:
    klawd, claret_serif, alga_corporate, minimal, compact) regardless
    of how many versioned siblings exist (forbidden-5 / scenario-14).
    """
    del ctx
    # Lazy import — see module-level note about circular import.
    from mint_python.mcp import preset_edit as _preset_edit

    entries: list[dict[str, Any]] = []
    for descriptor in _list_mint_resources():
        if not descriptor.uri.startswith("mint://preset/"):
            continue
        entry: dict[str, Any] = descriptor.to_dict()
        # Resolve the version chain. collect_preset_versions raises
        # PresetNotFound only when neither BUILTIN nor versioned
        # siblings exist — which can't happen here because we iterate
        # over the BUILTIN_PRESETS-derived descriptor list.
        # descriptor list comes from BUILTIN_PRESETS so the
        # PresetNotFound branch below is unreachable in practice; the
        # try/except is defensive against a future shift in the
        # enumerator's source-of-truth.
        try:
            versions = _preset_edit.collect_preset_versions(descriptor.name)
        except _preset_edit.PresetNotFound:  # pragma: no cover
            versions = []
        # Additive ONLY when versioned siblings exist on top of the
        # base (len > 1). Single-baseline presets keep the 4-key shape
        # so existing Phase-14 callers / tests don't see a diff.
        if len(versions) > 1:
            entry["latest_version"] = versions[-1]
            entry["predecessor_versions"] = versions[:-1]
        entries.append(entry)
    return entries


@server.tool(name="mint_get_preset")
async def get_preset(name: str, *, ctx: Context) -> dict[str, str]:
    """Return the raw content + mimeType of a preset.

    Mirrors resources/read for clients without the resources protocol.
    Chains to the latest versioned sibling via
    `_resolve_preset_for_read` so external MCP clients always see the
    most recent preset edit (Phase-17 W17-2 fix).
    """
    del ctx
    resolved = _resolve_preset_for_read(name)
    return {
        "name": name,
        "uri": f"mint://preset/{name}",
        "mimeType": _preset_mime_for_path(resolved),
        "content": resolved.read_text(encoding="utf-8"),
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
