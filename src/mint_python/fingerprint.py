# FILE: src/mint_python/fingerprint.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Pure-python port of mint.fingerprint — SHA-256 structural-style
#     drift fingerprint over OOXML documents. Preserves the legacy public API
#     surface (fingerprint / compute / compare / FingerprintResult /
#     DriftStatus / FingerprintError / MissingStyleXmlError / HashFailedError)
#     so the MCP wrap layer (MP-MCP-FINGERPRINT) can import from this module
#     instead of mint.fingerprint and stay constraint-8 compliant.
#   SCOPE: Open .docx/.pptx zip read-only, locate style XML members, feed
#     their bytes through a chunked SHA-256, return hex digest + structural
#     metadata. Compare two hex hashes returning DriftStatus.
#   DEPENDS: stdlib only — hashlib, zipfile, pathlib, dataclasses, enum,
#     logging. NO mint.* imports (constraint-8). NO lxml — styles.xml is
#     hashed as raw bytes, not parsed.
#   LINKS: docs/development-plan.xml#MP-FINGERPRINT,
#     docs/verification-plan.xml#V-MP-FINGERPRINT,
#     docs/knowledge-graph.xml#MP-FINGERPRINT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FingerprintError - base exception for all fingerprint failures
#   MissingStyleXmlError - raised when no style XML member is found in the zip
#   HashFailedError - raised when I/O or hash backend fails mid-read
#   DriftStatus - StrEnum {MATCH, DRIFT, UNKNOWN}
#   FingerprintResult - frozen dataclass {hash, format, has_styles_xml, byte_count}
#   DOCX_STYLE_FILES / DOCX_FALLBACK_FILES - .docx member search order
#   PPTX_STYLE_FILES / PPTX_FALLBACK_FILES - .pptx member search order
#   _CHUNK_SIZE - 64 KB chunked-read constant for SHA-256.update()
#   compute - load document, hash style xml bytes, return FingerprintResult
#   compare - compare two hex hashes, return DriftStatus (None -> UNKNOWN)
#   fingerprint - thin wrapper over compute(Path(document_path))
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-16-1 initial port — pure-python mirror of
#     mint.fingerprint preserving hash output byte-for-byte (porting parity
#     scenario-5). DriftStatus renamed BASELINE_MISSING -> UNKNOWN per the
#     Phase-16 contract; FingerprintResult fields reshaped to {hash, format,
#     has_styles_xml, byte_count}. BLOCK_FP_COMPUTE INFO log marker added
#     (payload: format byte_count chunks_read) for V-MP-FINGERPRINT
#     scenario-6 trace evidence.
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import logging
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-Fingerprint"

# Chunk size used when feeding style-xml bytes into SHA-256.  SHA-256 is
# associative over update() calls, so the hex digest is identical to feeding
# the whole buffer in one call — chunking only affects the chunks_read trace
# payload (and bounds peak memory for very large styles.xml members).
_CHUNK_SIZE: int = 64 * 1024


class FingerprintError(Exception):
    """Base fingerprint error."""


class MissingStyleXmlError(FingerprintError):
    """Required style XML not found in document zip."""


class HashFailedError(FingerprintError):
    """Hash computation failed (I/O or hash backend error mid-read)."""


# START_CONTRACT: DriftStatus
#   PURPOSE: Outcome of compare(hash_a, hash_b)
#   INPUTS: { string value: match | drift | unknown }
#   OUTPUTS: { DriftStatus enum member }
#   SIDE_EFFECTS: none
# END_CONTRACT: DriftStatus
class DriftStatus(StrEnum):
    MATCH = "match"
    DRIFT = "drift"
    UNKNOWN = "unknown"


# START_CONTRACT: FingerprintResult
#   PURPOSE: Immutable result of compute() — carries hash + structural metadata
#   INPUTS: { field values }
#   OUTPUTS: { FingerprintResult }
#   SIDE_EFFECTS: none
# END_CONTRACT: FingerprintResult
@dataclass(frozen=True)
class FingerprintResult:
    hash: str
    format: str
    has_styles_xml: bool
    byte_count: int


DOCX_STYLE_FILES: tuple[str, ...] = ("word/styles.xml", "word/numbering.xml")
DOCX_FALLBACK_FILES: tuple[str, ...] = ("word/document.xml",)
PPTX_STYLE_FILES: tuple[str, ...] = ("ppt/theme/theme1.xml",)
PPTX_FALLBACK_FILES: tuple[str, ...] = ("ppt/presentation.xml",)


def _detect_format(suffix: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Return (format, primary_style_members, fallback_members) for suffix."""
    if suffix == ".docx":
        return ("docx", DOCX_STYLE_FILES, DOCX_FALLBACK_FILES)
    if suffix == ".pptx":
        return ("pptx", PPTX_STYLE_FILES, PPTX_FALLBACK_FILES)
    raise FingerprintError(f"Unsupported format: {suffix}")


# START_CONTRACT: compute
#   PURPOSE: Read style-related XML members of an OOXML zip, SHA-256 hash
#     their concatenation in 64 KB chunks, return hex digest + metadata.
#   INPUTS: { document_path: Path — must exist; .docx or .pptx }
#   OUTPUTS: { FingerprintResult(hash, format, has_styles_xml, byte_count) }
#   SIDE_EFFECTS: reads filesystem (mode='r'); emits INFO log marker
#     BLOCK_FP_COMPUTE on success.
#   LINKS: V-MP-FINGERPRINT scenario-1..6
# END_CONTRACT: compute
def compute(document_path: Path) -> FingerprintResult:
    # START_BLOCK_FP_COMPUTE
    if not document_path.exists():
        raise FingerprintError(f"Document not found: {document_path}")

    suffix = document_path.suffix.lower()
    doc_format, style_files, fallback_files = _detect_format(suffix)

    found_members: list[str] = []
    byte_count = 0
    chunks_read = 0
    sha = hashlib.sha256()

    try:
        with zipfile.ZipFile(document_path, mode="r") as zf:
            members = set(zf.namelist())

            # First pass: primary style members in defined order.
            for name in style_files:
                if name in members:
                    chunks_read, byte_count = _hash_member(
                        zf, name, sha, chunks_read, byte_count
                    )
                    found_members.append(name)

            # Fallback pass: only if NO primary members matched (mirrors legacy).
            if not found_members:
                for name in fallback_files:
                    if name in members:
                        chunks_read, byte_count = _hash_member(
                            zf, name, sha, chunks_read, byte_count
                        )
                        found_members.append(name)
    except zipfile.BadZipFile as exc:
        raise FingerprintError(f"Not a valid ZIP: {exc}") from exc
    except FingerprintError:
        raise
    except OSError as exc:
        raise HashFailedError(f"Hash computation failed: {exc}") from exc

    if not found_members:
        raise MissingStyleXmlError(
            f"No style XML found in {document_path}: expected {style_files}"
        )

    try:
        hex_hash = sha.hexdigest()
    except Exception as exc:  # pragma: no cover — hashlib never fails at digest
        raise HashFailedError(f"Hash digest failed: {exc}") from exc

    has_styles_xml: bool
    if doc_format == "docx":
        has_styles_xml = "word/styles.xml" in found_members
    else:
        has_styles_xml = bool(found_members) and found_members[0] in style_files

    logger.info(
        f"[{_LOG_PREFIX}][compute][BLOCK_FP_COMPUTE] "
        f"format={doc_format} byte_count={byte_count} chunks_read={chunks_read}"
    )
    # END_BLOCK_FP_COMPUTE

    return FingerprintResult(
        hash=hex_hash,
        format=doc_format,
        has_styles_xml=has_styles_xml,
        byte_count=byte_count,
    )


def _hash_member(
    zf: zipfile.ZipFile,
    name: str,
    sha: hashlib._Hash,
    chunks_read: int,
    byte_count: int,
) -> tuple[int, int]:
    """Stream a single zip member through SHA-256 in _CHUNK_SIZE chunks.

    Returns updated (chunks_read, byte_count). Hash output is identical to
    sha.update(zf.read(name)) because SHA-256 is associative over update().
    """
    try:
        with zf.open(name, mode="r") as fp:
            while True:
                chunk = fp.read(_CHUNK_SIZE)
                if not chunk:
                    break
                sha.update(chunk)
                chunks_read += 1
                byte_count += len(chunk)
    except (zipfile.BadZipFile, OSError) as exc:
        raise HashFailedError(
            f"Hash computation failed reading {name}: {exc}"
        ) from exc
    return chunks_read, byte_count


# START_CONTRACT: compare
#   PURPOSE: Equality-compare two hex hashes, surface unknown when either side
#     is missing (None) — the Phase-16 rename of legacy BASELINE_MISSING.
#   INPUTS: { hash_a: str | None, hash_b: str | None }
#   OUTPUTS: { DriftStatus }
#   SIDE_EFFECTS: none
#   LINKS: V-MP-FINGERPRINT scenario-4
# END_CONTRACT: compare
def compare(hash_a: str | None, hash_b: str | None) -> DriftStatus:
    if hash_a is None or hash_b is None:
        return DriftStatus.UNKNOWN
    if hash_a == hash_b:
        return DriftStatus.MATCH
    return DriftStatus.DRIFT


# START_CONTRACT: fingerprint
#   PURPOSE: Public entry — coerce str/Path and delegate to compute().
#   INPUTS: { document_path: str | Path }
#   OUTPUTS: { FingerprintResult }
#   SIDE_EFFECTS: delegates to compute()
#   LINKS: V-MP-FINGERPRINT scenario-1, scenario-5
# END_CONTRACT: fingerprint
def fingerprint(document_path: str | Path) -> FingerprintResult:
    return compute(Path(document_path))
