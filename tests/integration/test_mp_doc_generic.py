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
