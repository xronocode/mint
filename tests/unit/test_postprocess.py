import io
import zipfile
from pathlib import Path

from lxml import etree

from mint.create import _postprocess_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def _make_minimal_docx(
    *,
    with_comments: bool = False,
    with_table: bool = False,
    duplicate_style_id: str | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
        )
        if with_table:
            doc_xml += (
                "<w:tbl>"
                "<w:tblPr>"
                '<w:tblW w:type="dxa" w:w="9360"/>'
                "<w:tblBorders>"
                '<w:top w:val="single" w:sz="4" w:color="auto"/>'
                '<w:left w:val="single" w:sz="4" w:color="auto"/>'
                '<w:bottom w:val="single" w:sz="4" w:color="auto"/>'
                '<w:right w:val="single" w:sz="4" w:color="auto"/>'
                "</w:tblBorders>"
                "</w:tblPr>"
                "<w:tblGrid>"
                '<w:gridCol w:w="9360"/>'
                "</w:tblGrid>"
                "<w:tr><w:tc><w:tcPr>"
                '<w:tcW w:type="dxa" w:w="9360"/>'
                "</w:tcPr><w:p/></w:tc></w:tr>"
                "</w:tbl>"
            )
        doc_xml += (
            "<w:p><w:r><w:t>Hello</w:t></w:r></w:p>"
            "<w:sectPr>"
            '<w:pgSz w:w="11906" w:h="16838"/>'
            "</w:sectPr>"
            "</w:body></w:document>"
        )
        z.writestr("word/document.xml", doc_xml.encode())

        styles_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:style w:type="paragraph" w:styleId="Heading1">'
            "<w:name w:val=\"heading 1\"/>"
            "</w:style>"
        )
        if duplicate_style_id:
            styles_xml += (
                f'<w:style w:type="paragraph" w:styleId="{duplicate_style_id}">'
                f"<w:name w:val=\"dup 1\"/>"
                f"</w:style>"
                f'<w:style w:type="paragraph" w:styleId="{duplicate_style_id}">'
                f"<w:name w:val=\"dup 2\"/>"
                f"</w:style>"
            )
        styles_xml += "</w:styles>"
        z.writestr("word/styles.xml", styles_xml.encode())

        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{REL_NS}">'
            f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        if with_comments:
            rels_xml += (
                '<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>'
            )
        rels_xml += "</Relationships>"
        z.writestr("word/_rels/document.xml.rels", rels_xml.encode())

        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Types xmlns="{CT_NS}">'
            '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" PartName="/word/document.xml"/>'
            '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml" PartName="/word/styles.xml"/>'
        )
        if with_comments:
            ct_xml += '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml" PartName="/word/comments.xml"/>'
        ct_xml += "</Types>"
        z.writestr("[Content_Types].xml", ct_xml.encode())

        if with_comments:
            comments_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
            )
            z.writestr("word/comments.xml", comments_xml.encode())
            z.writestr(
                "word/_rels/comments.xml.rels",
                f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}"/>'.encode(),
            )

    return buf.getvalue()


class TestPostprocessEmptyComments:
    def test_removes_empty_comments_xml(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(with_comments=True)
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)

        _postprocess_docx(p)

        with zipfile.ZipFile(p, "r") as z:
            names = z.namelist()
        assert "word/comments.xml" not in names
        assert "word/_rels/comments.xml.rels" not in names

    def test_removes_comments_from_rels(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(with_comments=True)
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)

        _postprocess_docx(p)

        with zipfile.ZipFile(p, "r") as z:
            rels = z.read("word/_rels/document.xml.rels").decode()
        assert "comments.xml" not in rels

    def test_removes_comments_from_content_types(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(with_comments=True)
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)

        _postprocess_docx(p)

        with zipfile.ZipFile(p, "r") as z:
            ct = z.read("[Content_Types].xml").decode()
        assert "/word/comments.xml" not in ct

    def test_no_change_when_no_comments(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(with_comments=False)
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)
        original_size = p.stat().st_size

        _postprocess_docx(p)

        assert p.stat().st_size == original_size


class TestPostprocessTableLayout:
    def test_adds_tbl_layout_fixed(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(with_table=True)
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)

        _postprocess_docx(p)

        with zipfile.ZipFile(p, "r") as z:
            doc = etree.fromstring(z.read("word/document.xml"))
        tbl = doc.find(f".//{{{W}}}tbl")
        assert tbl is not None
        layout = tbl.find(f".//{{{W}}}tblLayout")
        assert layout is not None
        assert layout.get(f"{{{W}}}type") == "fixed"

    def test_adds_tbl_look(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(with_table=True)
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)

        _postprocess_docx(p)

        with zipfile.ZipFile(p, "r") as z:
            doc = etree.fromstring(z.read("word/document.xml"))
        tbl = doc.find(f".//{{{W}}}tbl")
        look = tbl.find(f".//{{{W}}}tblLook")
        assert look is not None
        assert look.get(f"{{{W}}}firstRow") == "1"

    def test_no_change_when_no_table(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(with_table=False)
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)
        original_size = p.stat().st_size

        _postprocess_docx(p)

        assert p.stat().st_size == original_size


class TestPostprocessDuplicateStyles:
    def test_deduplicates_styles(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx(duplicate_style_id="MyStyle")
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)

        _postprocess_docx(p)

        with zipfile.ZipFile(p, "r") as z:
            styles = etree.fromstring(z.read("word/styles.xml"))
        ids = [
            s.get(f"{{{W}}}styleId")
            for s in styles.findall(f"{{{W}}}style")
        ]
        assert ids.count("MyStyle") == 1

    def test_no_change_when_no_duplicates(self, tmp_path: Path) -> None:
        docx_bytes = _make_minimal_docx()
        p = tmp_path / "test.docx"
        p.write_bytes(docx_bytes)
        original_size = p.stat().st_size

        _postprocess_docx(p)

        assert p.stat().st_size == original_size


class TestPostprocessNotDocx:
    def test_skips_non_docx_files(self, tmp_path: Path) -> None:
        p = tmp_path / "test.pptx"
        p.write_bytes(b"PK dummy")
        _postprocess_docx(p)
        assert p.read_bytes() == b"PK dummy"
