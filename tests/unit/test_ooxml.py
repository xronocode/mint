"""Tests for src/mint/ooxml.py (Phase-5 Wave-5-1).

One test per V-M-OOXML scenario plus the Wave-5-1 evidence checks (trace and
static-imports). All tests are deterministic.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from mint.ooxml import (
    OOXMLError,
    PackResult,
    UnpackResult,
    escape_smart_quotes,
    merge_runs,
    pack,
    unpack,
    validate_relationships,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CT = f"{{{CT_NS}}}"

ROUND_TRIP_FIXTURES = (
    "minimal_valid.docx",
    "with_grace.docx",
    "with_vendor_xml.docx",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trees_equal(a: etree._Element, b: etree._Element) -> bool:
    if etree.QName(a).localname != etree.QName(b).localname:
        return False
    if etree.QName(a).namespace != etree.QName(b).namespace:
        return False
    if dict(a.attrib) != dict(b.attrib):
        return False
    if (a.text or "").strip() != (b.text or "").strip():
        return False
    a_kids = list(a)
    b_kids = list(b)
    if len(a_kids) != len(b_kids):
        return False
    return all(
        _trees_equal(ca, cb) for ca, cb in zip(a_kids, b_kids, strict=True)
    )


def _read_zip_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _make_doc_with_body(body_xml: str, *, w14: bool = False) -> etree._Element:
    extra_ns = ' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"' if w14 else ""
    src = (
        f'<w:document xmlns:w="{W_NS}"{extra_ns}>'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    return etree.fromstring(src.encode("utf-8"))


# ---------------------------------------------------------------------------
# Scenario 1
# ---------------------------------------------------------------------------


def test_scenario_1_unpack_minimal_valid(tmp_path: Path) -> None:
    result = unpack(FIXTURES / "minimal_valid.docx", tmp_path / "u")
    assert isinstance(result, UnpackResult)
    assert "[Content_Types].xml" in result.parts
    assert "word/document.xml" in result.parts
    # Every XML file parses without errors.
    for part in result.parts:
        path = tmp_path / "u" / part
        if part.endswith(".xml") or part.endswith(".rels"):
            etree.fromstring(path.read_bytes())


# ---------------------------------------------------------------------------
# Scenario 2 - round-trip tree-equal / byte-equal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ROUND_TRIP_FIXTURES)
def test_scenario_2_round_trip_tree_equal(
    tmp_path: Path, fixture: str,
) -> None:
    src = FIXTURES / fixture
    out_dir = tmp_path / "u1"
    out_zip = tmp_path / "out.docx"

    r1 = unpack(src, out_dir)
    p1 = pack(out_dir, out_zip)
    assert isinstance(p1, PackResult)

    # Re-unpack to confirm parts list identical and trees equal.
    out_dir2 = tmp_path / "u2"
    r2 = unpack(out_zip, out_dir2)

    assert sorted(r1.parts) == sorted(r2.parts)

    src_parts = _read_zip_parts(src)
    out_parts = _read_zip_parts(out_zip)

    for name in src_parts:
        if name.endswith(".xml") or name.endswith(".rels"):
            tree_a = etree.fromstring(src_parts[name])
            tree_b = etree.fromstring(out_parts[name])
            assert _trees_equal(tree_a, tree_b), f"tree differs in {name}"
        else:
            assert src_parts[name] == out_parts[name], (
                f"binary part differs in {name}"
            )


# ---------------------------------------------------------------------------
# Scenario 3 - merge_runs + boundary list
# ---------------------------------------------------------------------------


def test_scenario_3_merge_runs_collapses_adjacent_equal_rpr() -> None:
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
    text = runs[0].find(f"{W}t").text
    assert text == "Hello world"


def test_scenario_3_merge_runs_does_not_merge_unequal_rpr() -> None:
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t></w:r>'
        '<w:r><w:rPr><w:i/></w:rPr><w:t>B</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    n = merge_runs(tree)
    assert n == 0
    assert len(tree.findall(f".//{W}p/{W}r")) == 2


@pytest.mark.parametrize(
    "boundary_xml",
    [
        '<w:hyperlink xmlns:w="urn:placeholder"/>',
        "<w:bookmarkStart/>",
        "<w:bookmarkEnd/>",
        "<w:commentRangeStart/>",
        "<w:commentRangeEnd/>",
        "<w:commentReference/>",
        "<w:ins/>",
        "<w:del/>",
    ],
)
def test_scenario_3_merge_runs_never_crosses_boundary(
    boundary_xml: str,
) -> None:
    boundary_local = boundary_xml.split("<w:")[1].split("/")[0].split(" ")[0]
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t></w:r>'
        f"<w:{boundary_local}/>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t>B</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    n = merge_runs(tree)
    assert n == 0, f"unexpectedly merged across {boundary_local}"
    assert len(tree.findall(f".//{W}p/{W}r")) == 2


def test_scenario_3_merge_runs_blocked_by_internal_br() -> None:
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t><w:br/></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>B</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    n = merge_runs(tree)
    assert n == 0


def test_scenario_3_merge_runs_blocked_by_internal_tab() -> None:
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t>A</w:t><w:tab/></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>B</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    assert merge_runs(tree) == 0


def test_scenario_3_merge_runs_blocked_by_internal_sym() -> None:
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/></w:rPr><w:sym w:font="Symbol" w:char="00B0"/><w:t>A</w:t></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>B</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    assert merge_runs(tree) == 0


def test_scenario_3_merge_runs_blocked_by_internal_fldchar() -> None:
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>x</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    assert merge_runs(tree) == 0


# ---------------------------------------------------------------------------
# Scenario 4 - apostrophes + Code-style skip + idempotency
# ---------------------------------------------------------------------------


def test_scenario_4_apostrophes_become_smart_apos_skipping_code_style() -> None:
    body = (
        "<w:p>"
        "<w:r><w:t>It's fine</w:t></w:r>"
        '<w:r><w:rPr><w:rStyle w:val="SourceCode"/></w:rPr>'
        "<w:t>don't</w:t></w:r>"
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    n = escape_smart_quotes(tree)
    assert n == 1  # only the non-Code apostrophe is replaced.

    runs = tree.findall(f".//{W}r")
    expected = "It" + "\u2019" + "s fine"
    assert runs[0].find(f"{W}t").text == expected
    assert runs[1].find(f"{W}t").text == "don't"


def test_scenario_4_idempotent() -> None:
    body = (
        "<w:p>"
        "<w:r><w:t>can't won't</w:t></w:r>"
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    escape_smart_quotes(tree)
    first = etree.tostring(tree)
    n2 = escape_smart_quotes(tree)
    second = etree.tostring(tree)
    assert n2 == 0
    assert first == second


# ---------------------------------------------------------------------------
# Scenario 4b - paragraph-scoped alternation
# ---------------------------------------------------------------------------


def test_scenario_4b_alternation_per_paragraph_paired() -> None:
    body = (
        "<w:p>"
        '<w:r><w:t>say "hi" and "yo"</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    escape_smart_quotes(tree)
    text = tree.find(f".//{W}r/{W}t").text
    assert text == "say “hi” and “yo”"


def test_scenario_4b_alternation_unpaired_left_ascii() -> None:
    body = (
        "<w:p>"
        '<w:r><w:t>three " quotes " and "</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    escape_smart_quotes(tree)
    text = tree.find(f".//{W}r/{W}t").text
    # 3 double quotes -> opening, closing, last left ASCII
    assert text == "three “ quotes ” and \""


def test_scenario_4b_alternation_resets_across_paragraphs() -> None:
    body = (
        "<w:p>"
        '<w:r><w:t>"first"</w:t></w:r>'
        "</w:p>"
        "<w:p>"
        '<w:r><w:t>"second"</w:t></w:r>'
        "</w:p>"
    )
    tree = _make_doc_with_body(body)
    escape_smart_quotes(tree)
    paragraphs = tree.findall(f".//{W}p")
    t1 = paragraphs[0].find(f".//{W}t").text
    t2 = paragraphs[1].find(f".//{W}t").text
    assert t1 == "“first”"
    assert t2 == "“second”"


# ---------------------------------------------------------------------------
# Scenario 5 - durable id auto-repair + whitespace preserve
# ---------------------------------------------------------------------------


def test_scenario_5_pack_autorepair_durable_id(tmp_path: Path) -> None:
    src = FIXTURES / "with_durable_id_overflow.docx"
    out_dir = tmp_path / "u"
    out_zip = tmp_path / "out.docx"

    unpack(src, out_dir)

    # Bump the durableId above the threshold so auto-repair fires; the
    # fixture deliberately uses 0x7FFFFFFE which is _at_ the boundary —
    # adjust to be strictly >= the threshold for a clean assert.
    doc_path = out_dir / "word" / "document.xml"
    raw = doc_path.read_text("utf-8").replace(
        'w:durableId="2147483646"', 'w:durableId="2147483647"',
    )
    doc_path.write_text(raw, "utf-8")

    result = pack(out_dir, out_zip)
    assert result.repaired_durable_ids >= 1


def test_scenario_5_pack_preserves_whitespace_runs(tmp_path: Path) -> None:
    # Build a tiny DOCX with leading/trailing whitespace on a w:t lacking
    # xml:space=preserve.
    src = FIXTURES / "minimal_valid.docx"
    out_dir = tmp_path / "u"
    out_zip = tmp_path / "out.docx"
    unpack(src, out_dir)

    doc_path = out_dir / "word" / "document.xml"
    raw = doc_path.read_text("utf-8")
    raw = raw.replace(
        "<w:t>Hello world</w:t>", "<w:t>  leading ws  </w:t>",
    )
    doc_path.write_text(raw, "utf-8")

    result = pack(out_dir, out_zip)
    assert result.preserved_whitespace_runs >= 1


# ---------------------------------------------------------------------------
# Scenario 6 - non-zip raises OOXML_NOT_A_ZIP
# ---------------------------------------------------------------------------


def test_scenario_6_unpack_non_zip_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "not_a_zip.docx"
    bogus.write_bytes(b"this is plain text, not a zip")
    with pytest.raises(OOXMLError) as exc_info:
        unpack(bogus, tmp_path / "u")
    assert exc_info.value.code == "OOXML_NOT_A_ZIP"


# ---------------------------------------------------------------------------
# Scenario 7 - missing [Content_Types].xml
# ---------------------------------------------------------------------------


def test_scenario_7_unpack_missing_content_types(tmp_path: Path) -> None:
    bogus = tmp_path / "no_ct.docx"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
    with pytest.raises(OOXMLError) as exc_info:
        unpack(bogus, tmp_path / "u")
    assert exc_info.value.code == "OOXML_MISSING_CONTENT_TYPES"


# ---------------------------------------------------------------------------
# Scenario 8 - validate_relationships dangling target
# ---------------------------------------------------------------------------


def test_scenario_8_validate_relationships_raises_on_dangling(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "u"
    unpack(FIXTURES / "with_dangling_rel.docx", out_dir)
    with pytest.raises(OOXMLError) as exc_info:
        validate_relationships(out_dir)
    err = exc_info.value
    assert err.code == "OOXML_RELATIONSHIP_BROKEN"
    assert "missing.xml" in str(err)


# ---------------------------------------------------------------------------
# Scenario 9 - Override entry order preserved
# ---------------------------------------------------------------------------


def test_scenario_9_content_types_override_order_preserved(
    tmp_path: Path,
) -> None:
    src = FIXTURES / "with_grace.docx"
    out_dir = tmp_path / "u"
    out_zip = tmp_path / "out.docx"
    unpack(src, out_dir)
    pack(out_dir, out_zip)

    src_ct = etree.fromstring(_read_zip_parts(src)["[Content_Types].xml"])
    out_ct = etree.fromstring(_read_zip_parts(out_zip)["[Content_Types].xml"])

    src_overrides = [el.get("PartName") for el in src_ct.findall(f"{CT}Override")]
    out_overrides = [el.get("PartName") for el in out_ct.findall(f"{CT}Override")]
    assert src_overrides == out_overrides


# ---------------------------------------------------------------------------
# Wave-5-1 evidence-1: log markers fire in correct order
# ---------------------------------------------------------------------------


def test_wave_5_1_log_markers_fire_in_order(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="mint.ooxml")

    src = FIXTURES / "with_durable_id_overflow.docx"
    out_dir = tmp_path / "u"
    out_zip = tmp_path / "out.docx"

    unpack(src, out_dir)

    # Force durable id above threshold so autorepair logs fire.
    doc_path = out_dir / "word" / "document.xml"
    raw = doc_path.read_text("utf-8").replace(
        'w:durableId="2147483646"', 'w:durableId="2147483647"',
    )
    doc_path.write_text(raw, "utf-8")
    pack(out_dir, out_zip)

    messages = [rec.getMessage() for rec in caplog.records]
    text = "\n".join(messages)

    assert "[OOXML][unpack][BLOCK_OOXML_UNPACK]" in text
    assert "[OOXML][pack][BLOCK_OOXML_PACK]" in text
    assert "[OOXML][autorepair][BLOCK_OOXML_AUTOREPAIR]" in text

    # Order check: the first unpack marker precedes the first pack marker
    # which precedes the autorepair marker (autorepair is emitted between
    # pack-start and pack-done in the implementation).
    def first_idx(needle: str) -> int:
        for i, m in enumerate(messages):
            if needle in m:
                return i
        return -1

    i_unpack = first_idx("[OOXML][unpack][BLOCK_OOXML_UNPACK]")
    i_pack = first_idx("[OOXML][pack][BLOCK_OOXML_PACK]")
    i_repair = first_idx("[OOXML][autorepair][BLOCK_OOXML_AUTOREPAIR]")
    assert 0 <= i_unpack < i_pack < i_repair


# ---------------------------------------------------------------------------
# Wave-5-1 evidence-5: static check on imports / no shell-out
# ---------------------------------------------------------------------------


def test_wave_5_1_static_no_shellout_imports() -> None:
    src = (Path(__file__).resolve().parent.parent.parent
           / "src" / "mint" / "ooxml.py")
    text = src.read_text("utf-8")

    forbidden_patterns = [
        r"\bimport\s+subprocess\b",
        r"\bfrom\s+subprocess\b",
        r"\bos\.system\b",
        r"\bos\.popen\b",
        r"\bos\.execv\b",
        r"\bos\.execvp\b",
        r"\bpty\.spawn\b",
        r"(^|[^a-zA-Z_])sh\.\w+\(",
        r"shell\s*=\s*True",
    ]
    for pat in forbidden_patterns:
        assert re.search(pat, text) is None, (
            f"forbidden pattern matched: {pat}"
        )

    # Positive framing: imports limited to allowed stdlib + lxml.etree.
    allowed_imports = {
        "zipfile",
        "lxml.etree",
        "hashlib",
        "pathlib",
        "dataclasses",
        "typing",
        "logging",
        "re",
        "unicodedata",
        "__future__",
    }
    import_line_re = re.compile(
        r"^(?:from\s+([a-zA-Z_][\w.]*)\s+import|import\s+([a-zA-Z_][\w.]*))",
        re.MULTILINE,
    )
    for match in import_line_re.finditer(text):
        mod = match.group(1) or match.group(2)
        # Allow submodules of allowed roots (e.g. lxml.etree).
        root = mod.split(".")[0]
        if root in {"lxml"}:
            assert mod in {"lxml", "lxml.etree"}, (
                f"only lxml.etree is permitted, got {mod}"
            )
            continue
        assert mod in allowed_imports, f"disallowed import: {mod}"
