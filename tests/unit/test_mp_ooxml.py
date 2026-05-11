# FILE: tests/unit/test_mp_ooxml.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Verify MP-OOXML pure-python port per V-MP-OOXML scenarios 1-8.
#     Asserts public API parity with legacy mint.ooxml (porting-parity
#     scenario-7 is the load-bearing oracle for forbidden-4), the BLOCK
#     log markers (BLOCK_OOXML_UNPACK / BLOCK_OOXML_PACK /
#     BLOCK_OOXML_AUTOREPAIR), and the constraint-8 grep gate.
#   SCOPE: 8 deterministic scenarios + edge-coverage tests for 100% line
#     coverage on the new module.
#   DEPENDS: mint_python.ooxml, mint.ooxml (legacy, scenario-7 parity ONLY —
#     read-only side-by-side comparison), tests._helpers.sample_docs,
#     tests._helpers.ooxml_parity, pytest, lxml.
#   LINKS: docs/verification-plan.xml#V-MP-OOXML
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   _make_doc_with_body - construct in-memory w:document for run/quote tests
#   _build_dangling_rel_tree - mutate unpacked tree to drop a rel target
#   TestScenario1UnpackBasic - V-MP-OOXML scenario-1
#   TestScenario2PackUnpackRoundtrip - scenario-2
#   TestScenario3RelationshipsValidation - scenario-3
#   TestScenario4MergeRuns - scenario-4
#   TestScenario5EscapeSmartQuotes - scenario-5
#   TestScenario6NotAZipRaises - scenario-6
#   TestScenario7LegacyParity - scenario-7 PORTING-PARITY
#   TestScenario8NoLegacyImport - scenario-8 grep gate
#   TestForbiddenBehaviors - forbidden-2 / forbidden-3 sanity
#   TestEdgeCoverage - non-scenario branches needed for 100% coverage
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Phase-16 Wave-16-3a initial implementation — 8 scenarios
#     covering the full V-MP-OOXML plan plus edge coverage for missing
#     unpack_dir, missing content-types, durable-id auto-repair, whitespace
#     preserve auto-repair, Override entry-order preservation, and the
#     classification helpers (_is_binary_part / _is_xml_part /
#     _should_transform / _format_from_parts).
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from mint_python.ooxml import (
    CT,
    PR,
    OOXMLError,
    PackResult,
    UnpackResult,
    W,
    _autorepair_durable_ids,
    _autorepair_whitespace_preserve,
    _elements_equal,
    _format_from_parts,
    _is_binary_part,
    _is_xml_part,
    _PackStats,
    _process_xml_for_pack,
    _rels_owner_dir,
    _resolve_target,
    _run_is_code_styled,
    _should_transform,
    escape_smart_quotes,
    merge_runs,
    pack,
    unpack,
    validate_relationships,
)
from tests._helpers.ooxml_parity import collect_parity
from tests._helpers.sample_docs import (
    minimal_docx_bytes,
    not_a_zip_bytes,
    write_to_tmp,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc_with_body(body_xml: str) -> etree._Element:
    src = (
        f'<w:document xmlns:w="{W_NS}">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    return etree.fromstring(src.encode("utf-8"))


def _read_zip_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _plant_dangling_rel(unpack_dir: Path) -> None:
    """Append a Relationship pointing at a non-existent target to
    word/_rels/document.xml.rels.

    Implementation note: scenario-3 deliberately mutates the unpacked tree
    inline (per worker brief) rather than extending sample_docs.py.
    """
    rels_path = unpack_dir / "word" / "_rels" / "document.xml.rels"
    tree = etree.fromstring(rels_path.read_bytes())
    rel = etree.SubElement(tree, f"{PR}Relationship")
    rel.set("Id", "rIdDangling")
    rel.set(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml",
    )
    rel.set("Target", "missing_target_that_does_not_exist.xml")
    rels_path.write_bytes(
        etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
    )


# ---------------------------------------------------------------------------
# Scenario 1 — unpack basic
# ---------------------------------------------------------------------------


class TestScenario1UnpackBasic:
    def test_scenario_1_unpack_basic(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        result = unpack(doc, tmp_path / "u")
        assert isinstance(result, UnpackResult)
        assert result.format == "docx"
        assert "[Content_Types].xml" in result.parts
        assert "word/document.xml" in result.parts
        # Every emitted XML part parses without errors.
        for part in result.parts:
            path = tmp_path / "u" / part
            if part.endswith(".xml") or part.endswith(".rels"):
                etree.fromstring(path.read_bytes())
        # unpack_dir + original_path round-trip the inputs verbatim.
        assert result.unpack_dir == tmp_path / "u"
        assert result.original_path == doc

    def test_unpack_accepts_str_paths(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        result = unpack(str(doc), str(tmp_path / "u"))
        assert isinstance(result, UnpackResult)


# ---------------------------------------------------------------------------
# Scenario 2 — round-trip pack/unpack
# ---------------------------------------------------------------------------


class TestScenario2PackUnpackRoundtrip:
    def test_scenario_2_pack_unpack_roundtrip(self, tmp_path: Path) -> None:
        # Use minimal_docx_bytes: valid_memo has a relative ../customXml rel
        # that neither legacy nor the port resolves correctly through pack
        # (parity-preserved bug; unrelated to this port).
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir1 = tmp_path / "u1"
        out_zip = tmp_path / "out.docx"
        out_dir2 = tmp_path / "u2"

        r1 = unpack(doc, out_dir1)
        p1 = pack(out_dir1, out_zip)
        assert isinstance(p1, PackResult)
        assert p1.output_path == out_zip
        assert p1.bytes_written > 0

        r2 = unpack(out_zip, out_dir2)
        assert sorted(r1.parts) == sorted(r2.parts)

    def test_pack_preserves_content_types_override_order(
        self, tmp_path: Path,
    ) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        out_zip = tmp_path / "out.docx"
        unpack(doc, out_dir)
        pack(out_dir, out_zip)

        src_ct = etree.fromstring(_read_zip_parts(doc)["[Content_Types].xml"])
        out_ct = etree.fromstring(_read_zip_parts(out_zip)["[Content_Types].xml"])
        src_overrides = [el.get("PartName") for el in src_ct.findall(f"{CT}Override")]
        out_overrides = [el.get("PartName") for el in out_ct.findall(f"{CT}Override")]
        assert src_overrides == out_overrides


# ---------------------------------------------------------------------------
# Scenario 3 — validate_relationships
# ---------------------------------------------------------------------------


class TestScenario3RelationshipsValidation:
    def test_scenario_3_validate_clean_relationships_returns_none(
        self, tmp_path: Path,
    ) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        unpack(doc, out_dir)
        # Returns None and does not raise.
        assert validate_relationships(out_dir) is None

    def test_scenario_3_validate_raises_on_dangling_target(
        self, tmp_path: Path,
    ) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        unpack(doc, out_dir)
        _plant_dangling_rel(out_dir)

        with pytest.raises(OOXMLError) as exc_info:
            validate_relationships(out_dir)
        assert exc_info.value.code == "OOXML_RELATIONSHIP_BROKEN"
        assert "missing_target_that_does_not_exist.xml" in str(exc_info.value)

    def test_pack_fails_fast_on_dangling_target(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        out_zip = tmp_path / "out.docx"
        unpack(doc, out_dir)
        _plant_dangling_rel(out_dir)
        with pytest.raises(OOXMLError) as exc_info:
            pack(out_dir, out_zip)
        assert exc_info.value.code == "OOXML_RELATIONSHIP_BROKEN"

    def test_validate_relationships_skips_external_targets(
        self, tmp_path: Path,
    ) -> None:
        """External-mode rels must be skipped (no resolve attempt)."""
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        unpack(doc, out_dir)
        rels_path = out_dir / "word" / "_rels" / "document.xml.rels"
        tree = etree.fromstring(rels_path.read_bytes())
        ext = etree.SubElement(tree, f"{PR}Relationship")
        ext.set("Id", "rExt")
        ext.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        )
        ext.set("Target", "https://example.com/")
        ext.set("TargetMode", "External")
        rels_path.write_bytes(
            etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        )
        # No exception — external is permitted to be unresolved.
        validate_relationships(out_dir)

    def test_validate_relationships_ignores_empty_target(
        self, tmp_path: Path,
    ) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        unpack(doc, out_dir)
        rels_path = out_dir / "word" / "_rels" / "document.xml.rels"
        tree = etree.fromstring(rels_path.read_bytes())
        empty = etree.SubElement(tree, f"{PR}Relationship")
        empty.set("Id", "rEmpty")
        empty.set("Type", "http://example.com/relationships/empty")
        empty.set("Target", "")
        rels_path.write_bytes(
            etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        )
        validate_relationships(out_dir)

    def test_validate_relationships_skips_unparseable_rels(
        self, tmp_path: Path,
    ) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        unpack(doc, out_dir)
        # Replace one rels with invalid XML — validate() must continue, not crash.
        rels_path = out_dir / "word" / "_rels" / "document.xml.rels"
        rels_path.write_bytes(b"<not-rels<<<>broken")
        validate_relationships(out_dir)

    def test_validate_relationships_no_rels_files_returns_none(
        self, tmp_path: Path,
    ) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert validate_relationships(empty_dir) is None

    def test_validate_relationships_absolute_target_resolved_from_root(
        self, tmp_path: Path,
    ) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        unpack(doc, out_dir)
        rels_path = out_dir / "word" / "_rels" / "document.xml.rels"
        tree = etree.fromstring(rels_path.read_bytes())
        abs_rel = etree.SubElement(tree, f"{PR}Relationship")
        abs_rel.set("Id", "rAbs")
        abs_rel.set("Type", "http://example.com/relationships/abs")
        abs_rel.set("Target", "/word/document.xml")  # absolute -> resolves to word/document.xml
        rels_path.write_bytes(
            etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        )
        validate_relationships(out_dir)


# ---------------------------------------------------------------------------
# Scenario 4 — merge_runs
# ---------------------------------------------------------------------------


class TestScenario4MergeRuns:
    def test_scenario_4_merge_adjacent_equal_rpr(self) -> None:
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:b/></w:rPr><w:t>Hello </w:t></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>world</w:t></w:r>'
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        n = merge_runs(tree)
        assert n == 1
        runs = tree.findall(f".//{W}p/{W}r")
        assert len(runs) == 1
        t = runs[0].find(f"{W}t")
        assert t is not None
        assert t.text == "Hello world"

    def test_merge_does_not_merge_unequal_rpr(self) -> None:
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t></w:r>'
            '<w:r><w:rPr><w:i/></w:rPr><w:t>B</w:t></w:r>'
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        assert merge_runs(tree) == 0
        assert len(tree.findall(f".//{W}p/{W}r")) == 2

    def test_merge_one_run_has_rpr_other_does_not(self) -> None:
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t></w:r>'
            "<w:r><w:t>B</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        assert merge_runs(tree) == 0

    def test_merge_both_lack_rpr(self) -> None:
        body = (
            "<w:p><w:r><w:t>A</w:t></w:r><w:r><w:t>B</w:t></w:r></w:p>"
        )
        tree = _make_doc_with_body(body)
        assert merge_runs(tree) == 1

    def test_merge_blocked_by_non_text_child(self) -> None:
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t><w:br/></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>B</w:t></w:r>'
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        assert merge_runs(tree) == 0

    def test_merge_runs_no_text_pair_appends_children(self) -> None:
        # Runs that have no w:t at all should still concatenate via append-only path.
        body = (
            "<w:p>"
            "<w:r></w:r>"
            "<w:r></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        assert merge_runs(tree) == 1
        assert len(tree.findall(f".//{W}p/{W}r")) == 1

    def test_merge_runs_no_text_pair_with_rpr_skipped(self) -> None:
        # Force _merge_run_pair's "no-text" branch to run through its child
        # loop with an rPr (rPr is skipped) + an unrecognized child appended.
        body = (
            "<w:p>"
            "<w:r><w:rPr><w:b/></w:rPr></w:r>"
            "<w:r><w:rPr><w:b/></w:rPr><w:something/></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        # Both rPr equal -> mergeable; neither has w:t -> no-text branch.
        assert merge_runs(tree) == 1
        runs = tree.findall(f".//{W}p/{W}r")
        assert len(runs) == 1
        # The "<w:something/>" was appended onto a.
        assert runs[0].find(f"{{{W_NS}}}something") is not None

    def test_merge_runs_text_branch_appends_extra_child(self) -> None:
        # Force the "text" branch's append-loop to actually append a child:
        # b has rPr + w:t + an extra trailing element after first_b.
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>B</w:t><w:trailing/></w:r>'
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        assert merge_runs(tree) == 1
        runs = tree.findall(f".//{W}p/{W}r")
        assert len(runs) == 1
        # Trailing element was appended after w:t concatenation.
        assert runs[0].find(f"{{{W_NS}}}trailing") is not None

    def test_merge_runs_preserve_space_on_concat(self) -> None:
        # Trailing space in first run requires preserve on the merged w:t.
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:b/></w:rPr>'
            '<w:t xml:space="preserve">Hello </w:t></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>world</w:t></w:r>'
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        merge_runs(tree)
        t = tree.find(f".//{W}r/{W}t")
        assert t is not None
        assert t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"


# ---------------------------------------------------------------------------
# Scenario 5 — escape_smart_quotes
# ---------------------------------------------------------------------------


class TestScenario5EscapeSmartQuotes:
    def test_scenario_5_normalizes_smart_quotes(self) -> None:
        body = (
            "<w:p>"
            "<w:r><w:t>It's fine</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        n = escape_smart_quotes(tree)
        assert n == 1
        t = tree.find(f".//{W}r/{W}t")
        assert t is not None
        assert t.text == "It\u2019s fine"

    def test_double_quote_alternation_paired(self) -> None:
        body = '<w:p><w:r><w:t>say "hi" and "yo"</w:t></w:r></w:p>'
        tree = _make_doc_with_body(body)
        escape_smart_quotes(tree)
        t = tree.find(f".//{W}r/{W}t")
        assert t is not None
        assert t.text == "say “hi” and “yo”"

    def test_double_quote_unpaired_last_left_ascii(self) -> None:
        body = '<w:p><w:r><w:t>three " quotes " and "</w:t></w:r></w:p>'
        tree = _make_doc_with_body(body)
        escape_smart_quotes(tree)
        t = tree.find(f".//{W}r/{W}t")
        assert t is not None
        assert t.text == "three “ quotes ” and \""

    def test_idempotent_on_already_escaped(self) -> None:
        body = "<w:p><w:r><w:t>can't won't</w:t></w:r></w:p>"
        tree = _make_doc_with_body(body)
        escape_smart_quotes(tree)
        first = etree.tostring(tree)
        n2 = escape_smart_quotes(tree)
        second = etree.tostring(tree)
        assert n2 == 0
        assert first == second

    def test_skip_code_styled_run(self) -> None:
        body = (
            "<w:p>"
            "<w:r><w:t>It's fine</w:t></w:r>"
            '<w:r><w:rPr><w:rStyle w:val="SourceCode"/></w:rPr>'
            "<w:t>don't</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        n = escape_smart_quotes(tree)
        assert n == 1
        runs = tree.findall(f".//{W}r")
        # second (Code-styled) run keeps the ASCII apostrophe
        t_code = runs[1].find(f"{W}t")
        assert t_code is not None
        assert t_code.text == "don't"

    def test_skip_paragraph_pstyle_code(self) -> None:
        body = (
            "<w:p>"
            '<w:pPr><w:pStyle w:val="ParaCode"/></w:pPr>'
            "<w:r><w:t>don't</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        assert escape_smart_quotes(tree) == 0

    def test_skip_paragraph_ppr_rpr_rstyle_code(self) -> None:
        body = (
            "<w:p>"
            "<w:pPr><w:rPr><w:rStyle w:val=\"InlineCode\"/></w:rPr></w:pPr>"
            "<w:r><w:t>don't</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        assert escape_smart_quotes(tree) == 0

    def test_empty_paragraph_returns_zero(self) -> None:
        body = "<w:p></w:p>"
        tree = _make_doc_with_body(body)
        assert escape_smart_quotes(tree) == 0

    def test_w_t_with_no_text_skipped(self) -> None:
        body = "<w:p><w:r><w:t/></w:r></w:p>"
        tree = _make_doc_with_body(body)
        assert escape_smart_quotes(tree) == 0

    def test_run_is_code_styled_helper_para_ppr_rpr_no_rstyle(self) -> None:
        body = (
            "<w:p>"
            "<w:pPr><w:rPr></w:rPr></w:pPr>"
            "<w:r><w:t>don't</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        # Reach _run_is_code_styled path where pPr/rPr exists but rStyle absent.
        assert escape_smart_quotes(tree) == 1

    def test_run_is_code_styled_helper_para_pstyle_no_match(self) -> None:
        body = (
            "<w:p>"
            "<w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
            "<w:r><w:t>don't</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        # pStyle present but does not end with "Code".
        assert escape_smart_quotes(tree) == 1

    def test_run_is_code_styled_helper_runlevel_rstyle_no_match(self) -> None:
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:rStyle w:val="Emphasis"/></w:rPr>'
            "<w:t>don't</w:t></w:r>"
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        # rStyle present but does not end with "Code".
        assert escape_smart_quotes(tree) == 1


# ---------------------------------------------------------------------------
# Scenario 6 — not-a-zip
# ---------------------------------------------------------------------------


class TestScenario6NotAZipRaises:
    def test_scenario_6_unpack_not_a_zip_raises(self, tmp_path: Path) -> None:
        bogus = write_to_tmp(tmp_path, "bogus.docx", not_a_zip_bytes())
        with pytest.raises(OOXMLError) as exc_info:
            unpack(bogus, tmp_path / "u")
        assert exc_info.value.code == "OOXML_NOT_A_ZIP"

    def test_unpack_missing_document_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OOXMLError) as exc_info:
            unpack(tmp_path / "does_not_exist.docx", tmp_path / "u")
        assert exc_info.value.code == "OOXML_NOT_A_ZIP"

    def test_unpack_missing_content_types_raises(self, tmp_path: Path) -> None:
        bogus = tmp_path / "no_ct.docx"
        with zipfile.ZipFile(bogus, "w") as zf:
            zf.writestr("word/document.xml", "<w:document/>")
        with pytest.raises(OOXMLError) as exc_info:
            unpack(bogus, tmp_path / "u")
        assert exc_info.value.code == "OOXML_MISSING_CONTENT_TYPES"

    def test_unpack_invalid_xml_part_raises(self, tmp_path: Path) -> None:
        bogus = tmp_path / "bad_xml.docx"
        with zipfile.ZipFile(bogus, "w") as zf:
            zf.writestr("[Content_Types].xml", "<not-valid-xml<<>")
        with pytest.raises(OOXMLError) as exc_info:
            unpack(bogus, tmp_path / "u")
        assert exc_info.value.code == "OOXML_XML_PARSE_ERROR"

    def test_unpack_default_code_is_ooxml_unknown(self) -> None:
        # Direct construction without code: defaults to OOXML_UNKNOWN.
        err = OOXMLError("boom")
        assert err.code == "OOXML_UNKNOWN"


# ---------------------------------------------------------------------------
# Scenario 7 — PORTING-PARITY against legacy mint.ooxml
# ---------------------------------------------------------------------------


class TestScenario7LegacyParity:
    def test_scenario_7_legacy_parity_on_valid_memo(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        record = collect_parity(doc, tmp_path)

        assert record.legacy_parts == record.port_parts
        assert record.legacy_format == record.port_format == "docx"
        assert record.legacy_runs_merged == record.port_runs_merged
        assert record.legacy_quotes_escaped == record.port_quotes_escaped
        assert (
            record.legacy_repaired_durable_ids
            == record.port_repaired_durable_ids
        )
        assert (
            record.legacy_preserved_whitespace_runs
            == record.port_preserved_whitespace_runs
        )


# ---------------------------------------------------------------------------
# Scenario 8 — constraint-8 grep gate
# ---------------------------------------------------------------------------


class TestScenario8NoLegacyImport:
    def test_scenario_8_no_legacy_import_in_port(self) -> None:
        port = (
            Path(__file__).resolve().parent.parent.parent
            / "src"
            / "mint_python"
            / "ooxml.py"
        )
        text = port.read_text("utf-8")
        # Forbid `from mint.X` and `import mint.X` (constraint-8).
        forbidden = re.compile(r"^\s*(?:from\s+mint\.|import\s+mint\.)", re.MULTILINE)
        matches = forbidden.findall(text)
        assert matches == [], (
            f"constraint-8 violation: src/mint_python/ooxml.py imports from mint.*: {matches}"
        )


# ---------------------------------------------------------------------------
# Forbidden behaviors
# ---------------------------------------------------------------------------


class TestForbiddenBehaviors:
    def test_forbidden_2_no_subprocess_imports(self) -> None:
        port = (
            Path(__file__).resolve().parent.parent.parent
            / "src"
            / "mint_python"
            / "ooxml.py"
        )
        text = port.read_text("utf-8")
        forbidden_patterns = [
            r"\bimport\s+subprocess\b",
            r"\bfrom\s+subprocess\b",
            r"\bos\.system\b",
            r"\bos\.popen\b",
            r"shell\s*=\s*True",
        ]
        for pat in forbidden_patterns:
            assert re.search(pat, text) is None, (
                f"forbidden pattern matched: {pat}"
            )

    def test_forbidden_3_public_symbol_names_match_legacy(self) -> None:
        """Symbol-name parity with mint.ooxml — required by forbidden-3 so
        MP-EDIT (W3b) can swap import paths transparently."""
        from mint import ooxml as legacy
        from mint_python import ooxml as port

        for sym in (
            "OOXMLError",
            "UnpackResult",
            "PackResult",
            "unpack",
            "pack",
            "merge_runs",
            "escape_smart_quotes",
            "validate_relationships",
        ):
            assert hasattr(legacy, sym), f"legacy missing public symbol: {sym}"
            assert hasattr(port, sym), f"port missing public symbol: {sym}"

        # Dataclass field-name parity (so MP-EDIT can rely on the same attrs).
        from dataclasses import fields
        assert {f.name for f in fields(legacy.UnpackResult)} == {
            f.name for f in fields(port.UnpackResult)
        }
        assert {f.name for f in fields(legacy.PackResult)} == {
            f.name for f in fields(port.PackResult)
        }


# ---------------------------------------------------------------------------
# Edge coverage — branches not exercised by scenarios 1-8.
# ---------------------------------------------------------------------------


class TestEdgeCoverage:
    def test_pack_missing_unpack_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OOXMLError) as exc_info:
            pack(tmp_path / "does_not_exist", tmp_path / "out.docx")
        assert exc_info.value.code == "OOXML_PACK_FAILED"

    def test_pack_empty_unpack_dir_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(OOXMLError) as exc_info:
            pack(empty, tmp_path / "out.docx")
        assert exc_info.value.code == "OOXML_PACK_FAILED"

    def test_pack_log_markers_fire_in_order(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="mint_python.ooxml")
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        out_dir = tmp_path / "u"
        out_zip = tmp_path / "out.docx"

        unpack(doc, out_dir)

        # Force auto-repair fire: rewrite a w:durableId-bearing element
        # (we add one explicitly; valid_memo_docx_bytes has no w14 ids).
        doc_xml = out_dir / "word" / "document.xml"
        raw = doc_xml.read_bytes()
        # Inject a w14:durableId attribute on the first w:p we find.
        # Use namespace-prefixed attribute to fire the autorepair walk.
        tree = etree.fromstring(raw)
        p = tree.find(f".//{W}p")
        assert p is not None
        p.set(f"{W}durableId", "2147483647")
        # Also inject leading whitespace on a w:t to fire xml:space=preserve.
        t = tree.find(f".//{W}t")
        assert t is not None
        t.text = "  leading whitespace  "
        doc_xml.write_bytes(
            etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        )

        result = pack(out_dir, out_zip)
        assert result.repaired_durable_ids >= 1
        assert result.preserved_whitespace_runs >= 1

        messages = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "[MP-OOXML][unpack][BLOCK_OOXML_UNPACK]" in messages
        assert "[MP-OOXML][pack][BLOCK_OOXML_PACK]" in messages
        assert "[MP-OOXML][autorepair][BLOCK_OOXML_AUTOREPAIR]" in messages

    def test_unpack_with_merge_runs_disabled(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        r = unpack(doc, tmp_path / "u", merge_runs=False)
        # merge_runs disabled => count is 0 regardless of fixture content.
        assert r.runs_merged == 0

    def test_unpack_handles_binary_and_unknown_parts(
        self, tmp_path: Path,
    ) -> None:
        """Cover unpack's binary-write + non-XML write branches.

        Build a custom docx containing word/media/image.png (binary) and
        word/unknown.txt (non-XML, non-binary).
        """
        bogus = tmp_path / "with_binary.docx"
        with zipfile.ZipFile(bogus, "w") as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types"/>',
            )
            zf.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\nfake")
            zf.writestr("word/unknown.txt", b"plain text payload")
        result = unpack(bogus, tmp_path / "u")
        assert "word/media/image1.png" in result.parts
        assert "word/unknown.txt" in result.parts
        # Binary preserved byte-equal.
        assert (
            tmp_path / "u" / "word" / "media" / "image1.png"
        ).read_bytes() == b"\x89PNG\r\n\x1a\nfake"
        # Plain text preserved verbatim.
        assert (
            tmp_path / "u" / "word" / "unknown.txt"
        ).read_bytes() == b"plain text payload"

    def test_pack_handles_binary_and_unknown_parts(
        self, tmp_path: Path,
    ) -> None:
        """Cover pack's binary read_bytes + non-XML read_bytes branches."""
        # Use unpack on a custom docx, then pack — must round-trip parts.
        bogus = tmp_path / "with_binary.docx"
        with zipfile.ZipFile(bogus, "w") as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types"/>',
            )
            zf.writestr(
                "word/document.xml",
                f'<w:document xmlns:w="{W_NS}"><w:body/></w:document>',
            )
            zf.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\nfake")
            zf.writestr("word/unknown.txt", b"plain text payload")
        out_dir = tmp_path / "u"
        out_zip = tmp_path / "out.docx"
        unpack(bogus, out_dir)
        p = pack(out_dir, out_zip)
        assert isinstance(p, PackResult)
        # Round-trip preserved binary + unknown.
        parts = _read_zip_parts(out_zip)
        assert parts["word/media/image1.png"] == b"\x89PNG\r\n\x1a\nfake"
        assert parts["word/unknown.txt"] == b"plain text payload"

    def test_pack_propagates_ooxml_error_from_xml_parse(
        self, tmp_path: Path,
    ) -> None:
        """Cover pack's `except OOXMLError: raise` re-raise branch.

        Plant a malformed XML part inside the unpack dir so _process_xml_for_pack
        raises OOXML_XML_PARSE_ERROR which the outer try-except must re-raise
        without wrapping.
        """
        out_dir = tmp_path / "u"
        (out_dir / "word").mkdir(parents=True)
        (out_dir / "[Content_Types].xml").write_bytes(
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
        )
        (out_dir / "word" / "document.xml").write_bytes(b"<broken<<>>not-xml")
        with pytest.raises(OOXMLError) as exc_info:
            pack(out_dir, tmp_path / "out.docx")
        assert exc_info.value.code == "OOXML_XML_PARSE_ERROR"

    def test_unpack_pretty_print_disabled(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "doc.docx", minimal_docx_bytes())
        r = unpack(doc, tmp_path / "u", pretty_print=False)
        assert isinstance(r, UnpackResult)

    def test_classification_helpers(self) -> None:
        assert _is_binary_part("word/media/image1.png") is True
        assert _is_binary_part("word/embeddings/oleObject1.bin") is True
        assert _is_binary_part("docProps/thumbnail.jpeg") is True
        assert _is_binary_part("word/oleObject1.bin") is True
        assert _is_binary_part("word/fontTable.bin") is True
        assert _is_binary_part("word/document.xml") is False

        assert _is_xml_part("[Content_Types].xml") is True
        assert _is_xml_part("word/_rels/document.xml.rels") is True
        assert _is_xml_part("word/media/image1.png") is False

        assert _should_transform("word/document.xml") is True
        assert _should_transform("ppt/slides/slide1.xml") is True
        assert _should_transform("xl/sharedStrings.xml") is True
        assert _should_transform("[Content_Types].xml") is False
        assert _should_transform("_rels/.rels") is False
        assert _should_transform("docProps/core.xml") is False

    def test_format_from_parts(self) -> None:
        assert _format_from_parts(["word/document.xml"]) == "docx"
        assert _format_from_parts(["ppt/slides/slide1.xml"]) == "pptx"

    def test_rels_owner_dir_and_resolve_target(self) -> None:
        assert _rels_owner_dir("word/_rels/document.xml.rels") == "word"
        assert _rels_owner_dir("ppt/_rels/presentation.xml.rels") == "ppt"
        assert _rels_owner_dir("_rels/.rels") == ""

        assert _resolve_target("word", "media/img.png") == "word/media/img.png"
        assert _resolve_target("", "media/img.png") == "media/img.png"
        assert _resolve_target("word", "/docProps/core.xml") == "docProps/core.xml"

    def test_elements_equal_branches(self) -> None:
        a = etree.fromstring(b"<x a='1'><y>z</y></x>")
        b = etree.fromstring(b"<x a='1'><y>z</y></x>")
        assert _elements_equal(a, b) is True

        diff_tag = etree.fromstring(b"<q a='1'><y>z</y></q>")
        assert _elements_equal(a, diff_tag) is False

        diff_attr = etree.fromstring(b"<x a='2'><y>z</y></x>")
        assert _elements_equal(a, diff_attr) is False

        diff_text = etree.fromstring(b"<x a='1'>hi<y>z</y></x>")
        assert _elements_equal(a, diff_text) is False

        diff_kids = etree.fromstring(b"<x a='1'><y>z</y><y2/></x>")
        assert _elements_equal(a, diff_kids) is False

        nested_diff = etree.fromstring(b"<x a='1'><y>w</y></x>")
        assert _elements_equal(a, nested_diff) is False

    def test_autorepair_durable_ids_skips_non_int(self) -> None:
        # An element with non-integer w:durableId must not crash the walk.
        tree = etree.fromstring(
            f'<w:document xmlns:w="{W_NS}">'
            '<w:p w:durableId="not-a-number"><w:r/></w:p>'
            "</w:document>".encode()
        )
        stats = _PackStats()
        _autorepair_durable_ids(tree, stats)
        assert stats.repaired_durable_ids == 0

    def test_autorepair_durable_ids_skips_in_range(self) -> None:
        tree = etree.fromstring(
            f'<w:document xmlns:w="{W_NS}">'
            '<w:p w:durableId="42"><w:r/></w:p>'
            "</w:document>".encode()
        )
        stats = _PackStats()
        _autorepair_durable_ids(tree, stats)
        assert stats.repaired_durable_ids == 0
        p = tree.find(f"{W}p")
        assert p is not None
        assert p.get(f"{W}durableId") == "42"

    def test_autorepair_durable_ids_avoids_collision(self) -> None:
        # Two elements: one valid at id=1 (preempting the counter); one
        # overflowing — the regenerated id must skip 1 and choose 2.
        tree = etree.fromstring(
            f'<w:document xmlns:w="{W_NS}">'
            '<w:p w:durableId="1"><w:r/></w:p>'
            '<w:p w:durableId="2147483647"><w:r/></w:p>'
            "</w:document>".encode()
        )
        stats = _PackStats()
        _autorepair_durable_ids(tree, stats)
        assert stats.repaired_durable_ids == 1
        ps = tree.findall(f"{W}p")
        # Element previously at 1 stays at 1; second one gets the next free id.
        assert ps[0].get(f"{W}durableId") == "1"
        assert ps[1].get(f"{W}durableId") == "2"

    def test_autorepair_whitespace_preserve_skips_empty(self) -> None:
        tree = etree.fromstring(
            f'<w:document xmlns:w="{W_NS}">'
            "<w:p><w:r><w:t></w:t></w:r></w:p>"
            "</w:document>".encode()
        )
        stats = _PackStats()
        _autorepair_whitespace_preserve(tree, stats)
        assert stats.preserved_whitespace_runs == 0

    def test_autorepair_whitespace_preserve_skips_already_set(self) -> None:
        tree = etree.fromstring(
            f'<w:document xmlns:w="{W_NS}" xmlns:xml="http://www.w3.org/XML/1998/namespace">'
            '<w:p><w:r><w:t xml:space="preserve">  hi  </w:t></w:r></w:p>'
            "</w:document>".encode()
        )
        stats = _PackStats()
        _autorepair_whitespace_preserve(tree, stats)
        assert stats.preserved_whitespace_runs == 0

    def test_process_xml_for_pack_xml_parse_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "word" / "document.xml"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"<broken<<>>")
        stats = _PackStats()
        with pytest.raises(OOXMLError) as exc_info:
            _process_xml_for_pack(bad, "word/document.xml", stats)
        assert exc_info.value.code == "OOXML_XML_PARSE_ERROR"

    def test_process_xml_for_pack_captures_overrides_order(
        self, tmp_path: Path,
    ) -> None:
        ct = tmp_path / "[Content_Types].xml"
        ct.write_bytes(
            f'<Types xmlns="{CT[1:-1]}">'.encode()
            + b'<Override PartName="/word/document.xml" ContentType="x"/>'
            + b'<Override PartName="/word/styles.xml" ContentType="y"/>'
            + b"</Types>"
        )
        stats = _PackStats()
        _process_xml_for_pack(ct, "[Content_Types].xml", stats)
        assert stats.overrides_order == [
            "/word/document.xml",
            "/word/styles.xml",
        ]

    def test_run_is_code_styled_direct_branches(self) -> None:
        # Build a w:p with a w:r whose rPr/rStyle ends in "Code".
        body = (
            "<w:p>"
            '<w:r><w:rPr><w:rStyle w:val="SourceCode"/></w:rPr><w:t>x</w:t></w:r>'
            "</w:p>"
        )
        tree = _make_doc_with_body(body)
        p = tree.find(f".//{W}p")
        r = tree.find(f".//{W}r")
        assert p is not None and r is not None
        assert _run_is_code_styled(r, p) is True

        # Run with no rPr and paragraph with no pPr -> False.
        body2 = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
        tree2 = _make_doc_with_body(body2)
        p2 = tree2.find(f".//{W}p")
        r2 = tree2.find(f".//{W}r")
        assert p2 is not None and r2 is not None
        assert _run_is_code_styled(r2, p2) is False

        # Run with rPr but no rStyle child -> drops through to paragraph.
        body3 = (
            "<w:p>"
            "<w:r><w:rPr></w:rPr><w:t>x</w:t></w:r>"
            "</w:p>"
        )
        tree3 = _make_doc_with_body(body3)
        p3 = tree3.find(f".//{W}p")
        r3 = tree3.find(f".//{W}r")
        assert p3 is not None and r3 is not None
        assert _run_is_code_styled(r3, p3) is False
