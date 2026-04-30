# FILE: src/mint/fingerprint.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Compute SHA256 hash of structural style XML for drift detection
#   SCOPE: Extract style XML from ZIP, concatenate, hash, compare
#   DEPENDS: M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-FINGERPRINT, docs/verification-plan.xml#V-M-FINGERPRINT
# END_MODULE_CONTRACT

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class FingerprintError(Exception):
    """Base fingerprint error."""


class MissingStyleXmlError(FingerprintError):
    """Required style XML not found in document."""


class HashFailedError(FingerprintError):
    """Hash computation failed."""


class DriftStatus(StrEnum):
    MATCH = "match"
    DRIFT = "drift"
    BASELINE_MISSING = "baseline_missing"


@dataclass(frozen=True)
class FingerprintResult:
    hash: str
    document_path: Path
    format: str
    xml_sources: list[str]


DOCX_STYLE_FILES = ("word/styles.xml", "word/numbering.xml")
DOCX_FALLBACK_FILES = ("word/document.xml",)
PPTX_STYLE_FILES = ("ppt/theme/theme1.xml",)
PPTX_FALLBACK_FILES = ("ppt/presentation.xml",)


# START_CONTRACT: compute
#   PURPOSE: Extract relevant XML from ZIP, concatenate, SHA256
#   INPUTS: { document_path: Path }
#   OUTPUTS: { FingerprintResult }
#   SIDE_EFFECTS: reads filesystem
# END_CONTRACT: compute
def compute(document_path: Path) -> FingerprintResult:
    if not document_path.exists():
        raise FingerprintError(f"Document not found: {document_path}")

    suffix = document_path.suffix.lower()
    if suffix == ".docx":
        style_files = DOCX_STYLE_FILES
        doc_format = "docx"
    elif suffix == ".pptx":
        style_files = PPTX_STYLE_FILES
        doc_format = "pptx"
    else:
        raise FingerprintError(f"Unsupported format: {suffix}")

    parts: list[bytes] = []
    found_sources: list[str] = []

    try:
        with zipfile.ZipFile(document_path) as z:
            for name in style_files:
                try:
                    parts.append(z.read(name))
                    found_sources.append(name)
                except KeyError:
                    pass
            if not parts:
                fallback = (
                    DOCX_FALLBACK_FILES if doc_format == "docx" else PPTX_FALLBACK_FILES
                )
                for name in fallback:
                    try:
                        parts.append(z.read(name))
                        found_sources.append(name)
                    except KeyError:
                        pass
    except zipfile.BadZipFile as exc:
        raise FingerprintError(f"Not a valid ZIP: {exc}") from exc

    if not parts:
        raise MissingStyleXmlError(
            f"No style XML found in {document_path}: "
            f"expected {style_files}"
        )

    try:
        h = hashlib.sha256()
        for part in parts:
            h.update(part)
        hex_hash = h.hexdigest()
    except Exception as exc:
        raise HashFailedError(f"Hash computation failed: {exc}") from exc

    return FingerprintResult(
        hash=hex_hash,
        document_path=document_path,
        format=doc_format,
        xml_sources=found_sources,
    )


# START_CONTRACT: compare
#   PURPOSE: Compare two hex hashes, return DriftStatus
#   INPUTS: { hash_a: str | None, hash_b: str | None }
#   OUTPUTS: { DriftStatus }
#   SIDE_EFFECTS: none
# END_CONTRACT: compare
def compare(hash_a: str | None, hash_b: str | None) -> DriftStatus:
    if hash_a is None or hash_b is None:
        return DriftStatus.BASELINE_MISSING
    if hash_a == hash_b:
        return DriftStatus.MATCH
    return DriftStatus.DRIFT


def fingerprint(document_path: str | Path) -> FingerprintResult:
    return compute(Path(document_path))
