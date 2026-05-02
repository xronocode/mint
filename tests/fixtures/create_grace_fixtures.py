"""Create GRACE injection test fixtures.

Produces:
  - with_grace.docx      : minimal_valid.docx + GRACE manifest Custom XML Part
  - with_vendor_xml.docx : minimal_valid.docx + vendor namespace Custom XML Part

Run once (idempotent — overwrites existing output).
"""

import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

FIXTURES_DIR = Path(__file__).resolve().parent
SRC = FIXTURES_DIR / "minimal_valid.docx"

GRACE_NS = "urn:mint:grace:2026:manifest"
VENDOR_NS = "urn:vendor:metadata:2026"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

GRACE_RULES = [
    "Validate document structure before any edit operation.",
    "Preserve existing Custom XML Parts from other namespaces.",
    "Apply design tokens consistently across all document elements.",
    "Use fixed column widths in tables, never percentage-based widths.",
    "Use w:br for line breaks instead of raw newline characters.",
    "Maintain font embedding for all non-system fonts.",
    "Keep text within safe margins (0.5 inch from edges).",
    "Run validation after each edit to catch cascading issues.",
]

FAKE_FINGERPRINT = hashlib.sha256(b"fixture-deterministic").hexdigest()


def _read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _build_grace_manifest_xml() -> str:
    root = ET.Element(f"{{{GRACE_NS}}}manifest")
    root.set("xmlns:grace", GRACE_NS)

    struct = ET.SubElement(root, f"{{{GRACE_NS}}}documentStructure")
    for k, v in {"format": "docx", "parts": "4", "has_custom_xml": "False"}.items():
        child = ET.SubElement(struct, f"{{{GRACE_NS}}}{k}")
        child.text = v

    fp = ET.SubElement(root, f"{{{GRACE_NS}}}fingerprint")
    fp.text = FAKE_FINGERPRINT

    instrs = ET.SubElement(root, f"{{{GRACE_NS}}}instructions")
    for i, rule in enumerate(GRACE_RULES):
        r = ET.SubElement(instrs, f"{{{GRACE_NS}}}rule")
        r.set("index", str(i))
        r.text = rule

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _build_vendor_xml() -> str:
    root = ET.Element(f"{{{VENDOR_NS}}}vendorMetadata")
    root.set("xmlns:vm", VENDOR_NS)
    desc = ET.SubElement(root, f"{{{VENDOR_NS}}}description")
    desc.text = "Test vendor metadata for non-GRACE Custom XML Part"
    version = ET.SubElement(root, f"{{{VENDOR_NS}}}version")
    version.text = "1.0.0"
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _add_override(
    content_types_xml: str,
    part_name: str,
    content_type: str = "application/xml",
) -> str:
    root = ET.fromstring(content_types_xml)
    override = ET.SubElement(root, f"{{{CONTENT_TYPES_NS}}}Override")
    override.set("PartName", f"/{part_name}")
    override.set("ContentType", content_type)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _add_relationship(rels_xml: str, part_name: str, rel_id: str) -> str:
    root = ET.fromstring(rels_xml)
    rel = ET.SubElement(root, f"{{{REL_NS}}}Relationship")
    rel.set("Id", rel_id)
    rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml")
    rel.set("Target", f"/{part_name}")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def create_with_grace() -> Path:
    out = FIXTURES_DIR / "with_grace.docx"
    entries = _read_zip(SRC)

    manifest_xml = _build_grace_manifest_xml()
    part_name = "grace/manifest.xml"

    entries["[Content_Types].xml"] = _add_override(
        entries["[Content_Types].xml"].decode("utf-8"), part_name,
    ).encode("utf-8")
    entries["_rels/.rels"] = _add_relationship(
        entries["_rels/.rels"].decode("utf-8"), part_name, "rIdGraceFixture01",
    ).encode("utf-8")
    entries[part_name] = manifest_xml.encode("utf-8")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)

    return out


def create_with_vendor_xml() -> Path:
    out = FIXTURES_DIR / "with_vendor_xml.docx"
    entries = _read_zip(SRC)

    vendor_xml = _build_vendor_xml()
    part_name = "customXml/vendor_meta.xml"

    entries["[Content_Types].xml"] = _add_override(
        entries["[Content_Types].xml"].decode("utf-8"), part_name,
    ).encode("utf-8")
    entries["_rels/.rels"] = _add_relationship(
        entries["_rels/.rels"].decode("utf-8"), part_name, "rIdVendor01",
    ).encode("utf-8")
    entries[part_name] = vendor_xml.encode("utf-8")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)

    return out


if __name__ == "__main__":
    p1 = create_with_grace()
    p2 = create_with_vendor_xml()
    print(p1)
    print(p2)
