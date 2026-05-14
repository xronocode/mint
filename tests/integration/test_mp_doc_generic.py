# FILE: tests/integration/test_mp_doc_generic.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-DOC-GENERIC verification — covers scenarios 1, 2, 3, 5, 6
#     of the generic create_document FastMCP tool. Scenario-4 (doc_type=
#     'letter') is deferred to W2 (MP-TEMPLATES-REGISTRY) because it
#     requires templates/letter.yaml as a committed asset; this file
#     instead exercises doc-type agnosticism via a fixture template loaded
#     through a monkeypatched _TEMPLATES_DIR.
#   SCOPE: Integration tests — exercise the generic pipeline against
#     FakeMCPContext, the legacy memo alias path, an unknown doc_type,
#     and the structured-content extension carrying doc_type +
#     template_version.
#   DEPENDS: pytest, mint_python.mcp.document, mint_python.mcp.memo,
#     tests._helpers.fake_mcp_context.
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest

from mint_python.mcp import document as document_module
from mint_python.mcp.document import (
    DocumentTypeNotFound,
    _run_pipeline,
    _to_tool_result,
)
from mint_python.mcp.memo import _run_memo_pipeline
from tests._helpers.fake_mcp_context import FakeMCPContext

MEMO_FIXTURES = Path(__file__).parent.parent / "fixtures" / "memo_poc"


def _read_memo_fixture(name: str) -> str:
    return (MEMO_FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic output dir per test — same pattern as test_mp_memo_poc.py."""
    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "doc_out"))


@pytest.fixture(autouse=True)
def _isolate_templates_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a tmp_path-isolated templates/ snapshot of the canonical
    baselines (memo + letter at v1.0). Insulates these tests from any
    update_template-authored siblings that may be sitting in repo's
    templates/ from local development or smoke-testing — the W1
    parity scenarios assert on version='1.0' which only holds when
    no v1.1 sibling exists."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    repo_templates = Path(__file__).parent.parent.parent / "templates"
    for name in ("memo.yaml", "letter.yaml"):
        (fixtures / name).write_text(
            (repo_templates / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    from mint_python.mcp import document as document_module
    from mint_python.templates import registry as reg_module

    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixtures)
    monkeypatch.setattr(reg_module, "_TEMPLATES_DIR", fixtures)
    from mint_python.templates.registry import reset_default_registry
    reset_default_registry()
    yield fixtures
    reset_default_registry()


# --------------------------------------------------------------------------- #
# Scenario-1: parity — create_document(doc_type='memo') reproduces the
# Phase-13 MEMO-POC byte-for-byte (same docx structure, same klawd visual,
# same GRACE manifest schema). Backwards-compat baseline.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_create_document_memo_parity_with_memo_poc() -> None:
    """create_document(doc_type='memo') produces the same docx surface the
    legacy create_memo path produces: GRACE manifest with audit_id, klawd
    preset applied, fields_elicited recording the user-supplied subset."""
    intent = _read_memo_fixture("intent_full.txt")
    ctx = FakeMCPContext(answers={})

    result = await _run_pipeline(
        intent=intent, doc_type="memo", source_md=intent, ctx=ctx
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "memo"
    assert result["template_version"] == "1.0"
    assert ctx.elicited_calls == [], (
        "scenario-1 forbids elicit calls when intent is full — got "
        + repr(ctx.elicited_calls)
    )

    # Filename uses the doc_type prefix — `memo_<date>_<subject>_<short>.docx`.
    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.name.startswith("memo_")

    # GRACE manifest parts live at grace/manifest_<uuid>.xml inside the
    # docx zip (per MP-GRACE bootstrap implementation); confirm audit_id
    # and the new doc_type/template_version metadata flow through.
    with zipfile.ZipFile(output_path, "r") as zf:
        grace_parts = [
            n for n in zf.namelist()
            if n.startswith("grace/") and n.endswith(".xml")
        ]
        assert grace_parts, "GRACE injection didn't run"
        manifest_blob = b"".join(zf.read(p) for p in grace_parts)
    assert result["audit_id"].encode() in manifest_blob
    assert b"template=memo.yaml" in manifest_blob
    assert b"template_version=1.0" in manifest_blob


# --------------------------------------------------------------------------- #
# Scenario-2: legacy create_memo alias delegates to create_document and
# emits the [MP-Memo] log marker family verbatim, so V-MP-MEMO-POC trace
# scenarios stay green during the transition window.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_create_memo_alias_keeps_mp_memo_log_prefix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The memo alias delegates to the generic pipeline but with
    log_prefix='MP-Memo' — V-MP-MEMO-POC scenarios assert the prefix in
    log messages, so the alias path must keep emitting it."""
    intent = _read_memo_fixture("intent_full.txt")
    ctx = FakeMCPContext(answers={})

    with caplog.at_level(logging.INFO, logger="mint_python.mcp.document"):
        result = await _run_memo_pipeline(intent=intent, source_md=intent, ctx=ctx)

    assert result["status"] == "complete"
    msgs = [r.getMessage() for r in caplog.records]
    memo_msgs = [m for m in msgs if "[MP-Memo]" in m]
    doc_msgs = [m for m in msgs if "[MP-Doc]" in m]
    assert memo_msgs, "expected [MP-Memo] log markers from the alias path"
    assert not doc_msgs, "alias path must NOT emit [MP-Doc] markers"


# --------------------------------------------------------------------------- #
# Scenario-3: unknown doc_type raises DOC_TYPE_NOT_FOUND and the message
# names the available alternatives. Helps the connected model retry with
# a valid name.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_unknown_doc_type_raises_with_available_list() -> None:
    """create_document(doc_type='nonexistent') raises DocumentTypeNotFound
    whose message lists the doc_types that ARE available."""
    intent = "anything"
    ctx = FakeMCPContext(answers={})

    with pytest.raises(DocumentTypeNotFound, match="DOC_TYPE_NOT_FOUND") as exc_info:
        await _run_pipeline(
            intent=intent, doc_type="nonexistent", source_md=None, ctx=ctx
        )
    # The repo ships templates/memo.yaml today; the message must list it.
    assert "memo" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# Scenario-5: BLOCK_PARSE_INTENT log marker carries doc_type and
# template_version in the payload. Cross-module evidence chain otherwise
# unchanged.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_parse_intent_log_carries_doc_type_and_template_version(
    caplog: pytest.LogCaptureFixture,
) -> None:
    intent = _read_memo_fixture("intent_full.txt")
    ctx = FakeMCPContext(answers={})

    with caplog.at_level(logging.INFO, logger="mint_python.mcp.document"):
        await _run_pipeline(
            intent=intent, doc_type="memo", source_md=intent, ctx=ctx
        )

    parse_msgs = [
        r.getMessage()
        for r in caplog.records
        if "BLOCK_PARSE_INTENT" in r.getMessage()
    ]
    assert parse_msgs, "BLOCK_PARSE_INTENT log marker missing"
    msg = parse_msgs[0]
    assert "doc_type=memo" in msg
    assert "template_version=1.0" in msg


# --------------------------------------------------------------------------- #
# Scenario-6: structured_content extends MEMO-POC's shape with doc_type and
# template_version. Existing readers of {status, path, audit_id,
# fields_elicited} continue to work.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_structured_content_extends_with_doc_type_fields() -> None:
    intent = _read_memo_fixture("intent_full.txt")
    ctx = FakeMCPContext(answers={})

    result = await _run_pipeline(
        intent=intent, doc_type="memo", source_md=intent, ctx=ctx
    )
    tool_result = _to_tool_result(result)
    structured = tool_result.structured_content
    assert structured is not None

    # Phase-13 (MEMO-POC) keys.
    for legacy_key in ("status", "path", "audit_id", "fields_elicited"):
        assert legacy_key in structured, f"legacy key {legacy_key!r} missing"

    # Phase-14 W1 (MP-DOC-GENERIC) extension.
    assert structured["doc_type"] == "memo"
    assert structured["template_version"] == "1.0"

    # ToolResult content blocks: TextContent with markdown link + ResourceLink.
    types_seen = {type(c).__name__ for c in tool_result.content}
    assert "TextContent" in types_seen
    assert "ResourceLink" in types_seen


# --------------------------------------------------------------------------- #
# doc_type agnosticism — fixture-driven sanity check for the W1 deliverable
# without committing templates/letter.yaml (W2 scope). We materialize a
# minimal alternate template into tmp_path, monkeypatch _TEMPLATES_DIR,
# and verify the same pipeline produces a docx whose filename + GRACE
# manifest carry the alternate doc_type. Anchors V-MP-DOC-GENERIC
# forbidden-1 (no per-doc_type if/else branches in the pipeline).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pipeline_is_doc_type_agnostic_via_fixture_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop a minimal alternate doc_type into a temp templates/ dir. The
    pipeline's required_fields driver reads from the template — no Python
    changes needed to support a new doc_type, which is the W1 deliverable.

    Letter has different fields (sender, recipient, body) than memo's
    five — this exercises that the elicitation loop iterates the
    template's required_fields list, not memo's hardcoded tuple."""
    fixtures_dir = tmp_path / "templates_fixture"
    fixtures_dir.mkdir()
    # Reuse the canonical memo so test_scenario_3's "available list"
    # assertion stays sensible inside the same monkeypatch scope.
    (fixtures_dir / "memo.yaml").write_text(
        (Path(__file__).parent.parent.parent / "templates" / "memo.yaml").read_text(),
        encoding="utf-8",
    )
    (fixtures_dir / "letter_fixture.yaml").write_text(
        """
name: letter_fixture
version: "0.1"
description: minimal letter for W1 fixture-driven agnosticism check
required_fields:
  - sender
  - recipient
  - body
layout:
  - kind: heading
    level: 1
    text: "{{ recipient }}"
  - kind: paragraph
    text: "{{ body }}"
  - kind: spacer
  - kind: paragraph
    text: "{{ sender }}"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixtures_dir)

    intent = (
        "sender: Mikhail Yevdokimov\n"
        "recipient: Mr. Smith\n"
        "body: A short letter body for the agnosticism fixture test."
    )
    ctx = FakeMCPContext(answers={})

    result = await _run_pipeline(
        intent=intent,
        doc_type="letter_fixture",
        source_md=None,
        ctx=ctx,
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "letter_fixture"
    assert result["template_version"] == "0.1"

    # No elicit calls — heuristic + labelled-key extraction filled all
    # three fields. Proves the labelled-key path is doc_type agnostic.
    assert ctx.elicited_calls == []

    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.name.startswith("letter_fixture_")

    # GRACE manifest records the doc_type-specific template + version.
    with zipfile.ZipFile(output_path, "r") as zf:
        grace_parts = [
            n for n in zf.namelist()
            if n.startswith("grace/") and n.endswith(".xml")
        ]
        assert grace_parts, "GRACE injection didn't run"
        manifest_blob = b"".join(zf.read(p) for p in grace_parts)
    assert b"template=letter_fixture.yaml" in manifest_blob
    assert b"template_version=0.1" in manifest_blob
    assert b"generated_by=MP-DOC-GENERIC" in manifest_blob


# --------------------------------------------------------------------------- #
# Direct invocation of the @server.tool-decorated create_document — mirrors
# the test_mp_memo_poc coverage of create_memo's wrapper. Confirms the
# FunctionTool decorator preserves the callable signature on the new tool.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Scenario-4: real templates/letter.yaml drives the pipeline (W2 ships
# the asset; this test promotes V-MP-DOC-GENERIC scenario-4 from
# deferred-to-W2 to done). Different required_fields → different
# elicitation order, different layout, klawd preset still applied.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_letter_doc_type_uses_real_letter_yaml() -> None:
    """create_document(doc_type='letter') uses the committed
    templates/letter.yaml. The letter shape's required_fields differ from
    memo's (recipient/body/sender/date — no subject; different order),
    proving the pipeline is genuinely doc-type agnostic."""
    intent = (
        "recipient: Mr. Smith\n"
        "body: A short letter for the scenario-4 verification.\n"
        "sender: Mikhail Yevdokimov\n"
        "date: 2026-05-15\n"
    )
    ctx = FakeMCPContext(answers={})

    result = await _run_pipeline(
        intent=intent, doc_type="letter", source_md=None, ctx=ctx
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "letter"
    assert ctx.elicited_calls == [], (
        "scenario-4 forbids elicit calls when intent carries all letter fields"
    )

    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.name.startswith("letter_")

    with zipfile.ZipFile(output_path, "r") as zf:
        grace_parts = [
            n for n in zf.namelist()
            if n.startswith("grace/") and n.endswith(".xml")
        ]
        assert grace_parts
        manifest_blob = b"".join(zf.read(p) for p in grace_parts)
    assert b"template=letter.yaml" in manifest_blob
    assert b"generated_by=MP-DOC-GENERIC" in manifest_blob


@pytest.mark.asyncio
async def test_create_document_decorated_callable_returns_tool_result() -> None:
    """The @server.tool wrapper around create_document is callable in tests
    the same way as create_memo (Phase-13 pattern). Returned ToolResult
    carries TextContent + ResourceLink + structured_content."""
    from mcp.types import ResourceLink, TextContent

    from mint_python.mcp.document import create_document

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\n"
        "subject: T\nbody: Plain body content.\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=intent, doc_type="memo", source_md=None, ctx=ctx
    )

    assert len(result.content) == 2
    assert isinstance(result.content[0], TextContent)
    assert "ready" in result.content[0].text.lower()
    assert "file://" in result.content[0].text

    assert isinstance(result.content[1], ResourceLink)
    assert str(result.content[1].uri).startswith("file://")

    assert result.structured_content["status"] == "complete"
    assert result.structured_content["doc_type"] == "memo"
    assert result.structured_content["template_version"] == "1.0"


# --------------------------------------------------------------------------- #
# Bug #1 — kind: callout in template layout renders to docx (was previously
# silently skipped, surfaced by W3 cross-model handoff smoke 2026-05-10).
# --------------------------------------------------------------------------- #


def test_callout_in_template_layout_renders_to_docx(tmp_path: Path) -> None:
    """A template entry with kind: callout produces a callout in the
    output docx. Tests both the comment-documented vocab (kind_of /
    body / title) AND the natural Claude-Desktop shape (type / text /
    label) to cover the field-name leniency."""
    from mint_python.mcp.document import (
        DocumentSpec,
        DocumentTemplate,
        _build_document,
    )

    # Comment-documented vocab.
    template_a = DocumentTemplate(
        name="x",
        version="1.0",
        required_fields=(),
        layout=[
            {"kind": "heading", "level": 1, "text": "Header"},
            {"kind": "callout", "kind_of": "warning", "title": "A", "body": "Body A"},
        ],
    )
    spec = DocumentSpec()
    doc_a = _build_document(spec, template_a)
    out_a = tmp_path / "a.docx"
    doc_a.save(out_a)

    # Claude's natural shape (type / text / label).
    template_b = DocumentTemplate(
        name="x",
        version="1.0",
        required_fields=(),
        layout=[
            {"kind": "heading", "level": 1, "text": "Header"},
            {"kind": "callout", "type": "warning", "label": "B", "text": "Body B"},
        ],
    )
    doc_b = _build_document(spec, template_b)
    out_b = tmp_path / "b.docx"
    doc_b.save(out_b)

    # Both docx files contain the callout text in the body part. The
    # callout is implemented via styled paragraphs in word/document.xml,
    # so the body text appears verbatim there.
    with zipfile.ZipFile(out_a) as zf:
        body_a = zf.read("word/document.xml").decode("utf-8")
    with zipfile.ZipFile(out_b) as zf:
        body_b = zf.read("word/document.xml").decode("utf-8")
    assert "Body A" in body_a
    assert "Body B" in body_b


def test_callout_substitutes_template_fields(tmp_path: Path) -> None:
    """Callout body and title go through _substitute, so {{ field }}
    placeholders work just like in headings/paragraphs/tables."""
    from mint_python.mcp.document import (
        DocumentSpec,
        DocumentTemplate,
        _build_document,
    )

    template = DocumentTemplate(
        name="x",
        version="1.0",
        required_fields=("subject",),
        layout=[
            {"kind": "heading", "level": 1, "text": "Header"},
            {
                "kind": "callout",
                "kind_of": "info",
                "title": "Re: {{ subject }}",
                "body": "Note about {{ subject }}",
            },
        ],
    )
    spec = DocumentSpec(subject="Q2 review")
    doc = _build_document(spec, template)
    out = tmp_path / "out.docx"
    doc.save(out)
    with zipfile.ZipFile(out) as zf:
        body = zf.read("word/document.xml").decode("utf-8")
    assert "Note about Q2 review" in body
    assert "Re: Q2 review" in body


def test_callout_unknown_kind_falls_back_to_info(tmp_path: Path) -> None:
    """Unknown `kind_of` value (typo or future extension) defaults to
    INFO rather than crashing — forgiving for template authors."""
    from mint_python.mcp.document import (
        DocumentSpec,
        DocumentTemplate,
        _build_document,
    )

    template = DocumentTemplate(
        name="x",
        version="1.0",
        required_fields=(),
        layout=[
            {"kind": "heading", "level": 1, "text": "Header"},
            {"kind": "callout", "kind_of": "futuristic-warning", "body": "X"},
        ],
    )
    doc = _build_document(DocumentSpec(), template)
    out = tmp_path / "out.docx"
    doc.save(out)  # must not raise
    with zipfile.ZipFile(out) as zf:
        body = zf.read("word/document.xml").decode("utf-8")
    assert "X" in body


# --------------------------------------------------------------------------- #
# Bug #2 — _substitute supports {{ name | default: "..." }} Jinja filter.
# --------------------------------------------------------------------------- #


def test_substitute_uses_field_when_set() -> None:
    """When the named field IS set on the spec, the default is ignored
    and the field value used."""
    from mint_python.mcp.document import DocumentSpec, _substitute

    spec = DocumentSpec(subject="Q2 review")
    assert _substitute("Re: {{ subject }}", spec) == "Re: Q2 review"
    assert (
        _substitute('Re: {{ subject | default: "untitled" }}', spec)
        == "Re: Q2 review"
    )


def test_substitute_falls_back_to_default_when_field_unset() -> None:
    """Field unset + default present → default rendered. Both double-
    and single-quoted defaults work."""
    from mint_python.mcp.document import DocumentSpec, _substitute

    spec = DocumentSpec()  # nothing set
    assert (
        _substitute('Hello {{ subject | default: "world" }}', spec)
        == "Hello world"
    )
    assert (
        _substitute("Hello {{ subject | default: 'world' }}", spec)
        == "Hello world"
    )


def test_substitute_empty_when_no_default_and_field_unset() -> None:
    """Field unset + no default → empty string (preserves prior behavior)."""
    from mint_python.mcp.document import DocumentSpec, _substitute

    spec = DocumentSpec()
    assert _substitute("Hello {{ subject }}", spec) == "Hello "


def test_substitute_default_with_punctuation_inside() -> None:
    """Default strings can contain commas, colons, periods — common in
    confidentiality-style notices that drove this issue."""
    from mint_python.mcp.document import DocumentSpec, _substitute

    spec = DocumentSpec()
    text = (
        '{{ confidentiality | default: '
        '"This memo is confidential. Do not forward." }}'
    )
    out = _substitute(text, spec)
    assert out == "This memo is confidential. Do not forward."


# --------------------------------------------------------------------------- #
# Bug #3 — tool result text includes the file:// URI verbatim AND the
# create_document docstring directs the model to relay it as-is.
# --------------------------------------------------------------------------- #


def test_to_tool_result_text_includes_file_uri_verbatim() -> None:
    """The TextContent we return MUST contain the file:// URI as a raw
    string (not just inside a markdown link). Easier for downstream
    paraphrasing to preserve."""
    from mint_python.mcp.document import _to_tool_result

    fake_result = {
        "status": "complete",
        "path": "/Users/example/Documents/MINT/memo_test.docx",
        "audit_id": "deadbeef-1234",
        "fields_elicited": [],
        "doc_type": "memo",
        "template_version": "1.0",
    }
    tool_result = _to_tool_result(fake_result)
    text = tool_result.content[0].text
    # The raw file:// URI must appear in the text — not only inside a
    # markdown-link parens.
    assert "file:///Users/example/Documents/MINT/memo_test.docx" in text
    # Imperative-style cue that helps relayers preserve the path.
    assert "Open" in text or "Saved" in text or "open" in text
    assert "deadbeef-1234" in text


def test_create_document_docstring_directs_verbatim_relay() -> None:
    """create_document's docstring must instruct the model to relay the
    file:// link verbatim — directives in tool descriptions are
    typically respected by orchestrating models."""
    from mint_python.mcp.document import create_document

    # FastMCP tool decorators preserve the underlying function's docstring.
    docstring = (
        getattr(create_document, "__doc__", None)
        or getattr(create_document, "fn", create_document).__doc__
        or ""
    )
    needle = "verbatim"
    assert needle in docstring.lower(), (
        "create_document docstring must contain a verbatim-relay "
        "directive so connected models preserve the file:// link in "
        "their reply to the user (closes #3)"
    )


# --------------------------------------------------------------------------- #
# Report-specific heuristic extraction (author / summary / conclusions)
# --------------------------------------------------------------------------- #


def test_report_labelled_fields_extracted() -> None:
    """Report intent with labelled fields should extract all 6 required
    fields: title, author, date, summary, sections, conclusions."""
    from mint_python.mcp.document import _heuristic_extract

    intent = (
        "title: MINT SDK Quarterly Report\n"
        "author: Quality Engineering Team\n"
        "date: May 13, 2026\n"
        "summary: This report summarises MINT achievements in Q1 2026.\n"
        "sections: Template architecture, style system, validation pipeline.\n"
        "conclusions: MINT v0.4.0 achieves 100% test coverage."
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.title == "MINT SDK Quarterly Report"
    assert spec.author == "Quality Engineering Team"
    assert spec.date == "May 13, 2026"
    assert spec.summary == "This report summarises MINT achievements in Q1 2026"
    assert spec.sections == "Template architecture, style system, validation pipeline"
    assert spec.conclusions == "MINT v0.4.0 achieves 100% test coverage"


def test_report_kv_fields_extracted() -> None:
    """Report intent with key=value format should extract author, summary,
    conclusions."""
    from mint_python.mcp.document import _heuristic_extract

    intent = (
        "author=QE Team "
        "summary=Q1 2026 results "
        "conclusions=All green"
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.author == "QE Team"
    assert spec.summary == "Q1 2026 results"
    assert spec.conclusions == "All green"


def test_report_filled_covers_author_summary_conclusions() -> None:
    """DocumentSpec.filled() includes author/summary/conclusions for report
    template required_fields."""
    from mint_python.mcp.document import DocumentSpec

    report_fields = (
        "title", "author", "date", "summary", "sections", "conclusions",
    )
    spec = DocumentSpec(
        title="T", author="A", date="2026-05-13",
        summary="S", sections="X", conclusions="C",
    )
    assert spec.filled(report_fields) == [
        "title", "author", "date", "summary", "sections", "conclusions",
    ]


def test_comma_separated_labelled_intent() -> None:
    """Comma-separated "label: value, label: value" on one line should
    extract all fields (BUG-1 regression test from smoke test v4)."""
    from mint_python.mcp.document import _heuristic_extract

    intent = (
        "sender: QE Team, recipient: Engineering Team, "
        "date: May 15, 2026, "
        "subject: MINT v0.4.0 Release, "
        "body: We are pleased to announce MINT v0.4.0."
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.sender == "QE Team"
    assert spec.recipient == "Engineering Team"
    assert spec.date == "May 15, 2026"
    assert spec.subject == "MINT v0.4.0 Release"
    assert spec.body == "We are pleased to announce MINT v0.4.0"
