# FILE: src/mint_python/mcp/auth.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Authorization shim for write-path MCP tools. Today
#     update_template is writable by any connected MCP client (Cursor,
#     OpenWebUI, sidecars) — a stray model can clobber governed templates.
#     This shim gates the write path behind a config-driven author
#     allowlist; read-path tools (list_templates, get_template,
#     list_presets, get_preset, mint_read_grace_manifest, mint://
#     resource handlers) MUST NOT call into this module — read stays
#     open by design. First write tool gated is update_template (Phase-15
#     Wave-15-1). Closes audit Priority-4 sub-clause "Authorization shim:
#     read = open; write = config-gated allowlist" left as TODO at
#     Phase-14 close.
#   SCOPE: Public surface = WritersConfig dataclass,
#     TemplateWriteForbidden / AuthConfigInvalid errors,
#     load_writers_config (cached lazy resolver), is_template_writer
#     (pure check), require_template_writer (raise-on-deny entry).
#     Pure-decision module: NO disk writes, NO mutation of templates/,
#     NO mutation of _audit.jsonl. Stdlib-only beyond logging.
#   DEPENDS: stdlib (json, logging, os, pathlib, dataclasses, typing).
#   LINKS: docs/development-plan.xml#MP-AUTH-SHIM,
#     docs/verification-plan.xml#V-MP-AUTH-SHIM,
#     docs/verification-plan.xml#VF-017,
#     docs/knowledge-graph.xml#MP-AUTH-SHIM
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   WritersConfig             - frozen dataclass: writers tuple,
#                               source ('env'|'file'|'none'), open_mode flag
#   TemplateWriteForbidden    - raised by require_template_writer on deny
#   AuthConfigInvalid         - malformed writers.json; raised on first
#                               require_template_writer call (lazy load).
#                               Cache poisoned after first failure
#                               (re-raises from cache on subsequent calls).
#   load_writers_config       - resolve env > file > none; cached
#                               per-process via _CACHE module global;
#                               emits BLOCK_AUTH_OPEN_MODE WARNING ONCE
#                               when config resolves to open mode.
#   is_template_writer        - pure check; returns bool, no side effects
#   require_template_writer   - raise TemplateWriteForbidden on deny;
#                               called by update_template BEFORE any
#                               disk write. Emits BLOCK_AUTH_ADMIT on
#                               admit, BLOCK_AUTH_DENY on deny.
#   _reset_for_tests          - clear cache + open-mode-warned flag;
#                               consumed by clean_writers_config fixture.
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-15 Wave-15-1 initial implementation per
#     V-MP-AUTH-SHIM scenarios 1-5 + VF-017 invariants.
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class TemplateWriteForbidden(Exception):  # noqa: N818 — error code TEMPLATE_WRITE_FORBIDDEN mirrors class name; suffix omitted intentionally
    """require_template_writer denied the author. Raised before any
    disk mutation in update_template; the templates/ directory and
    _audit.jsonl remain byte-identical (VF-017 inv-1)."""


class AuthConfigInvalid(Exception):  # noqa: N818 — error code AUTH_CONFIG_INVALID mirrors class name; suffix omitted intentionally
    """writers.json malformed (not JSON, not a top-level object, missing
    'writers' key, or 'writers' is not a list of strings). Raised lazily
    at first require_template_writer call — NOT at module import. Cache
    poisoned after first failure (re-raises from cache; VF-017 inv-5)."""


# --------------------------------------------------------------------------- #
# Config dataclass
# --------------------------------------------------------------------------- #


_CONFIG_SOURCE = Literal["env", "file", "none"]


@dataclass(frozen=True)
class WritersConfig:
    """Resolved writers allowlist + provenance.

    open_mode is True when both env and file are absent (writers tuple
    is empty AND source is 'none'); is_template_writer admits any
    author in this mode. The full writers tuple MUST NEVER appear in
    log payloads (VF-017 forbidden-3 / inv-6) — only `source` and the
    rejected `author` may surface in caplog.
    """

    writers: tuple[str, ...]
    source: _CONFIG_SOURCE
    open_mode: bool


# --------------------------------------------------------------------------- #
# Cache + lazy loader
# --------------------------------------------------------------------------- #


# Sentinel "not loaded yet" preserves the lazy-load contract: load on
# first require_template_writer call, NOT at module import. A cached
# AuthConfigInvalid instance poisons subsequent calls (VF-017 inv-5).
_CACHE: WritersConfig | AuthConfigInvalid | None = None
_OPEN_MODE_WARNED: bool = False


def _config_file_path() -> Path:
    """~/.config/mint/writers.json. HOME is read at call time so tests
    can monkeypatch HOME via the env to redirect to tmp_path."""
    return Path(os.path.expanduser("~/.config/mint/writers.json"))


def _parse_env(raw: str) -> tuple[str, ...]:
    """Comma-separated; whitespace around entries trimmed; empty entries
    dropped. Empty input ('' or all-whitespace) returns an empty tuple
    so the caller can fall through to file/none resolution."""
    return tuple(entry.strip() for entry in raw.split(",") if entry.strip())


def _parse_file(path: Path) -> tuple[str, ...]:
    """Schema: top-level object with 'writers' key → list[str]. Anything
    else raises AuthConfigInvalid naming the path + parse failure mode
    so the operator knows where to look."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthConfigInvalid(
            f"AUTH_CONFIG_INVALID: {path} did not parse as JSON ({exc})"
        ) from exc
    if not isinstance(raw, dict):
        raise AuthConfigInvalid(
            f"AUTH_CONFIG_INVALID: {path} top-level must be an object "
            f"(got {type(raw).__name__})"
        )
    if "writers" not in raw:
        raise AuthConfigInvalid(
            f"AUTH_CONFIG_INVALID: {path} missing required key 'writers'"
        )
    writers = raw["writers"]
    if not isinstance(writers, list) or not all(isinstance(w, str) for w in writers):
        raise AuthConfigInvalid(
            f"AUTH_CONFIG_INVALID: {path} 'writers' must be a list of strings"
        )
    return tuple(writers)


def load_writers_config() -> WritersConfig:
    """Resolve writers config with precedence env > file > none. Cached
    per-process — first call does the env+file resolution; subsequent
    calls hit the cache (VF-017 inv-4 CACHE-INVARIANT).

    Open mode (both env and file absent / env empty / file absent)
    emits [MP-Auth][check_writer][BLOCK_AUTH_OPEN_MODE] WARNING ONCE
    per process. Subsequent admit calls in open mode do NOT re-emit
    (VF-017 inv-2 OPEN-MODE-WARNS-ONCE).

    Raises:
        AuthConfigInvalid: writers.json present but malformed. The
            cache is poisoned with the exception so subsequent calls
            re-raise it without re-touching disk (VF-017 inv-5).
    """
    global _CACHE, _OPEN_MODE_WARNED
    if isinstance(_CACHE, AuthConfigInvalid):
        # Poisoned — re-raise the cached failure rather than retrying.
        raise _CACHE
    if _CACHE is not None:
        return _CACHE

    env_raw = os.environ.get("MINT_TEMPLATE_WRITERS")
    if env_raw is not None and env_raw.strip() != "":
        writers = _parse_env(env_raw)
        # _parse_env may still produce empty tuple if every entry was
        # whitespace ("  ,  "); treat that as open mode rather than a
        # zero-writer allowlist (which would deny everyone).
        if writers:
            _CACHE = WritersConfig(writers=writers, source="env", open_mode=False)
            return _CACHE

    config_path = _config_file_path()
    if config_path.exists():
        try:
            writers = _parse_file(config_path)
        except AuthConfigInvalid as exc:
            _CACHE = exc  # Poison the cache.
            raise
        if writers:
            _CACHE = WritersConfig(writers=writers, source="file", open_mode=False)
            return _CACHE

    # Neither source contributes → open mode. Warn ONCE per process.
    config = WritersConfig(writers=(), source="none", open_mode=True)
    _CACHE = config
    if not _OPEN_MODE_WARNED:
        _OPEN_MODE_WARNED = True
        # START_BLOCK_AUTH_OPEN_MODE
        logger.warning(
            "[MP-Auth][check_writer][BLOCK_AUTH_OPEN_MODE] "
            "message=no writers configured; update_template is open to all callers "
            "config_source=none"
        )
        # END_BLOCK_AUTH_OPEN_MODE
    return config


# --------------------------------------------------------------------------- #
# Public checks
# --------------------------------------------------------------------------- #


def is_template_writer(author: str) -> bool:
    """Pure check: True if `author` may write templates under the
    current process's resolved config. Open mode admits any author.
    No side effects, no logging — call require_template_writer for
    the logged + raise-on-deny variant.
    """
    config = load_writers_config()
    if config.open_mode:
        return True
    return author in config.writers


def require_template_writer(author: str) -> None:
    """Raise TemplateWriteForbidden if `author` is not on the allowlist.
    Called by update_template BEFORE any disk I/O — the destructive
    check at the top of the function body, before semver computation,
    before audit-log append, before any file open (mirrors V-MP-FIX
    forbidden-2 fix-pattern).

    Logs:
        BLOCK_AUTH_ADMIT (INFO) on admit when source != 'none'.
        BLOCK_AUTH_DENY  (INFO) on deny.
        Open-mode admits do NOT emit BLOCK_AUTH_ADMIT — the open-mode
        warning already fired (once) in load_writers_config; subsequent
        admits stay silent so caplog isn't flooded.

    Raises:
        TemplateWriteForbidden: author not on allowlist.
        AuthConfigInvalid: writers.json present but malformed (re-
            raised from cache on subsequent calls).
    """
    config = load_writers_config()
    if config.open_mode:
        # Admit silently — the once-per-process BLOCK_AUTH_OPEN_MODE
        # warning is the operator signal here.
        return
    if author in config.writers:
        # START_BLOCK_AUTH_ADMIT
        logger.info(
            "[MP-Auth][check_writer][BLOCK_AUTH_ADMIT] "
            "author=%s config_source=%s",
            author,
            config.source,
        )
        # END_BLOCK_AUTH_ADMIT
        return
    # START_BLOCK_AUTH_DENY
    logger.info(
        "[MP-Auth][check_writer][BLOCK_AUTH_DENY] "
        "author=%s reason=not_in_writers config_source=%s",
        author,
        config.source,
    )
    # END_BLOCK_AUTH_DENY
    raise TemplateWriteForbidden(
        f"TEMPLATE_WRITE_FORBIDDEN: author {author!r} is not on the "
        f"template-writers allowlist (config_source={config.source!r}). "
        f"Add the author to MINT_TEMPLATE_WRITERS or "
        f"~/.config/mint/writers.json and retry."
    )


def _reset_for_tests() -> None:
    """Clear the process cache + the open-mode-warned flag so each
    test starts from a clean state. Consumed by the
    clean_writers_config fixture in tests/unit/conftest.py.
    """
    global _CACHE, _OPEN_MODE_WARNED
    _CACHE = None
    _OPEN_MODE_WARNED = False


__all__ = [
    "AuthConfigInvalid",
    "TemplateWriteForbidden",
    "WritersConfig",
    "is_template_writer",
    "load_writers_config",
    "require_template_writer",
]
