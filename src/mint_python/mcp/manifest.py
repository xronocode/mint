# FILE: src/mint_python/mcp/manifest.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-15 W1 (MP-MANIFEST-READ) — symmetric read counterpart
#     to MP-GRACE injection. Exposes the `mint_read_grace_manifest` MCP
#     tool that opens any docx, locates `grace/manifest_*.xml`, parses
#     the manifest, and returns a canonical dict shape covering the
#     metadata MINT records during create_document. Closes audit
#     Priority-1 acceptance criterion "read_grace_manifest exists and
#     round-trips a manifest written by any other tool" (mcp-audit.md
#     §1 row-7, §3(c), §5 roadmap; never shipped during Phase-14 W2).
#   SCOPE: Public surface = `mint_read_grace_manifest` (FastMCP tool),
#     ManifestReadError + 3 structured error subclasses
#     (ManifestNotFound / ManifestParseError / InvalidDocument),
#     `_canonicalize` (testable internal — strict dict shape), and
#     `_select_most_recent` (testable internal — multi-manifest tie-
#     breaker per VF-018 inv-5).
#   DEPENDS: fastmcp (Context, FastMCP server reused from
#     mint_python.mcp.document), mint_python.grace (describe +
#     GRACEManifest + GRACEInjectionError + GRACE_NS),
#     mint._security.safe_doc (path traversal guard), zipfile + xml.etree
#     (multi-manifest fallback walk for VF-018 inv-5 MOST-RECENT-WINS).
#   LINKS: docs/development-plan.xml#MP-MANIFEST-READ,
#     docs/verification-plan.xml#V-MP-MANIFEST-READ,
#     docs/verification-plan.xml#VF-018,
#     docs/knowledge-graph.xml#MP-MANIFEST-READ
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ManifestReadError              - base error for the tool
#   ManifestNotFound               - MANIFEST_NOT_FOUND (no grace/* part)
#   ManifestParseError             - MANIFEST_PARSE_ERROR (malformed XML)
#   InvalidDocument                - INVALID_DOCUMENT (bad zip / traversal)
#   CANONICAL_KEYS                 - tuple of the 12 keys in the canonical
#                                    dict shape (returned dict is exactly
#                                    these keys, no more). Phase-17 W17-0
#                                    added preset_version + lang for
#                                    MP-AUDIT-EXTEND extended provenance.
#   GRACE_NAMESPACE                - re-exported urn:mint:grace:2026:manifest
#   _canonicalize                  - GRACEManifest -> canonical dict
#   _select_most_recent            - list[GRACEManifest] -> the entry with
#                                    the highest parsed timestamp (ties
#                                    fall back to namelist order)
#   _read_all_manifests            - walk every grace/manifest_*.xml part
#                                    in mode='r' (VF-018 forbidden-1) and
#                                    return parsed GRACEManifest entries
#   mint_read_grace_manifest       - @server.tool async fn; the production
#                                    entry registered on the shared
#                                    FastMCP `server`
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-15 Wave-15-1 (MP-MANIFEST-READ). Initial
#     module. Wraps mint_python.grace.describe() but extends it with
#     multi-manifest most-recent-wins selection (describe() returns the
#     first match in namelist order, which is incorrect when N>1 and
#     audit_id-named parts don't sort chronologically — see VF-018
#     inv-5 + scenario-2 contract). Read path is read-only by contract:
#     all zipfile.ZipFile opens here pin mode='r' explicitly.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastmcp import Context

from mint._security import safe_doc
from mint_python.grace import GRACE_NS, GRACEManifest, _parse_manifest_xml
from mint_python.mcp.document import server
from mint_python.mcp.telemetry import track_call

logger = logging.getLogger(__name__)


GRACE_NAMESPACE = GRACE_NS

CANONICAL_KEYS: tuple[str, ...] = (
    "audit_id",
    "model_identity",
    "fields_elicited",
    "template",
    "template_version",
    "template_author",
    "timestamp",
    "instructions",
    "fingerprint",
    "namespace",
    # Phase-17 W17-0: 2 new keys for extended provenance.
    # MP-AUDIT-EXTEND (W17-3) writes preset_version + lang into the
    # instructions list; the canonicalizer here promotes them to typed
    # fields. On Phase-16 docx (no preset_version stamp) preset_version
    # surfaces as None; on monolingual templates lang surfaces as []
    # (V-MP-AUDIT-EXTEND forbidden-6 — no KeyError on legacy reads).
    "preset_version",
    "lang",
)


# --------------------------------------------------------------------------- #
# Errors — structured tool errors, surfaced to the MCP client without leaking
# Python tracebacks (VF-018 forbidden-4). Each carries a code-style message
# prefix so connected models can route on the prefix without parsing prose.
# --------------------------------------------------------------------------- #


class ManifestReadError(Exception):
    """Base for mint_read_grace_manifest tool errors."""


class ManifestNotFound(ManifestReadError):  # noqa: N818 — error code MANIFEST_NOT_FOUND mirrors class name
    """Docx has no urn:mint:grace:2026:manifest custom XML part."""


class ManifestParseError(ManifestReadError):
    """Manifest XML present but unparseable."""


class InvalidDocument(ManifestReadError):  # noqa: N818 — error code INVALID_DOCUMENT mirrors class name
    """Path is not a valid .docx zip / path traversal rejected."""


# --------------------------------------------------------------------------- #
# Canonicalizer
# --------------------------------------------------------------------------- #


def _parse_kv_instructions(instructions: list[str]) -> dict[str, str]:
    """Parse the `key=value` lines emitted by document._audit_instructions
    into a dict. Lines without an `=` are skipped; on duplicate keys the
    LAST occurrence wins (matches Python dict-update semantics). The
    canonical key set is a strict subset; unknown keys land in the dict
    but are filtered out by _canonicalize."""
    parsed: dict[str, str] = {}
    for line in instructions:
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


def _canonicalize(manifest: GRACEManifest) -> dict[str, Any]:
    """Project a GRACEManifest into the canonical 12-key dict shape.

    GRACE injection encodes audit metadata as `key=value` lines inside
    the manifest's `instructions` list (see document._audit_instructions).
    We parse those lines back out into typed canonical fields and keep the
    full instructions list alongside for callers who want raw access.

    Missing fields surface as None (or empty list / empty string for
    the typed-collection fields), never KeyError — V-MP-MANIFEST-READ
    scenario-1 mandates "values may be None/empty when absent in source
    manifest". V-MP-MANIFEST-READ scenario-9 (Phase-17 W17-0 addition)
    mandates the same for the new preset_version + lang keys: legacy
    Phase-16 docx without those instructions surfaces preset_version=None
    and lang=[] cleanly."""
    kv = _parse_kv_instructions(manifest.instructions)

    fields_elicited_raw = kv.get("fields_elicited", "")
    if not fields_elicited_raw or fields_elicited_raw == "(none)":
        fields_elicited: list[str] = []
    else:
        fields_elicited = [
            name.strip() for name in fields_elicited_raw.split(",") if name.strip()
        ]

    # Phase-17 W17-0: parse lang from comma-separated string; empty / missing → [].
    lang_raw = kv.get("lang", "")
    lang: list[str] = (
        [code.strip() for code in lang_raw.split(",") if code.strip()]
        if lang_raw
        else []
    )

    return {
        "audit_id": kv.get("audit_id") or "",
        "model_identity": kv.get("model_identity") or None,
        "fields_elicited": fields_elicited,
        "template": kv.get("template") or None,
        "template_version": kv.get("template_version") or None,
        "template_author": kv.get("template_author") or None,
        "timestamp": kv.get("generated_at") or "",
        "instructions": list(manifest.instructions),
        "fingerprint": manifest.fingerprint or None,
        "namespace": manifest.namespace,
        # Phase-17 W17-0 additions.
        "preset_version": kv.get("preset_version") or None,
        "lang": lang,
    }


# --------------------------------------------------------------------------- #
# Multi-manifest selection (VF-018 inv-5 MOST-RECENT-WINS)
# --------------------------------------------------------------------------- #


def _read_all_manifests(document_path: Path) -> list[GRACEManifest]:
    """Walk every grace/manifest_*.xml part in the docx and parse each.

    Read-only by contract: the zipfile is opened with mode='r' explicitly
    (VF-018 forbidden-1 ZIP-MODE-PINNED). Parse failures bubble up as
    ManifestParseError naming the offending part — describe() swallows
    these silently which would mask malformed-XML scenarios.
    """
    manifests: list[GRACEManifest] = []
    with zipfile.ZipFile(document_path, mode="r") as zf:
        for name in zf.namelist():
            if not (name.startswith("grace/") and name.endswith(".xml")):
                continue
            xml_bytes = zf.read(name)
            try:
                xml_text = xml_bytes.decode("utf-8")
                manifests.append(_parse_manifest_xml(xml_text, name))
            except (UnicodeDecodeError, ET.ParseError) as exc:
                raise ManifestParseError(
                    f"MANIFEST_PARSE_ERROR: failed to parse "
                    f"xml_part_name={name!r} in document_path="
                    f"{str(document_path)!r}: {type(exc).__name__}"
                ) from exc
    return manifests


def _select_most_recent(manifests: list[GRACEManifest]) -> GRACEManifest:
    """Return the entry with the highest `generated_at` timestamp.

    Phase-14 W3's append-extension scenario names parts by `audit_id`
    (a UUID), so namelist alphabetical order does NOT track chronology.
    We parse each manifest's instructions to extract `generated_at=<iso>`
    and pick the lexicographically-greatest value (ISO 8601 timestamps
    sort correctly as strings). Manifests missing the timestamp sort
    last — safer than crashing when an externally-authored manifest
    omits the field.
    """

    def _ts(m: GRACEManifest) -> str:
        kv = _parse_kv_instructions(m.instructions)
        return kv.get("generated_at", "")

    # Stable sort by timestamp ascending, take the last entry. Stable
    # ordering means manifests with identical timestamps fall back to
    # namelist order (deterministic for repeated runs).
    return sorted(manifests, key=_ts)[-1]


# --------------------------------------------------------------------------- #
# Public tool — mint_read_grace_manifest
# --------------------------------------------------------------------------- #


@server.tool(name="mint_read_grace_manifest")
async def mint_read_grace_manifest(
    document_path: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    """Read the GRACE manifest from a docx and return it as a canonical
    dict.

    Returns a dict with the 12 canonical keys (audit_id, model_identity,
    fields_elicited, template, template_version, template_author,
    timestamp, instructions, fingerprint, namespace, preset_version, lang).
    Values may be None or empty when the source manifest doesn't carry
    that field. Phase-17 W17-0 added preset_version + lang for extended
    provenance — legacy Phase-16 docx without those instructions surfaces
    preset_version=None and lang=[] (V-MP-MANIFEST-READ scenario-9).

    When the docx contains multiple `grace/manifest_*.xml` parts (Phase-14
    W3 append-extension scenario), returns the entry with the highest
    timestamp (VF-018 inv-5 MOST-RECENT-WINS).

    Raises:
        InvalidDocument: path is not a valid .docx zip, or the path
            traversal guard rejected it before any zipfile open.
        ManifestNotFound: docx has no grace/manifest_*.xml part.
        ManifestParseError: a manifest part exists but its XML is
            malformed.
    """
    del ctx  # reserved for future progress reporting
    with track_call("mint_read_grace_manifest"):
        # Path traversal guard — fires BEFORE any zipfile open
        # (V-MP-MANIFEST-READ scenario-4-b + VF-018 forbidden-4).
        try:
            resolved = safe_doc(document_path)
        except (ValueError, OSError) as exc:
            raise InvalidDocument(
                f"INVALID_DOCUMENT: path traversal or invalid path "
                f"document_path={document_path!r}: {exc}"
            ) from exc

        if not resolved.is_file():
            raise InvalidDocument(
                f"INVALID_DOCUMENT: not a regular file "
                f"document_path={document_path!r}"
            )

        try:
            manifests = _read_all_manifests(resolved)
        except ManifestParseError as exc:
            message = str(exc)
            xml_part_name = ""
            marker = "xml_part_name="
            if marker in message:
                tail = message.split(marker, 1)[1]
                xml_part_name = tail.split(" ", 1)[0].strip("'\"")
            # START_BLOCK_MANIFEST_PARSE_ERROR
            logger.info(
                "[MP-Manifest][read][BLOCK_MANIFEST_PARSE_ERROR] "
                "document_path=%s xml_part_name=%s",
                str(resolved),
                xml_part_name,
            )
            # END_BLOCK_MANIFEST_PARSE_ERROR
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise InvalidDocument(
                f"INVALID_DOCUMENT: not a valid zip / unreadable "
                f"document_path={document_path!r}: {type(exc).__name__}"
            ) from exc

        if not manifests:
            # START_BLOCK_MANIFEST_NOT_FOUND
            logger.info(
                "[MP-Manifest][read][BLOCK_MANIFEST_NOT_FOUND] "
                "document_path=%s",
                str(resolved),
            )
            # END_BLOCK_MANIFEST_NOT_FOUND
            raise ManifestNotFound(
                f"MANIFEST_NOT_FOUND: no urn:mint:grace:2026:manifest part "
                f"in document_path={document_path!r}"
            )

        selected = _select_most_recent(manifests)
        canonical = _canonicalize(selected)

        # START_BLOCK_READ_MANIFEST
        logger.info(
            "[MP-Manifest][read][BLOCK_READ_MANIFEST] "
            "document_path=%s manifest_count=%d selected_xml_part_name=%s",
            str(resolved),
            len(manifests),
            selected.xml_part_name,
        )
        # END_BLOCK_READ_MANIFEST

        return canonical


__all__ = [
    "CANONICAL_KEYS",
    "GRACE_NAMESPACE",
    "InvalidDocument",
    "ManifestNotFound",
    "ManifestParseError",
    "ManifestReadError",
    "mint_read_grace_manifest",
]
