# FILE: src/mint/grace/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Inject GRACE manifest + instructions as Custom XML Parts into OOXML docs
#   SCOPE: GRACE namespace injection, manifest creation, vendor XML preservation
#   DEPENDS: M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-GRACE, docs/verification-plan.xml#V-M-GRACE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   GRACEManifest - manifest dataclass with structure and fingerprint
#   bootstrap - inject manifest + instructions into document
#   describe - read existing GRACE metadata from document
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from mint._security import compute_file_hash

logger = logging.getLogger(__name__)

GRACE_NS = "urn:mint:grace:2026:manifest"
GRACE_NS_PREFIX = "grace"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

GRACE_INSTRUCTIONS = [
    "Validate document structure before any edit operation.",
    "Preserve existing Custom XML Parts from other namespaces.",
    "Apply design tokens consistently across all document elements.",
    "Use fixed column widths in tables, never percentage-based widths.",
    "Use w:br for line breaks instead of raw newline characters.",
    "Maintain font embedding for all non-system fonts.",
    "Keep text within safe margins (0.5 inch from edges).",
    "Run validation after each edit to catch cascading issues.",
    "Compute fingerprint before and after edits to detect drift.",
    "Backup document before applying any destructive changes.",
]


class GRACEInjectionError(Exception):
    pass


@dataclass
class GRACEManifest:
    document_structure: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    instructions: list[str] = field(default_factory=list)
    namespace: str = GRACE_NS
    xml_part_name: str = ""


# START_BLOCK_INJECT_MANIFEST
def bootstrap(
    document_path: Path,
    rules: list[str] | None = None,
    output_path: Path | None = None,
) -> GRACEManifest:
    if not document_path.is_file():
        raise GRACEInjectionError(f"File not found: {document_path}")

    instructions = rules if rules else GRACE_INSTRUCTIONS[:10]

    try:
        structure = _analyze_structure(document_path)
    except Exception as e:
        raise GRACEInjectionError(f"Structure analysis failed: {e}") from e

    fingerprint = compute_file_hash(document_path)

    part_id = str(uuid.uuid4())
    part_name = f"grace/manifest_{part_id}.xml"

    manifest = GRACEManifest(
        document_structure=structure,
        fingerprint=fingerprint,
        instructions=instructions,
        xml_part_name=part_name,
    )

    manifest_xml = _build_manifest_xml(manifest)

    if output_path is None:
        output_path = document_path.parent / f"{document_path.stem}_grace{document_path.suffix}"

    _inject_custom_xml_part(document_path, output_path, part_name, manifest_xml)

    logger.info(
        "[GRACE][inject][BLOCK_INJECT_MANIFEST] "
        "Injected manifest: part=%s, fingerprint=%s",
        part_name,
        fingerprint[:16],
    )
    return manifest
# END_BLOCK_INJECT_MANIFEST


# START_BLOCK_PRESERVE_EXISTING
def describe(document_path: Path) -> GRACEManifest | None:
    if not document_path.is_file():
        raise GRACEInjectionError(f"File not found: {document_path}")

    try:
        with zipfile.ZipFile(document_path, "r") as zf:
            for name in zf.namelist():
                if name.startswith("grace/") and name.endswith(".xml"):
                    xml_content = zf.read(name).decode("utf-8")
                    return _parse_manifest_xml(xml_content, name)
    except (zipfile.BadZipFile, KeyError):
        pass

    return None
# END_BLOCK_PRESERVE_EXISTING


def _analyze_structure(path: Path) -> dict[str, Any]:
    structure: dict[str, Any] = {"format": path.suffix.lstrip(".")}
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        structure["parts"] = len(names)
        structure["has_custom_xml"] = any(n.startswith("customXml/") for n in names)
    return structure


def _build_manifest_xml(manifest: GRACEManifest) -> str:
    root = ET.Element(f"{{{GRACE_NS}}}manifest")
    root.set("xmlns:grace", GRACE_NS)

    struct_elem = ET.SubElement(root, f"{{{GRACE_NS}}}documentStructure")
    for key, value in manifest.document_structure.items():
        child = ET.SubElement(struct_elem, f"{{{GRACE_NS}}}{key}")
        child.text = str(value)

    fp_elem = ET.SubElement(root, f"{{{GRACE_NS}}}fingerprint")
    fp_elem.text = manifest.fingerprint

    instr_elem = ET.SubElement(root, f"{{{GRACE_NS}}}instructions")
    for i, instruction in enumerate(manifest.instructions):
        rule_elem = ET.SubElement(instr_elem, f"{{{GRACE_NS}}}rule")
        rule_elem.set("index", str(i))
        rule_elem.text = instruction

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _parse_manifest_xml(xml_content: str, part_name: str) -> GRACEManifest:
    root = ET.fromstring(xml_content)
    structure: dict[str, Any] = {}
    struct_elem = root.find(f"{{{GRACE_NS}}}documentStructure")
    if struct_elem is not None:
        for child in struct_elem:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            structure[tag] = child.text or ""

    fingerprint = ""
    fp_elem = root.find(f"{{{GRACE_NS}}}fingerprint")
    if fp_elem is not None:
        fingerprint = fp_elem.text or ""

    instructions: list[str] = []
    instr_elem = root.find(f"{{{GRACE_NS}}}instructions")
    if instr_elem is not None:
        for rule_elem in instr_elem:
            if rule_elem.text:
                instructions.append(rule_elem.text)

    return GRACEManifest(
        document_structure=structure,
        fingerprint=fingerprint,
        instructions=instructions,
        xml_part_name=part_name,
    )


def _inject_custom_xml_part(
    src_path: Path, output_path: Path, part_name: str, manifest_xml: str
) -> None:
    with tempfile.TemporaryDirectory(prefix="mint_grace_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        with zipfile.ZipFile(src_path, "r") as zf_in:
            from mint._security import validate_zip_paths

            validate_zip_paths(zf_in)
            zf_in.extractall(tmp_dir)

        grace_dir = tmp_dir / "grace"
        grace_dir.mkdir(exist_ok=True)
        (grace_dir / part_name.split("/")[-1]).write_text(
            manifest_xml, encoding="utf-8"
        )

        _update_content_types(tmp_dir, part_name)
        _update_relationships(tmp_dir, part_name)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
            for file_path in tmp_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(tmp_dir)
                    zf_out.write(file_path, str(arcname))

    logger.info(
        "[GRACE][preserve][BLOCK_PRESERVE_EXISTING] "
        "Preserved existing Custom XML parts during injection"
    )


def _update_content_types(tmp_dir: Path, part_name: str) -> None:
    ct_path = tmp_dir / "[Content_Types].xml"
    if not ct_path.exists():
        return

    try:
        tree = ET.parse(ct_path)
        root = tree.getroot()
    except ET.ParseError:
        return

    override = ET.SubElement(root, f"{{{CONTENT_TYPES_NS}}}Override")
    override.set("PartName", f"/{part_name}")
    override.set("ContentType", "application/xml")

    ET.ElementTree(root).write(ct_path, encoding="unicode", xml_declaration=True)


def _update_relationships(tmp_dir: Path, part_name: str) -> None:
    rels_path = tmp_dir / "_rels" / ".rels"
    if not rels_path.exists():
        rels_path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element(f"{{{REL_NS}}}Relationships")
    else:
        try:
            tree = ET.parse(rels_path)
            root = tree.getroot()
        except ET.ParseError:
            root = ET.Element(f"{{{REL_NS}}}Relationships")

    rel_id = f"rIdGrace{hashlib.md5(part_name.encode()).hexdigest()[:8]}"
    rel = ET.SubElement(root, f"{{{REL_NS}}}Relationship")
    rel.set("Id", rel_id)
    rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml")
    rel.set("Target", f"/{part_name}")

    ET.ElementTree(root).write(rels_path, encoding="unicode", xml_declaration=True)
