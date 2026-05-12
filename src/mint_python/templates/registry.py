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
#   LAST_CHANGE: Phase-15 Wave-15-1 — gate TemplateRegistry.update
#     behind MP-AUTH-SHIM.require_template_writer BEFORE any disk I/O.
#     Closes audit Priority-4 sub-clause "Authorization shim: read =
#     open; write = config-gated allowlist". See V-MP-AUTH-SHIM +
#     VF-017 in docs/verification-plan.xml.
#   PRIOR: v0.1.0 — Phase-14 W2 initial implementation per
#     V-MP-TEMPLATES-REGISTRY scenarios 1-5; W3 added update + audit
#     log append.
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastmcp import Context
from fastmcp.server.elicitation import AcceptedElicitation
from mcp.shared.exceptions import McpError

from mint_python.mcp.auth import require_template_writer
from mint_python.mcp.document import _TEMPLATES_DIR, server
from mint_python.mcp.telemetry import track_call

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


class TemplateVersionConflict(TemplateRegistryError):  # noqa: N818 — error code TEMPLATE_VERSION_CONFLICT mirrors class name; suffix omitted intentionally
    """update_template tried to write a version file that already exists.

    The Phase-14 W3 contract is strictly append-only: existing version
    files MUST NOT be overwritten. If two writers race on the same name,
    the second one sees TEMPLATE_VERSION_CONFLICT and is expected to
    re-fetch the registry, recompute the next bump, and retry."""


class TemplateAuthorRequired(TemplateRegistryError):  # noqa: N818 — error code TEMPLATE_AUTHOR_REQUIRED mirrors class name; suffix omitted intentionally
    """update_template fired without an author and the elicit prompt
    was declined / cancelled. Author is required for the audit log;
    we refuse the write rather than silently labelling it 'anonymous'."""


# --------------------------------------------------------------------------- #
# Schema dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TemplateSchema:
    """Validated template content. Generic — replaces the memo-flavored
    DocumentTemplate used by mcp.document for backwards-compat. Both shapes
    coexist during W2; W3+ may consolidate once update_template lands.

    The optional `author` field carries the identity of the model / human
    who wrote a versioned sibling via update_template (W3). Canonical
    `<name>.yaml` files don't usually have it (the original templates
    pre-date the audit trail). When present, it surfaces in the GRACE
    manifest as `template_author=...` so produced docs record the chain
    of who authored which template version.
    """

    name: str
    version: str
    description: str
    doc_type: str
    required_fields: tuple[str, ...]
    layout: tuple[dict[str, Any], ...]
    source_path: Path
    author: str = ""

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
    missing = [key for key in _REQUIRED_KEYS if key not in raw]
    if missing:
        raise TemplateInvalidSchema(
            f"TEMPLATE_INVALID_SCHEMA: {source_path} missing required key(s): "
            + ", ".join(repr(k) for k in missing)
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
    # doc_type defaults to file stem; for versioned siblings strip the
    # _v<semver> suffix so memo_v1.1.yaml reports doc_type='memo'.
    fallback_doc_type = source_path.stem
    versioned = _VERSIONED_FILENAME_RE.match(source_path.name)
    if versioned:
        fallback_doc_type = versioned.group("name")
    return TemplateSchema(
        name=str(name),
        version=str(version),
        description=str(raw.get("description", "")),
        doc_type=str(raw.get("doc_type", fallback_doc_type)),
        required_fields=tuple(required_fields),
        layout=tuple(layout),
        source_path=source_path,
        author=str(raw.get("_authored_by", "")),
    )


# `<name>_v<semver>.yaml` pattern. Versioned siblings live alongside the
# canonical `<name>.yaml` baseline. Semver is restricted to MAJOR.MINOR
# during W3 — patch-level bumps are not exposed yet (they'd dilute the
# audit signal: a template change is structural enough to deserve a
# minor bump every time, even if a writer thinks of it as a "tweak").
_VERSIONED_FILENAME_RE = re.compile(
    r"^(?P<name>[a-zA-Z0-9_-]+?)_v(?P<version>\d+\.\d+)\.yaml$"
)


def _semver_tuple(version: str) -> tuple[int, ...]:
    """Convert "1.10" to (1, 10) for ordered comparison. Larger components
    sort higher than smaller — "1.10" > "1.9" > "1.0" > "0.5"."""
    return tuple(int(part) for part in version.split("."))


def _bump_minor(version: str) -> str:
    """Minor bump — 1.0 → 1.1, 1.9 → 1.10, 2.0 → 2.1. Plain string-
    integer arithmetic; the W3 versioning model is intentionally
    minimal (MAJOR.MINOR only)."""
    parts = version.split(".")
    # The registry only feeds versions that already passed _validate's
    # type check, so this defensive guard is unreachable in practice.
    if len(parts) != 2 or not all(p.isdigit() for p in parts):  # pragma: no cover
        raise ValueError(f"unexpected version shape {version!r}")
    major, minor = int(parts[0]), int(parts[1])
    return f"{major}.{minor + 1}"


@dataclass
class TemplateRegistry:
    """Discover + validate + serve templates from a directory.

    Construction is the heavy work — every YAML file is parsed and
    validated up-front. Subsequent list/get queries are O(1) lookups.
    Tests construct registries over tmp_path; the @server.tool wrappers
    use a lazy module-level singleton over the shared `_TEMPLATES_DIR`.

    W3 (MP-TEMPLATES-WRITE) extends the data model with multiple
    versions per name: a canonical `<name>.yaml` baseline plus zero-or-
    more `<name>_v<semver>.yaml` siblings. The map below is keyed by
    name, valued as a list of TemplateSchema sorted ascending by semver
    — `_versions_by_name['memo'][-1]` is always the latest.
    """

    templates_dir: Path
    _versions_by_name: dict[str, list[TemplateSchema]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load()

    def _load(self) -> None:
        if not self.templates_dir.exists():  # pragma: no cover — repo layout invariant
            self._versions_by_name = {}
            return
        by_name_version: dict[tuple[str, str], TemplateSchema] = {}
        for path in sorted(self.templates_dir.glob("*.yaml")):
            # _audit.jsonl is jsonl not yaml; this skip is defensive
            # against future sidecar files that adopt the .yaml suffix.
            if path.name.startswith("_"):  # pragma: no cover
                continue
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            schema = _validate(raw, path)
            # If filename encodes a version (memo_v1.1.yaml), it MUST
            # match the version field inside the YAML — otherwise renaming
            # a file lies about its lineage. Hard-fail at load.
            versioned = _VERSIONED_FILENAME_RE.match(path.name)
            if versioned and versioned.group("version") != schema.version:
                raise TemplateInvalidSchema(
                    f"TEMPLATE_INVALID_SCHEMA: {path} filename version "
                    f"{versioned.group('version')!r} does not match "
                    f"yaml 'version' field {schema.version!r}"
                )
            key = (schema.name, schema.version)
            if key in by_name_version:
                raise TemplateInvalidSchema(
                    f"TEMPLATE_INVALID_SCHEMA: duplicate (name, version) "
                    f"{key!r} from {path} and {by_name_version[key].source_path}"
                )
            by_name_version[key] = schema

        grouped: dict[str, list[TemplateSchema]] = {}
        for (name, _v), schema in by_name_version.items():
            grouped.setdefault(name, []).append(schema)
        for schemas in grouped.values():
            schemas.sort(key=lambda s: _semver_tuple(s.version))
        self._versions_by_name = grouped

        total_versions = sum(len(v) for v in grouped.values())
        # START_BLOCK_LOAD_REGISTRY
        logger.info(
            "[MP-Templates][load][BLOCK_LOAD_REGISTRY] "
            "templates_count=%d doc_types=%s total_versions=%d",
            len(grouped),
            sorted({s.doc_type for ss in grouped.values() for s in ss}),
            total_versions,
        )
        # END_BLOCK_LOAD_REGISTRY

    def summaries(self) -> list[TemplateSummary]:
        """One TemplateSummary per (name, version) tuple — versioned
        siblings each surface separately so picker UIs can show full
        lineage. Order: by name, then by ascending semver.

        (Named `summaries` rather than `list` because the latter shadows
        the builtin `list` type inside the class body, which trips up
        mypy's `list[T]` annotation resolution. The MCP tool wrapper
        `list_templates` is the user-facing surface name.)"""
        out: list[TemplateSummary] = []
        for name in sorted(self._versions_by_name):
            for schema in self._versions_by_name[name]:
                out.append(
                    TemplateSummary(
                        name=schema.name,
                        version=schema.version,
                        doc_type=schema.doc_type,
                        description=schema.description,
                        last_modified=schema.last_modified,
                    )
                )
        return out

    def versions(self, name: str) -> list[str]:
        """All known versions of `name` in ascending semver order. Empty
        list if name is unknown — callers that want a hard fail use get."""
        return [s.version for s in self._versions_by_name.get(name, [])]

    def get(self, name: str, version: str = "latest") -> TemplateSchema:
        """Return the parsed schema for `name`.

        version='latest' (default) resolves to the highest known semver.
        Any other value resolves by exact match against the YAML's
        version field (e.g. version='1.0' returns memo at 1.0 even if
        memo_v1.1 exists).

        Raises:
            TemplateNotFound: name not in the registry, or the requested
                version does not match any registered schema for that
                name. Message lists the alternatives.
        """
        if name not in self._versions_by_name:
            available = sorted(self._versions_by_name.keys())
            raise TemplateNotFound(
                f"TEMPLATE_NOT_FOUND: no template named {name!r}. "
                f"Available: {', '.join(available) if available else '(none)'}"
            )
        schemas = self._versions_by_name[name]
        if version == "latest":
            return schemas[-1]
        for schema in schemas:
            if schema.version == version:
                return schema
        known_versions = ", ".join(s.version for s in schemas)
        raise TemplateNotFound(
            f"TEMPLATE_NOT_FOUND: template {name!r} has no version "
            f"{version!r}. Available: {known_versions}"
        )

    def update(self, name: str, content: str, author: str) -> dict[str, Any]:
        """Author a new versioned sibling for an existing template.

        Validates `content` (must be a valid TemplateSchema YAML),
        computes the next minor bump from the current latest version,
        writes `<name>_v<bumped>.yaml`, appends an entry to
        `_audit.jsonl`, refreshes the in-memory registry. NEVER
        overwrites an existing version file — surfaces conflicts as
        TemplateVersionConflict so a concurrent writer can retry.

        Args:
            name: existing template name (must already be registered).
                W3 doesn't ship a "create new template from scratch"
                path; that's a write surface for a later phase.
            content: full YAML body of the new template version. The
                `version` field inside the YAML is overridden by the
                computed bump (filename stays authoritative for the
                version slot — avoids bumps that disagree with their
                contents).
            author: identity to record in the audit log + GRACE
                manifest. Required; an empty string raises
                TemplateAuthorRequired (callers that didn't collect
                an author up-front go through update_template's
                ctx.elicit fallback before reaching this method).

        Returns:
            dict with keys {name, version, audit_id, predecessor_version,
            written_to}.

        Raises:
            TemplateNotFound: name has no current version to bump from.
            TemplateInvalidSchema: content doesn't parse as a template.
            TemplateVersionConflict: target file already exists on disk.
            TemplateAuthorRequired: author is empty.
        """
        if not author:
            raise TemplateAuthorRequired(
                "TEMPLATE_AUTHOR_REQUIRED: update_template needs a non-empty author"
            )
        # Phase-15 Wave-15-1 / VF-017: gate write-path behind
        # MP-AUTH-SHIM allowlist BEFORE any disk I/O — destructive check
        # at the top, before semver computation, before audit-log
        # append, before any file open. Mirrors V-MP-FIX forbidden-2
        # fix-pattern. Open mode (no env, no file) admits silently;
        # the once-per-process BLOCK_AUTH_OPEN_MODE warning is the
        # operator signal there.
        require_template_writer(author)
        # Predecessor — must exist; we're bumping FROM something.
        predecessor = self.get(name)
        next_version = _bump_minor(predecessor.version)

        target_path = self.templates_dir / f"{name}_v{next_version}.yaml"
        if target_path.exists():
            raise TemplateVersionConflict(
                f"TEMPLATE_VERSION_CONFLICT: {target_path} already exists; "
                f"refusing to overwrite (predecessor was {predecessor.version})"
            )

        # Validate the proposed content. We override the version field
        # to the bumped value before serialization so the on-disk YAML's
        # `version` matches the filename — drift between the two was
        # the failure mode forbidden-1 protects against.
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict):
            raise TemplateInvalidSchema(
                f"TEMPLATE_INVALID_SCHEMA: update_template content for {name!r} "
                f"did not parse to a mapping"
            )
        raw["version"] = next_version
        raw["_authored_by"] = author
        # Validate via the same path the registry uses on load — guarantees
        # parity with discovery rules.
        _validate(raw, target_path)

        target_path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        audit_id = str(uuid.uuid4())
        content_sha256 = hashlib.sha256(target_path.read_bytes()).hexdigest()
        audit_entry = {
            "audit_id": audit_id,
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "name": name,
            "version": next_version,
            "predecessor_version": predecessor.version,
            "author": author,
            "content_sha256": content_sha256,
            "written_to": str(target_path),
        }
        audit_log_path = self.templates_dir / "_audit.jsonl"
        # Append-only: 'a' mode + a newline-terminated JSON object per
        # line. forbidden-2: never rewrite previous entries.
        with audit_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")

        # START_BLOCK_UPDATE_TEMPLATE
        logger.info(
            "[MP-TemplatesWrite][update][BLOCK_UPDATE_TEMPLATE] "
            "name=%s version=%s predecessor_version=%s author=%s audit_id=%s",
            name,
            next_version,
            predecessor.version,
            author,
            audit_id,
        )
        # END_BLOCK_UPDATE_TEMPLATE

        # Refresh in-memory registry so subsequent get() calls see the
        # new version without a server restart.
        self._load()

        return {
            "name": name,
            "version": next_version,
            "audit_id": audit_id,
            "predecessor_version": predecessor.version,
            "written_to": str(target_path),
        }


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


@server.tool(name="mint_list_templates")
async def list_templates(ctx: Context) -> list[dict[str, str]]:
    """Enumerate the templates the server can author documents from.

    Each entry is a dict with name, version, doc_type, description, and
    last_modified (ISO-8601). Connected clients use this to populate a
    picker before invoking create_document.
    """
    del ctx  # unused; the registry is read-only and stateless per tool call
    with track_call("mint_list_templates"):
        return [summary.to_dict() for summary in get_default_registry().summaries()]


@server.tool(name="mint_update_template")
async def update_template(
    name: str,
    content: str,
    *,
    ctx: Context,
    author: str = "",
) -> dict[str, Any]:
    """Author a new versioned sibling for an existing template.

    The connected model passes the new YAML in `content`; the registry
    bumps the minor version, writes `templates/<name>_v<bumped>.yaml`,
    and appends an audit entry to `templates/_audit.jsonl`. The
    GRACE manifest of any document subsequently produced from this new
    version names the template_author in its lineage record (Phase-14
    W3 closes audit's Priority-4).

    The `content` YAML must include ALL of these required keys:
      - name (str): template display name
      - doc_type (str): document type identifier
      - description (str): human-readable description
      - required_fields (list[str]): field names the pipeline will elicit
      - layout (list[dict]): block declarations (heading/paragraph/table/
        callout/spacer)

    If `author` is empty, falls back to ctx.elicit asking the user for
    an identity to record. This is a single-prompt elicit; declining
    raises TemplateAuthorRequired (we refuse to write an unsigned
    template — the audit signal is the whole point of versioning).
    Clients without elicitation support (Claude Desktop today) get a
    clear -32601-shaped error and can retry the call with author
    supplied inline.
    """
    with track_call("mint_update_template", doc_type=name):
        if not author:
            try:
                result = await ctx.elicit(
                    message="Who should be recorded as the template author?",
                    response_type=str,  # type: ignore[arg-type]
                    response_title="author",
                )
            except McpError:
                raise TemplateAuthorRequired(
                    "TEMPLATE_AUTHOR_REQUIRED: update_template called without "
                    "author and the MCP client does not support elicitation. "
                    "Retry with author= explicitly populated."
                ) from None
            if not isinstance(result, AcceptedElicitation):
                raise TemplateAuthorRequired(
                    f"TEMPLATE_AUTHOR_REQUIRED: author elicit "
                    f"{getattr(result, 'action', 'declined')!r} for template {name!r}"
                )
            author = str(result.data)

        registry = get_default_registry()
        outcome = registry.update(name=name, content=content, author=author)
        reset_default_registry()
        return outcome


@server.tool(name="mint_get_template")
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
    with track_call("mint_get_template", doc_type=name):
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
    "TemplateAuthorRequired",
    "TemplateInvalidSchema",
    "TemplateNotFound",
    "TemplateRegistry",
    "TemplateRegistryError",
    "TemplateSchema",
    "TemplateSummary",
    "TemplateVersionConflict",
    "get_default_registry",
    "get_template",
    "list_templates",
    "reset_default_registry",
    "update_template",
]
