# FILE: tests/unit/test_mp_grace.py
# START_MODULE_CONTRACT
#   PURPOSE: Verify MP-GRACE bootstrap/describe and manifest injection into .docx
#   SCOPE: 10 tests covering injection, read-back, error paths, vendor XML
#     preservation, corrupted ZIP handling, custom output_path, trace markers,
#     and manifest XML structure validation
#   DEPENDS: pytest, mint_python.grace, mint_python.core.document, lxml, zipfile
#   LINKS: docs/verification-plan.xml#V-MP-GRACE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_01_bootstrap_creates_valid_docx_with_customxml_part
#   test_02_bootstrap_rejects_nonexistent_file
#   test_03_describe_reads_back_injected_manifest
#   test_04_describe_on_file_without_grace_returns_none
#   test_05_describe_on_nonexistent_file_raises
#   test_06_bootstrap_preserves_existing_customxml_parts
#   test_07_bootstrap_handles_corrupted_zip
#   test_08_bootstrap_with_custom_output_path
#   test_09_trace_markers_inject_manifest_and_preserve_existing
#   test_10_verify_manifest_xml_structure
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-11-1 — initial test suite for MP-GRACE
# END_CHANGE_SUMMARY
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from mint_python.core.document import Document
from mint_python.core.section import Section
from mint_python.grace import (
    GRACE_NS,
    GRACEInjectionError,
    GRACEManifest,
    bootstrap,
    describe,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _build_minimal_docx(out_path: Path) -> Path:
    return (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("Body", level=1).add_paragraph("hello"))
        .save(out_path)
    )


def _docx_has_grace_part(path: Path) -> bool:
    with zipfile.ZipFile(path, "r") as zf:
        return any(
            n.startswith("grace/") and n.endswith(".xml") for n in zf.namelist()
        )


# ---------------------------------------------------------------------------
# test-01: bootstrap creates a valid .docx with customXml part
# ---------------------------------------------------------------------------

def test_01_bootstrap_creates_valid_docx_with_customxml_part(tmp_path: Path) -> None:
    in_path = tmp_path / "in.docx"
    _build_minimal_docx(in_path)

    manifest = bootstrap(in_path)

    assert isinstance(manifest, GRACEManifest)
    assert manifest.fingerprint != ""
    assert len(manifest.instructions) == 10
    assert manifest.xml_part_name.startswith("grace/")

    out_path = in_path.parent / f"{in_path.stem}_grace{in_path.suffix}"
    assert out_path.exists()
    assert _docx_has_grace_part(out_path)


# ---------------------------------------------------------------------------
# test-02: bootstrap rejects nonexistent file → GRACEInjectionError
# ---------------------------------------------------------------------------

def test_02_bootstrap_rejects_nonexistent_file() -> None:
    with pytest.raises(GRACEInjectionError, match="File not found"):
        bootstrap(Path("/nonexistent/grace_input.docx"))


# ---------------------------------------------------------------------------
# test-03: describe reads back the injected manifest
# ---------------------------------------------------------------------------

def test_03_describe_reads_back_injected_manifest(tmp_path: Path) -> None:
    in_path = tmp_path / "in.docx"
    _build_minimal_docx(in_path)

    bootstrap(in_path)
    out_path = in_path.parent / f"{in_path.stem}_grace{in_path.suffix}"
    manifest = describe(out_path)

    assert manifest is not None
    assert isinstance(manifest, GRACEManifest)
    assert len(manifest.instructions) == 10
    assert manifest.fingerprint != ""
    assert manifest.xml_part_name.startswith("grace/")
    assert manifest.document_structure["format"] == "docx"


# ---------------------------------------------------------------------------
# test-04: describe on file without GRACE → returns None
# ---------------------------------------------------------------------------

def test_04_describe_on_file_without_grace_returns_none(tmp_path: Path) -> None:
    in_path = tmp_path / "in.docx"
    _build_minimal_docx(in_path)

    result = describe(in_path)
    assert result is None


# ---------------------------------------------------------------------------
# test-05: describe on nonexistent file → GRACEInjectionError
# ---------------------------------------------------------------------------

def test_05_describe_on_nonexistent_file_raises() -> None:
    with pytest.raises(GRACEInjectionError, match="File not found"):
        describe(Path("/nonexistent/grace_input.docx"))


# ---------------------------------------------------------------------------
# test-06: bootstrap preserves existing customXml parts (multivendor)
# ---------------------------------------------------------------------------

def test_06_bootstrap_preserves_existing_customxml_parts(tmp_path: Path) -> None:
    in_path = tmp_path / "in.docx"
    _build_minimal_docx(in_path)

    # Inject a vendor Custom XML Part manually before GRACE bootstrap.
    vendor_part_name = "customXml/item1.xml"
    vendor_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<vendor:data xmlns:vendor="urn:vendor:ns">hello</vendor:data>'
    )

    # Build a new docx with the vendor part included.
    vendor_path = tmp_path / "with_vendor.docx"
    ct_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels"'
        ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/{vendor_part_name}" ContentType="application/xml"/>'
        "</Types>"
    )

    with zipfile.ZipFile(vendor_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
        with zipfile.ZipFile(in_path, "r") as zf_in:
            zf_out.writestr("[Content_Types].xml", ct_xml)
            for item in zf_in.infolist():
                buf = zf_in.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    continue
                zf_out.writestr(item, buf)
        zf_out.writestr(vendor_part_name, vendor_xml)

    bootstrap(vendor_path)

    out_path = vendor_path.parent / f"{vendor_path.stem}_grace{vendor_path.suffix}"
    assert out_path.exists()

    with zipfile.ZipFile(out_path, "r") as zf:
        names = zf.namelist()
        assert vendor_part_name in names, f"vendor part {vendor_part_name} missing"
        assert any(n.startswith("grace/") for n in names)


# ---------------------------------------------------------------------------
# test-07: bootstrap handles corrupted ZIP gracefully
# ---------------------------------------------------------------------------

def test_07_bootstrap_handles_corrupted_zip(tmp_path: Path) -> None:
    bad_path = tmp_path / "corrupted.docx"
    bad_path.write_text("not a zip file")

    with pytest.raises(GRACEInjectionError, match="Structure analysis failed"):
        bootstrap(bad_path)


# ---------------------------------------------------------------------------
# test-08: bootstrap with custom output_path
# ---------------------------------------------------------------------------

def test_08_bootstrap_with_custom_output_path(tmp_path: Path) -> None:
    in_path = tmp_path / "in.docx"
    _build_minimal_docx(in_path)

    custom_out = tmp_path / "custom_output.docx"
    manifest = bootstrap(in_path, output_path=custom_out)

    assert custom_out.exists()
    assert _docx_has_grace_part(custom_out)
    assert isinstance(manifest, GRACEManifest)


# ---------------------------------------------------------------------------
# test-09: Trace markers — BLOCK_INJECT_MANIFEST + BLOCK_PRESERVE_EXISTING
# ---------------------------------------------------------------------------

def test_09_trace_markers_inject_manifest_and_preserve_existing(
    tmp_path: Path, caplog_at_info, marker_counter
) -> None:
    in_path = tmp_path / "in.docx"
    _build_minimal_docx(in_path)

    bootstrap(in_path)

    counts = marker_counter(caplog_at_info)
    assert counts.get("BLOCK_INJECT_MANIFEST", 0) >= 1
    assert counts.get("BLOCK_PRESERVE_EXISTING", 0) >= 1

    inject_records = [
        r for r in caplog_at_info.records
        if "BLOCK_INJECT_MANIFEST" in r.getMessage()
    ]
    assert len(inject_records) >= 1
    assert "[MP-Grace]" in inject_records[0].getMessage()
    assert "[inject]" in inject_records[0].getMessage()

    preserve_records = [
        r for r in caplog_at_info.records
        if "BLOCK_PRESERVE_EXISTING" in r.getMessage()
    ]
    assert len(preserve_records) >= 1
    assert "[MP-Grace]" in preserve_records[0].getMessage()
    assert "[preserve]" in preserve_records[0].getMessage()


# ---------------------------------------------------------------------------
# test-10: Verify manifest XML structure
# ---------------------------------------------------------------------------

def test_10_verify_manifest_xml_structure(tmp_path: Path) -> None:
    in_path = tmp_path / "in.docx"
    _build_minimal_docx(in_path)

    bootstrap(in_path)
    out_path = in_path.parent / f"{in_path.stem}_grace{in_path.suffix}"

    with zipfile.ZipFile(out_path, "r") as zf:
        for name in zf.namelist():
            if name.startswith("grace/") and name.endswith(".xml"):
                xml_content = zf.read(name).decode("utf-8")
                root = ET.fromstring(xml_content)

                assert root.tag == f"{{{GRACE_NS}}}manifest"

                struct_elem = root.find(f"{{{GRACE_NS}}}documentStructure")
                assert struct_elem is not None
                children = list(struct_elem)
                assert len(children) >= 2  # format + parts at minimum

                fp_elem = root.find(f"{{{GRACE_NS}}}fingerprint")
                assert fp_elem is not None
                assert fp_elem.text is not None
                assert len(fp_elem.text) == 64  # SHA-256 hex

                instr_elem = root.find(f"{{{GRACE_NS}}}instructions")
                assert instr_elem is not None
                rules = list(instr_elem)
                assert len(rules) == 10
                for rule_elem in rules:
                    assert rule_elem.text is not None
                    assert len(rule_elem.text) > 0
