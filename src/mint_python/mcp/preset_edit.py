# FILE: src/mint_python/mcp/preset_edit.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-2 (MP-THEME-EDIT) — ship 3 structured-patch
#     MCP tools so connected models can edit a design preset's palette,
#     typography, and spacing without composing valid YAML by hand.
#     Themes are sub-noun of templates per Phase-16 framing: the write
#     path reuses MINT_TEMPLATE_WRITERS through MP-AUTH-SHIM
#     `require_template_writer` (VF-022 inv-2 WRITERS-ALLOWLIST-REUSED;
#     NO separate MINT_PRESET_WRITERS env). Each successful call emits a
#     NEW versioned YAML file at presets/<name>_v<next>.yaml + appends a
#     JSONL audit entry to presets/_audit.jsonl. The merge surface is a
#     small, opinionated set of knobs (5 palette keys, 5 typography
#     knobs, 4 spacing knobs) that fan out into the full preset shape
#     consumed by MP-STYLE.load_preset.
#   SCOPE: Public surface = `mint_update_preset_palette`,
#     `mint_update_preset_typography`, `mint_update_preset_spacing`
#     (FastMCP tools), `PresetNotFound` / `InvalidPatch` /
#     `PresetVersionConflict` structured ToolErrors, `PRESETS_DIR`
#     module-level directory (defaults to mint_python.core.presets but
#     monkeypatchable in tests), `CANONICAL_RESULT_KEYS`,
#     `PALETTE_KEYS`, `TYPOGRAPHY_KEYS`, `SPACING_KEYS`.
#   DEPENDS: fastmcp (Context + ToolError), pyyaml, mint_python.mcp.auth
#     (`require_template_writer`, `TemplateWriteForbidden` — read but
#     never modified by this module), mint_python.mcp.document (shared
#     `server` instance), mint_python.core.style (`BUILTIN_PRESETS` for
#     base preset resolution). Does NOT depend on MP-MCP-RESOURCES
#     directly — the existing mint://preset/* read URIs surface new
#     versions transparently because they walk PRESETS_DIR.
#   LINKS: docs/development-plan.xml#MP-THEME-EDIT,
#     docs/verification-plan.xml#V-MP-THEME-EDIT,
#     docs/verification-plan.xml#VF-022,
#     docs/knowledge-graph.xml#MP-THEME-EDIT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PresetNotFound                - structured ToolError; PRESET_NOT_FOUND
#   InvalidPatch                  - structured ToolError; INVALID_PATCH
#                                   (bad hex / negative spacing / empty
#                                   font / unknown patch key)
#   PresetVersionConflict         - structured ToolError;
#                                   PRESET_VERSION_CONFLICT (collision
#                                   on the next-version file)
#   CANONICAL_RESULT_KEYS         - 5-key oracle for the return dict
#                                   shape (VF-022 contract surface)
#   PALETTE_KEYS / TYPOGRAPHY_KEYS / SPACING_KEYS
#                                  - frozensets of allowed patch keys
#                                    per tool surface
#   HEX_RE                        - palette-hex validator
#   PRESETS_DIR                   - module-level Path; defaults to
#                                   mint_python.core.presets dir;
#                                   monkeypatched in tests
#   _VERSIONED_FILENAME_RE        - parses <name>_v<MAJOR.MINOR>.yaml
#   _semver_tuple / _bump_minor   - semver helpers (mirror
#                                   MP-TEMPLATES-REGISTRY pattern)
#   resolve_latest_preset_path    - PUBLIC (W17-0): latest versioned
#                                   sibling OR built-in; imported by
#                                   MP-AUDIT-EXTEND + MP-MCP-RESOURCES-
#                                   VERSIONED (single source of truth
#                                   for "what's the latest preset")
#   collect_preset_versions       - PUBLIC (W17-0): ascending semver list
#                                   of ALL available versions for `name`;
#                                   used by MP-MCP-RESOURCES-VERSIONED
#                                   to populate predecessor_versions
#                                   metadata in list_presets summary
#   _load_current_preset_raw     - parses current preset YAML into a
#                                   plain dict (preserves comments-as-
#                                   absent + key order via PyYAML)
#   _apply_palette_patch          - pure merge; raises InvalidPatch
#   _apply_typography_patch       - pure merge; raises InvalidPatch
#   _apply_spacing_patch          - pure merge; raises InvalidPatch
#   _write_versioned_preset       - atomic-ish write to a NEW path; if
#                                   path exists raises
#                                   PresetVersionConflict
#   _append_preset_audit          - append-only JSONL entry
#   _update_preset                - shared orchestration: auth -> load
#                                   base -> patch -> bump -> write ->
#                                   audit -> log
#   mint_update_preset_palette    - @server.tool async fn
#   mint_update_preset_typography - @server.tool async fn
#   mint_update_preset_spacing    - @server.tool async fn
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-16 Wave-16-2 (MP-THEME-EDIT). Initial
#     module. Mirrors MP-TEMPLATES-WRITE versioning + audit pattern with
#     a structured-patch surface (5 + 5 + 4 knobs) so models don't have
#     to compose full preset YAML. inv-7 GRACE-MANIFEST-CARRIES-PRESET
#     -VERSION deferred — document.py does not yet stamp preset version
#     into _audit_instructions (out-of-scope for W2; carry-forward note
#     to Phase-17 in the matching test scenario-10).
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastmcp import Context
from fastmcp.exceptions import ToolError

from mint_python.core.style import BUILTIN_PRESETS
from mint_python.mcp.auth import require_template_writer
from mint_python.mcp.document import server
from mint_python.mcp.telemetry import track_call

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-ThemeEdit"


# --------------------------------------------------------------------------- #
# Errors — surface via fastmcp.exceptions.ToolError so FastMCP wraps them into
# a structured MCP error response without leaking a Python traceback. The
# class names mirror their error codes (NO N818 suffix per project style).
# --------------------------------------------------------------------------- #


class PresetNotFound(ToolError):  # noqa: N818 — code PRESET_NOT_FOUND mirrors class name
    """Requested preset name is unknown — neither a built-in nor a versioned
    sibling in PRESETS_DIR. Raised BEFORE auth check is irrelevant because
    auth still runs first; raised AFTER auth admit on the load step."""


class InvalidPatch(ToolError):  # noqa: N818 — code INVALID_PATCH mirrors class name
    """Patch payload fails range / shape validation. Raised AFTER auth
    admit but BEFORE any disk write (VF-022 inv-6 PATCH-VALIDATION-PRE
    -WRITE). Carries the failing key + reason in the message."""


class PresetVersionConflict(ToolError):  # noqa: N818 — code PRESET_VERSION_CONFLICT mirrors class name
    """Target versioned file already exists on disk. The write path is
    strictly append-only — concurrent writers re-fetch + retry rather
    than clobber (VF-022 inv-3 NEVER-OVERWRITE)."""


# --------------------------------------------------------------------------- #
# Constants — pin contract surface
# --------------------------------------------------------------------------- #


# Canonical return-dict keys. External MCP clients depend on this exact
# shape; any change is a contract break.
CANONICAL_RESULT_KEYS: tuple[str, ...] = (
    "name",
    "version",
    "audit_id",
    "predecessor_version",
    "patched_fields",
)

# Patch-surface key sets. Models may pass any subset; missing keys
# preserve the current preset value.
PALETTE_KEYS: frozenset[str] = frozenset(
    {"primary", "secondary", "accent", "text", "background"}
)
TYPOGRAPHY_KEYS: frozenset[str] = frozenset(
    {"heading_font", "body_font", "base_size_pt", "heading_scale", "line_height"}
)
SPACING_KEYS: frozenset[str] = frozenset(
    {"paragraph_pt", "section_pt", "page_margin_top_pt", "page_margin_side_pt"}
)

HEX_RE: re.Pattern[str] = re.compile(r"^#[0-9A-Fa-f]{6}$")

# `<name>_v<MAJOR.MINOR>.yaml` — versioned sibling pattern. Matches the
# MP-TEMPLATES-REGISTRY convention so the audit story stays parallel.
_VERSIONED_FILENAME_RE: re.Pattern[str] = re.compile(
    r"^(?P<name>[a-zA-Z0-9_-]+?)_v(?P<version>\d+\.\d+)\.yaml$"
)

# Default presets directory. Set at import time from the MP-STYLE built-
# in registry's parent so the module emits versioned siblings into the
# same dir the read-path (mint://preset/*) scans. Tests monkeypatch this
# to a tmp_path-isolated directory.
PRESETS_DIR: Path = Path(__file__).resolve().parent.parent / "core" / "presets"


# --------------------------------------------------------------------------- #
# Semver helpers — mirror mint_python.templates.registry MAJOR.MINOR shape.
# Duplicated rather than imported to keep the W2 worker scope disjoint from
# the templates module (forbidden write scope).
# --------------------------------------------------------------------------- #


def _semver_tuple(version: str) -> tuple[int, int]:
    """Convert '1.10' → (1, 10) for ordered comparison."""
    parts = version.split(".")
    return (int(parts[0]), int(parts[1]))


def _bump_minor(version: str) -> str:
    """1.0 → 1.1, 1.9 → 1.10. The W2 versioning model is intentionally
    minimal (MAJOR.MINOR only) so the audit signal matches MP-TEMPLATES
    -WRITE exactly."""
    major, minor = _semver_tuple(version)
    return f"{major}.{minor + 1}"


# --------------------------------------------------------------------------- #
# Preset resolution + load
# --------------------------------------------------------------------------- #


def resolve_latest_preset_path(name: str) -> Path:
    """Return the path of the CURRENT latest version of `name` to bump from.

    Resolution order:
      1. Latest `<name>_v<X>.yaml` sibling in PRESETS_DIR (highest semver).
      2. Built-in baseline from mint_python.core.style.BUILTIN_PRESETS.
      3. Raise PresetNotFound.

    Versioned siblings live alongside their canonical baseline; once an
    edit lands the next read MUST resolve to the new version. Built-in
    fallback only kicks in for the FIRST edit of an un-edited preset.
    """
    # Step 1: scan PRESETS_DIR for versioned siblings of this name.
    if PRESETS_DIR.exists():
        candidates: list[tuple[tuple[int, int], Path]] = []
        for path in PRESETS_DIR.glob(f"{name}_v*.yaml"):
            match = _VERSIONED_FILENAME_RE.match(path.name)
            if match and match.group("name") == name:
                candidates.append((_semver_tuple(match.group("version")), path))
        if candidates:
            candidates.sort(key=lambda pair: pair[0])
            return candidates[-1][1]

    # Step 2: built-in baseline.
    if name in BUILTIN_PRESETS:
        return BUILTIN_PRESETS[name]

    # Step 3: unknown.
    raise PresetNotFound(
        f"PRESET_NOT_FOUND: no preset named {name!r}. "
        f"Built-ins: {sorted(BUILTIN_PRESETS)}"
    )


def collect_preset_versions(name: str) -> list[str]:
    """Return the sorted list of all available version strings for `name`.

    Includes the baseline '1.0' from BUILTIN_PRESETS (or whatever the
    base YAML's `version:` field declares) and every versioned sibling
    in PRESETS_DIR. Returned in ascending semver order; the last element
    is the same version that `resolve_latest_preset_path` resolves to.

    Used by MP-MCP-RESOURCES-VERSIONED to populate the
    `predecessor_versions` metadata in list_presets summary output —
    callers see the full edit chain at a glance.

    Raises PresetNotFound when `name` is not in BUILTIN_PRESETS AND no
    versioned siblings exist — mirrors `resolve_latest_preset_path`.
    """
    versions: list[tuple[int, int]] = []

    # Versioned siblings (Phase-16 W2+ edits).
    if PRESETS_DIR.exists():
        for path in PRESETS_DIR.glob(f"{name}_v*.yaml"):
            match = _VERSIONED_FILENAME_RE.match(path.name)
            if match and match.group("name") == name:
                versions.append(_semver_tuple(match.group("version")))

    # Built-in baseline. We don't actually parse the YAML for `version:` —
    # by convention BUILTIN_PRESETS baselines are at 1.0; if the YAML
    # disagrees the writer will catch it at the next edit.
    if name in BUILTIN_PRESETS and (1, 0) not in versions:
        versions.append((1, 0))

    if not versions:
        raise PresetNotFound(
            f"PRESET_NOT_FOUND: no preset named {name!r}. "
            f"Built-ins: {sorted(BUILTIN_PRESETS)}"
        )

    versions.sort()
    return [f"{major}.{minor}" for major, minor in versions]


def _load_current_preset_raw(name: str) -> tuple[dict[str, Any], str, Path]:
    """Return (preset_dict, current_version, source_path).

    The preset is loaded with `yaml.safe_load` so the dict is a plain
    Python mapping (no preserved comments — versioned outputs are clean
    serializations). The version comes from the YAML's `version` field;
    if absent the fallback is '1.0' so the first bump produces 1.1.
    """
    base_path = resolve_latest_preset_path(name)
    raw_text = base_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text)
    if not isinstance(raw, dict):
        # Defensive — built-in presets all parse to dicts; this branch
        # only fires if a malformed versioned sibling sneaks in.
        raise PresetNotFound(
            f"PRESET_NOT_FOUND: preset {name!r} at {base_path} did not "
            f"parse to a mapping"
        )
    current_version = str(raw.get("version", "1.0"))
    return raw, current_version, base_path


# --------------------------------------------------------------------------- #
# Patch validation + merge — pure functions, NO disk I/O.
# Each returns the list of keys actually changed (for the response's
# `patched_fields` slot + the log marker).
# --------------------------------------------------------------------------- #


def _validate_patch_keys(patch: Any, allowed: frozenset[str], surface: str) -> None:
    """Type-check the patch payload + reject unknown / wrong-shape keys.

    Empty patch dicts are NOT rejected here — they pass through and
    produce an empty `patched_fields` list. That preserves the
    contract that author-tagged "no-op bumps" can ship (rare but
    legitimate: re-stamping authorship after an unrelated metadata
    change).
    """
    if not isinstance(patch, dict):
        raise InvalidPatch(
            f"INVALID_PATCH: {surface} patch must be an object, got "
            f"{type(patch).__name__}"
        )
    unknown = set(patch) - allowed
    if unknown:
        raise InvalidPatch(
            f"INVALID_PATCH: {surface} patch has unknown keys "
            f"{sorted(unknown)}; allowed: {sorted(allowed)}"
        )


def _apply_palette_patch(
    preset: dict[str, Any], patch: dict[str, Any]
) -> list[str]:
    """Merge palette keys into preset['color_palette'].

    All values must be #RRGGBB hex strings; non-hex raises InvalidPatch.
    Returns the list of keys actually changed (i.e. present in patch
    AND different from the current value)."""
    _validate_patch_keys(patch, PALETTE_KEYS, "palette")

    # Validate hex BEFORE mutating anything (VF-022 inv-6 pre-write).
    for key, value in patch.items():
        if not isinstance(value, str) or not HEX_RE.match(value):
            raise InvalidPatch(
                f"INVALID_PATCH: palette[{key!r}] must be a #RRGGBB hex "
                f"string, got {value!r}"
            )

    palette = preset.setdefault("color_palette", {})
    if not isinstance(palette, dict):
        # Defensive: a malformed preset shouldn't have made it past
        # _load_current_preset_raw, but if it did we surface as
        # PRESET_NOT_FOUND-shaped failure rather than corrupting data.
        raise InvalidPatch(
            f"INVALID_PATCH: preset color_palette is not a mapping "
            f"(got {type(palette).__name__})"
        )

    changed: list[str] = []
    for key, value in patch.items():
        if palette.get(key) != value:
            palette[key] = value
            changed.append(key)
    return changed


def _apply_typography_patch(
    preset: dict[str, Any], patch: dict[str, Any]
) -> list[str]:
    """Merge typography knobs into preset['typography'].

    The patch surface is intentionally a compressed set of high-level
    knobs that fan out across the deeper structure consumed by MP-STYLE:
      - heading_font   -> heading1/2/3.font
      - body_font      -> body / table_header / caption .font
      - base_size_pt   -> body.size_pt
      - heading_scale  -> heading1 = base * scale^2,
                          heading2 = base * scale^1.5,
                          heading3 = base * scale
      - line_height    -> body.line_height + spacing.default_line_height

    Returns the list of patch keys actually applied (a key counts as
    applied iff at least one resulting field changed)."""
    _validate_patch_keys(patch, TYPOGRAPHY_KEYS, "typography")

    # Pre-write validation.
    numeric_keys = {"base_size_pt", "heading_scale", "line_height"}
    for key, value in patch.items():
        if key in ("heading_font", "body_font"):
            if not isinstance(value, str) or not value.strip():
                raise InvalidPatch(
                    f"INVALID_PATCH: typography[{key!r}] must be a non-empty "
                    f"string, got {value!r}"
                )
        elif key in numeric_keys:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidPatch(
                    f"INVALID_PATCH: typography[{key!r}] must be a number, "
                    f"got {type(value).__name__}"
                )
            if value <= 0:
                raise InvalidPatch(
                    f"INVALID_PATCH: typography[{key!r}] must be > 0, got {value}"
                )

    typography = preset.setdefault("typography", {})
    if not isinstance(typography, dict):
        raise InvalidPatch(
            f"INVALID_PATCH: preset typography is not a mapping "
            f"(got {type(typography).__name__})"
        )

    # Snapshot the typography sub-tree BEFORE applying any knob, so we
    # can decide per-knob whether it actually changed something. Deep
    # copy is unnecessary — we compare values at the leaf level.
    changed: list[str] = []

    def _set_font(targets: tuple[str, ...], font: str) -> bool:
        any_change = False
        for target in targets:
            spec = typography.setdefault(target, {})
            if isinstance(spec, dict) and spec.get("font") != font:
                spec["font"] = font
                any_change = True
        return any_change

    if "heading_font" in patch and _set_font(
        ("heading1", "heading2", "heading3"), patch["heading_font"]
    ):
        changed.append("heading_font")

    if "body_font" in patch and _set_font(
        ("body", "table_header", "caption"), patch["body_font"]
    ):
        changed.append("body_font")

    # base_size_pt + heading_scale need to be resolved together so the
    # heading sizes track the latest base. If either is in the patch
    # we recompute heading sizes from the resulting base * scale.
    body = typography.setdefault("body", {})
    if not isinstance(body, dict):  # pragma: no cover — defensive
        raise InvalidPatch("INVALID_PATCH: preset typography.body is not a mapping")
    current_base_raw = body.get("size_pt", 11)
    # current_base_raw is body.size_pt from the preset YAML (always a
    # number per MP-STYLE schema); the type-check on patch values above
    # already gated base_size_pt as a number. Cast through Any here just
    # to satisfy mypy on the heterogeneous Mapping.get fallback.
    new_base = float(patch.get("base_size_pt", current_base_raw))  # type: ignore[arg-type]
    base_changed = "base_size_pt" in patch and body.get("size_pt") != new_base
    if base_changed:
        body["size_pt"] = new_base
        changed.append("base_size_pt")

    if "heading_scale" in patch:
        scale = float(patch["heading_scale"])
        sizes = {
            "heading1": round(new_base * scale * scale, 2),
            "heading2": round(new_base * (scale ** 1.5), 2),
            "heading3": round(new_base * scale, 2),
        }
        any_heading_change = False
        for target, size in sizes.items():
            spec = typography.setdefault(target, {})
            if isinstance(spec, dict) and spec.get("size_pt") != size:
                spec["size_pt"] = size
                any_heading_change = True
        if any_heading_change:
            changed.append("heading_scale")

    if "line_height" in patch:
        line_height = float(patch["line_height"])
        body_changed = body.get("line_height") != line_height
        if body_changed:
            body["line_height"] = line_height
        # Also surface at the top-level spacing block (the schema's
        # default_line_height) so non-body styles inherit consistently.
        spacing = preset.setdefault("spacing", {})
        spacing_changed = False
        if isinstance(spacing, dict) and spacing.get("default_line_height") != line_height:
            spacing["default_line_height"] = line_height
            spacing_changed = True
        if body_changed or spacing_changed:
            changed.append("line_height")

    return changed


def _apply_spacing_patch(
    preset: dict[str, Any], patch: dict[str, Any]
) -> list[str]:
    """Merge spacing knobs into preset['spacing'].

    Surface mapping:
      - paragraph_pt        -> spacing.paragraph_default_after_pt
      - section_pt          -> spacing.paragraph_default_before_pt
      - page_margin_top_pt  -> spacing.page_margin_top_pt (extension key)
      - page_margin_side_pt -> spacing.page_margin_side_pt (extension key)

    All values must be non-negative numbers; negative values raise
    InvalidPatch (VF-022 inv-6 PATCH-VALIDATION-PRE-WRITE — verified by
    scenario-6 in the integration suite)."""
    _validate_patch_keys(patch, SPACING_KEYS, "spacing")

    for key, value in patch.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidPatch(
                f"INVALID_PATCH: spacing[{key!r}] must be a number, "
                f"got {type(value).__name__}"
            )
        if value < 0:
            raise InvalidPatch(
                f"INVALID_PATCH: spacing[{key!r}] must be >= 0, got {value}"
            )

    spacing = preset.setdefault("spacing", {})
    if not isinstance(spacing, dict):
        raise InvalidPatch(
            f"INVALID_PATCH: preset spacing is not a mapping "
            f"(got {type(spacing).__name__})"
        )

    mapping: dict[str, str] = {
        "paragraph_pt": "paragraph_default_after_pt",
        "section_pt": "paragraph_default_before_pt",
        "page_margin_top_pt": "page_margin_top_pt",
        "page_margin_side_pt": "page_margin_side_pt",
    }
    changed: list[str] = []
    for patch_key, value in patch.items():
        target_key = mapping[patch_key]
        if spacing.get(target_key) != value:
            spacing[target_key] = value
            changed.append(patch_key)
    return changed


# --------------------------------------------------------------------------- #
# Write + audit — touch disk ONLY after auth admit + patch validation.
# --------------------------------------------------------------------------- #


def _write_versioned_preset(
    preset: dict[str, Any], name: str, version: str
) -> Path:
    """Serialize `preset` to PRESETS_DIR/<name>_v<version>.yaml.

    Raises PresetVersionConflict if the target path already exists —
    NEVER overwrites (VF-022 inv-3 NEVER-OVERWRITE / forbidden-2).
    Uses O_CREAT|O_EXCL|O_WRONLY to make the existence-check + create
    atomic against concurrent writers (scenario-7 race)."""
    target_path = PRESETS_DIR / f"{name}_v{version}.yaml"

    # Stamp the version into the YAML body so the on-disk content matches
    # the filename's version slot (mirrors MP-TEMPLATES-WRITE's anti-drift
    # invariant: filename and content never disagree).
    preset["version"] = version

    serialized = yaml.safe_dump(preset, sort_keys=False, allow_unicode=True)
    encoded = serialized.encode("utf-8")

    try:
        fd = os.open(
            target_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode=0o644
        )
    except FileExistsError as exc:
        raise PresetVersionConflict(
            f"PRESET_VERSION_CONFLICT: {target_path} already exists; "
            f"refusing to overwrite. Re-fetch the preset and retry."
        ) from exc
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)
    return target_path


def _append_preset_audit(entry: dict[str, Any]) -> None:
    """Append a JSONL line to PRESETS_DIR/_audit.jsonl.

    Append-only: 'a' mode + newline-terminated JSON object per line.
    Existing lines are byte-identical after the call (VF-022 inv-5
    AUDIT-JSONL-APPEND-ONLY)."""
    audit_path = PRESETS_DIR / "_audit.jsonl"
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# Shared orchestration — auth -> load -> patch -> bump -> write -> audit -> log.
# Three thin tool wrappers below dispatch to this via a patch-fn closure.
# --------------------------------------------------------------------------- #


PatchFn = Callable[[dict[str, Any], dict[str, Any]], list[str]]


def _update_preset(
    *,
    name: str,
    patch: dict[str, Any],
    author: str,
    surface: str,
    patch_fn: PatchFn,
) -> dict[str, Any]:
    """Shared core called by all 3 @server.tool wrappers.

    `surface` ('palette' | 'typography' | 'spacing') tags the log marker
    + audit entry so we can correlate which tool fired in trace analysis.
    `patch_fn` is the surface-specific merger that returns the list of
    keys actually changed.

    Order of operations (matches VF-022 trace-sequence + invariant
    requirements):
      1. require_template_writer(author) — auth gate BEFORE any I/O.
      2. _load_current_preset_raw — read disk (built-in OR versioned).
      3. _apply_*_patch — pure merge + validate (raises InvalidPatch
         BEFORE any write — VF-022 inv-6).
      4. _bump_minor — compute next version from predecessor.
      5. _write_versioned_preset — atomic create-or-conflict.
      6. _append_preset_audit — JSONL append.
      7. BLOCK_PRESET_WRITE log marker.
    """
    # Step 1: auth (BLOCK_AUTH_ADMIT / BLOCK_AUTH_DENY fires inside).
    # Deny path raises TemplateWriteForbidden which FastMCP surfaces to
    # the client; BLOCK_PRESET_WRITE NEVER fires on deny (forbidden-3).
    require_template_writer(author)

    # Step 2: load current state.
    preset, predecessor_version, _source_path = _load_current_preset_raw(name)

    # Step 3: patch (pre-write validation + merge).
    patched_fields = patch_fn(preset, patch)

    # Step 4: compute next version.
    next_version = _bump_minor(predecessor_version)

    # Step 5: write (atomic O_EXCL — raises PresetVersionConflict on race).
    target_path = _write_versioned_preset(preset, name, next_version)

    # Step 6: audit append.
    audit_id = str(uuid.uuid4())
    content_sha256 = hashlib.sha256(target_path.read_bytes()).hexdigest()
    audit_entry = {
        "audit_id": audit_id,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "name": name,
        "surface": surface,
        "version": next_version,
        "predecessor_version": predecessor_version,
        "author": author,
        "patched_fields": patched_fields,
        "content_sha256": content_sha256,
        "written_to": str(target_path),
    }
    _append_preset_audit(audit_entry)

    # Step 7: log marker. Per-tool variant (palette/typography/spacing)
    # so scenario-9 can assert the marker sequence per surface.
    # START_BLOCK_PRESET_WRITE
    logger.info(
        "[%s][%s][BLOCK_PRESET_WRITE] "
        "name=%s version=%s predecessor_version=%s "
        "patched_fields=%s content_sha256=%s audit_id=%s",
        _LOG_PREFIX,
        surface,
        name,
        next_version,
        predecessor_version,
        patched_fields,
        content_sha256,
        audit_id,
    )
    # END_BLOCK_PRESET_WRITE

    return {
        "name": name,
        "version": next_version,
        "audit_id": audit_id,
        "predecessor_version": predecessor_version,
        "patched_fields": patched_fields,
    }


# --------------------------------------------------------------------------- #
# Public MCP tools — one per surface.
# --------------------------------------------------------------------------- #


@server.tool(name="mint_update_preset_palette")
async def mint_update_preset_palette(
    name: str,
    palette: dict[str, Any],
    author: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Patch a preset's color palette.

    Allowed `palette` keys (all optional — missing keys preserve the
    current value): primary, secondary, accent, text, background. All
    values must be #RRGGBB hex strings.

    Args:
        name: preset name — must resolve to either a built-in
            (BUILTIN_PRESETS) or an existing versioned sibling in
            PRESETS_DIR. Raises PresetNotFound otherwise.
        palette: structured patch dict; see PALETTE_KEYS.
        author: identity recorded in presets/_audit.jsonl. Must be on
            the MINT_TEMPLATE_WRITERS allowlist (open mode admits all).
        ctx: fastmcp.Context (currently unused; reserved for future
            progress reporting + per-call cancellation).

    Returns:
        Dict with the canonical 5-key shape (CANONICAL_RESULT_KEYS):
            name, version, audit_id, predecessor_version,
            patched_fields (list of keys actually changed).

    Raises:
        TemplateWriteForbidden: author not on allowlist
            (TEMPLATE_WRITE_FORBIDDEN — from MP-AUTH-SHIM).
        PresetNotFound: name does not resolve.
        InvalidPatch: malformed hex / unknown patch key.
        PresetVersionConflict: target path already exists (race).
    """
    del ctx  # reserved for future progress reporting
    with track_call("mint_update_preset_palette", doc_type=name):
        return _update_preset(
            name=name,
            patch=palette,
            author=author,
            surface="palette",
            patch_fn=_apply_palette_patch,
        )


@server.tool(name="mint_update_preset_typography")
async def mint_update_preset_typography(
    name: str,
    typography: dict[str, Any],
    author: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Patch a preset's typography.

    Allowed `typography` keys (all optional): heading_font, body_font,
    base_size_pt, heading_scale, line_height. See TYPOGRAPHY_KEYS for
    the canonical surface; fan-out into the deeper preset structure
    documented in _apply_typography_patch.

    Args + Returns: same shape as mint_update_preset_palette.
    """
    del ctx
    with track_call("mint_update_preset_typography", doc_type=name):
        return _update_preset(
            name=name,
            patch=typography,
            author=author,
            surface="typography",
            patch_fn=_apply_typography_patch,
        )


@server.tool(name="mint_update_preset_spacing")
async def mint_update_preset_spacing(
    name: str,
    spacing: dict[str, Any],
    author: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Patch a preset's spacing.

    Allowed `spacing` keys (all optional, all non-negative numbers):
    paragraph_pt, section_pt, page_margin_top_pt, page_margin_side_pt.

    Args + Returns: same shape as mint_update_preset_palette.
    """
    del ctx
    with track_call("mint_update_preset_spacing", doc_type=name):
        return _update_preset(
            name=name,
            patch=spacing,
            author=author,
            surface="spacing",
            patch_fn=_apply_spacing_patch,
        )


__all__ = [
    "CANONICAL_RESULT_KEYS",
    "HEX_RE",
    "PALETTE_KEYS",
    "PRESETS_DIR",
    "SPACING_KEYS",
    "TYPOGRAPHY_KEYS",
    "InvalidPatch",
    "PresetNotFound",
    "PresetVersionConflict",
    "mint_update_preset_palette",
    "mint_update_preset_spacing",
    "mint_update_preset_typography",
]
