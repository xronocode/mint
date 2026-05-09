"""Generate DOCX fixtures used by tests/unit/test_edit.py (Phase-5 Wave-5-2).

Produces:
  - with_revisions.docx          : DOCX containing existing w:ins and w:del
                                   entries for accept_change / reject_change
                                   tests (V-M-EDIT scenario-12, fixture-15).
  - with_comments.docx           : DOCX containing top-level comments in
                                   word/comments.xml plus matching
                                   commentRange markers in document.xml
                                   (fixture-16).
  - with_comment_replies.docx    : DOCX containing parent comment plus reply
                                   linked through word/commentsExtended.xml
                                   (w15:commentEx paraIdParent) (fixture-17).

Run once (idempotent — overwrites existing output).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
SRC = FIXTURES_DIR / "minimal_valid.docx"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def _read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _document_xml(body_p: str, *, with_w14: bool = True) -> bytes:
    ns_decl = (
        f'xmlns:w="{W_NS}" xmlns:r="{R_NS}"'
        + (f' xmlns:w14="{W14_NS}"' if with_w14 else "")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {ns_decl}>"
        "<w:body>"
        f"{body_p}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
        "</w:body></w:document>"
    ).encode()


def _styles_xml() -> bytes:
    """Minimal styles.xml with Heading1, Heading2, Normal, ListParagraph."""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:styles xmlns:w="{W_NS}">'
        '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/></w:style>'
        "</w:styles>"
    ).encode()


def _ct_with_extras(*, comments: bool = False, comments_extended: bool = False) -> bytes:
    """Content types listing the extras we add."""
    overrides = [
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>',
    ]
    if comments:
        overrides.append(
            '<Override PartName="/word/comments.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
        )
    if comments_extended:
        overrides.append(
            '<Override PartName="/word/commentsExtended.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    ).encode("utf-8")


def _document_rels(*, comments: bool = False) -> bytes:
    rels = []
    if comments:
        rels.append(
            '<Relationship Id="rIdC1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" '
            'Target="comments.xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{PR_NS}">'
        + "".join(rels)
        + "</Relationships>"
    ).encode("utf-8")


def create_with_revisions() -> Path:
    """Build a DOCX with one w:ins(id=1) and one w:del(id=2) inside paragraphs.

    Plus a third paragraph with a paragraph-mark deletion (w:pPr/w:rPr/w:del).
    """
    out = FIXTURES_DIR / "with_revisions.docx"
    body = (
        # Paragraph 1: ordinary
        "<w:p>"
        "<w:r><w:t>Intro paragraph.</w:t></w:r>"
        "</w:p>"
        # Paragraph 2: contains w:ins (id=1)
        "<w:p>"
        "<w:r><w:t>Before insertion. </w:t></w:r>"
        '<w:ins w:id="1" w:author="Alice" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">inserted text</w:t></w:r>'
        "</w:ins>"
        "<w:r><w:t> after.</w:t></w:r>"
        "</w:p>"
        # Paragraph 3: contains w:del (id=2)
        "<w:p>"
        "<w:r><w:t>Before deletion. </w:t></w:r>"
        '<w:del w:id="2" w:author="Bob" w:date="2026-01-02T00:00:00Z">'
        '<w:r><w:rPr><w:i/></w:rPr><w:delText xml:space="preserve">deleted text</w:delText></w:r>'
        "</w:del>"
        "<w:r><w:t> after.</w:t></w:r>"
        "</w:p>"
        # Paragraph 4: paragraph-mark deletion
        "<w:p>"
        "<w:pPr>"
        '<w:rPr><w:del w:id="3" w:author="Bob" w:date="2026-01-03T00:00:00Z"/></w:rPr>'
        "</w:pPr>"
        "<w:r><w:t>Paragraph with deleted mark.</w:t></w:r>"
        "</w:p>"
    )
    entries = _read_zip(SRC)
    entries["word/document.xml"] = _document_xml(body)
    entries["word/styles.xml"] = _styles_xml()
    entries["[Content_Types].xml"] = _ct_with_extras()
    _write_zip(out, entries)
    return out


def create_with_comments() -> Path:
    """DOCX with one top-level comment around a run in paragraph 2."""
    out = FIXTURES_DIR / "with_comments.docx"
    body = (
        "<w:p>"
        "<w:r><w:t>Untouched paragraph.</w:t></w:r>"
        "</w:p>"
        '<w:p w14:paraId="00000010">'
        '<w:commentRangeStart w:id="0"/>'
        "<w:r><w:t>Commented sentence.</w:t></w:r>"
        '<w:commentRangeEnd w:id="0"/>'
        '<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
        '<w:commentReference w:id="0"/></w:r>'
        "</w:p>"
    )
    comments = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:comments xmlns:w="{W_NS}" xmlns:w14="{W14_NS}">'
        '<w:comment w:id="0" w:author="Alice" w:date="2026-01-01T00:00:00Z" w:initials="A">'
        '<w:p w14:paraId="0000B000"><w:r><w:t>This is a top-level comment.</w:t></w:r></w:p>'
        "</w:comment>"
        "</w:comments>"
    ).encode()
    entries = _read_zip(SRC)
    entries["word/document.xml"] = _document_xml(body, with_w14=True)
    entries["word/styles.xml"] = _styles_xml()
    entries["word/comments.xml"] = comments
    entries["[Content_Types].xml"] = _ct_with_extras(comments=True)
    entries["word/_rels/document.xml.rels"] = _document_rels(comments=True)
    _write_zip(out, entries)
    return out


def create_with_comment_replies() -> Path:
    """DOCX with parent comment + reply linked via commentsExtended.xml."""
    out = FIXTURES_DIR / "with_comment_replies.docx"
    body = (
        "<w:p>"
        "<w:r><w:t>Header paragraph.</w:t></w:r>"
        "</w:p>"
        '<w:p w14:paraId="0000A001">'
        '<w:commentRangeStart w:id="0"/>'
        "<w:r><w:t>Discussed sentence.</w:t></w:r>"
        '<w:commentRangeEnd w:id="0"/>'
        '<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
        '<w:commentReference w:id="0"/></w:r>'
        "</w:p>"
    )
    comments = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:comments xmlns:w="{W_NS}" xmlns:w14="{W14_NS}">'
        '<w:comment w:id="0" w:author="Alice" w:date="2026-01-01T00:00:00Z" w:initials="A">'
        '<w:p w14:paraId="0000B001"><w:r><w:t>Parent comment.</w:t></w:r></w:p>'
        "</w:comment>"
        '<w:comment w:id="1" w:author="Bob" w:date="2026-01-02T00:00:00Z" w:initials="B">'
        '<w:p w14:paraId="0000B002"><w:r><w:t>Reply comment.</w:t></w:r></w:p>'
        "</w:comment>"
        "</w:comments>"
    ).encode()
    comments_extended = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w15:commentsEx xmlns:w15="{W15_NS}">'
        '<w15:commentEx w15:paraId="0000B001" w15:done="0"/>'
        '<w15:commentEx w15:paraId="0000B002" w15:paraIdParent="0000B001" w15:done="0"/>'
        "</w15:commentsEx>"
    ).encode()
    entries = _read_zip(SRC)
    entries["word/document.xml"] = _document_xml(body, with_w14=True)
    entries["word/styles.xml"] = _styles_xml()
    entries["word/comments.xml"] = comments
    entries["word/commentsExtended.xml"] = comments_extended
    entries["[Content_Types].xml"] = _ct_with_extras(
        comments=True, comments_extended=True
    )
    entries["word/_rels/document.xml.rels"] = _document_rels(comments=True)
    _write_zip(out, entries)
    return out


def main() -> None:
    p1 = create_with_revisions()
    p2 = create_with_comments()
    p3 = create_with_comment_replies()
    print(p1)
    print(p2)
    print(p3)


if __name__ == "__main__":
    main()
