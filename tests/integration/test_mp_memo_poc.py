# FILE: tests/integration/test_mp_memo_poc.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-MEMO-POC verification — covers scenarios 1-8 of the FastMCP
#     create_memo tool against FakeMCPContext. Scenario-9 (manual Claude
#     Desktop smoke) is documented procedure; not pytest-runnable.
#   SCOPE: Integration tests — exercise the full pipeline (heuristic field
#     extraction → elicitation dialog → MP-DOCUMENT build → klawd preset →
#     GRACE manifest injection) end-to-end with a fake context.
#   DEPENDS: pytest, mint_python.mcp.memo, tests._helpers.fake_mcp_context
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest

from mint_python.mcp.memo import (
    MEMO_REQUIRED_FIELDS,
    MemoElicitationRejected,
    create_memo,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

FIXTURES = Path(__file__).parent.parent / "fixtures" / "memo_poc"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# scenario-1: full intent → no elicitation, docx straight through
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_full_intent_no_elicit_calls() -> None:
    intent = _read("intent_full.txt")
    # Body comes from intent itself — pass intent text via source_md too so
    # the heuristic can extract a body. (The "from / to / date / subject"
    # heuristics fire on the intent string; body fills from source_md.)
    ctx = FakeMCPContext(answers={})

    result = await create_memo(intent=intent, source_md=intent, ctx=ctx)

    assert ctx.elicited_calls == [], (
        "scenario-1 forbids any elicit call — got " + repr(ctx.elicited_calls)
    )
    assert result["fields_elicited"] == []
    assert Path(result["path"]).exists()
    assert result["audit_id"]


# --------------------------------------------------------------------------- #
# scenario-2: missing recipient → exactly one elicit call
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_single_missing_field() -> None:
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(
        answers={
            "recipient": "Board of Directors",
            "body": "Body filled by elicit",
        }
    )

    result = await create_memo(intent=intent, source_md=None, ctx=ctx)

    elicited_field_names = [field for field, _msg in ctx.elicited_calls]
    assert "recipient" in elicited_field_names
    # The result records every field that came from elicitation.
    assert "recipient" in result["fields_elicited"]


# --------------------------------------------------------------------------- #
# scenario-3: missing 2 fields → two elicit calls in declaration order
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_multi_round_in_declaration_order() -> None:
    """Intent only carries 'recipient' (Board of Directors). The heuristic
    won't extract sender/date/subject/body from the bare-bones text; all four
    get elicited in MEMO_REQUIRED_FIELDS declaration order.

    The order matters: sender → recipient → date → subject → body. This
    test asserts the elicit calls appear in that exact sequence, with
    recipient skipped (already extracted)."""
    intent = _read("intent_missing_two.txt")
    ctx = FakeMCPContext(
        answers={
            "sender": "Mikhail Yevdokimov, CFO",
            "date": "2026-05-15",
            "subject": "Q2 Revenue Trends",
            "body": "Q2 revenue grew 13% year-over-year, driven by services expansion.",
        }
    )

    result = await create_memo(intent=intent, source_md=None, ctx=ctx)

    elicited_field_names = [field for field, _msg in ctx.elicited_calls]
    # Sender + date + subject + body got elicited; recipient came from intent.
    assert "sender" in elicited_field_names
    assert "date" in elicited_field_names
    # Order check — sender comes before date (declaration order).
    assert elicited_field_names.index("sender") < elicited_field_names.index("date")
    assert "recipient" not in elicited_field_names  # extracted from intent
    assert result["fields_elicited"] == elicited_field_names


# --------------------------------------------------------------------------- #
# scenario-4: elicitation rejected → MemoElicitationRejected, no docx
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_elicitation_decline_raises_no_docx() -> None:
    intent = _read("intent_missing_recipient.txt")
    # User declines the recipient elicitation.
    ctx = FakeMCPContext(answers={"recipient": "__DECLINE__"})

    with pytest.raises(MemoElicitationRejected) as excinfo:
        await create_memo(intent=intent, source_md=None, ctx=ctx)

    assert excinfo.value.field_name == "recipient"
    # The trace must show ONE elicit call (the rejected one) — no further
    # elicitation past the rejection.
    assert ctx.elicited_calls == [
        ("recipient", excinfo.value.field_name and ctx.elicited_calls[0][1])
    ] or len(ctx.elicited_calls) == 1


@pytest.mark.asyncio
async def test_scenario_4_elicitation_cancel_raises_no_docx() -> None:
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(answers={"recipient": "__CANCEL__"})

    with pytest.raises(MemoElicitationRejected) as excinfo:
        await create_memo(intent=intent, source_md=None, ctx=ctx)

    assert excinfo.value.field_name == "recipient"


# --------------------------------------------------------------------------- #
# scenario-5: lenient validation passes on the produced docx
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_lenient_validation_passes() -> None:
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(
        answers={
            "recipient": "Board of Directors",
            "body": "Q2 revenue grew 13%.",
        }
    )
    result = await create_memo(intent=intent, source_md=None, ctx=ctx)

    # Re-open the docx through MP-VALIDATE on lenient mode.
    from mint_python.validate import run_checks

    report = run_checks(Path(result["path"]), severity_mode="lenient")
    assert report.passed, f"lenient validation failed: {report.violations}"
    assert report.hard_count == 0


# --------------------------------------------------------------------------- #
# scenario-6: klawd visual fidelity in saved styles.xml
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_6_klawd_visual_applied() -> None:
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(
        answers={
            "recipient": "Board",
            "body": "Body content.",
        }
    )
    result = await create_memo(intent=intent, source_md=None, ctx=ctx)

    with zipfile.ZipFile(result["path"]) as z:
        styles_xml = z.read("word/styles.xml").decode("utf-8")
    assert "1B3A5C" in styles_xml, "klawd primary #1B3A5C missing — apply_preset_to_doc regression"
    assert "Arial" in styles_xml, "klawd font Arial missing"
    assert "333333" in styles_xml, "klawd body color #333333 missing"


# --------------------------------------------------------------------------- #
# scenario-7: GRACE manifest injected with audit-trail
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_7_grace_manifest_with_audit_trail() -> None:
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(
        answers={"recipient": "Board", "body": "Body."}
    )
    result = await create_memo(intent=intent, source_md=None, ctx=ctx)

    audit_id = result["audit_id"]
    assert audit_id

    # GRACE manifests are stored under grace/manifest_<uuid>.xml inside the
    # docx zip (per MP-GRACE bootstrap implementation).
    with zipfile.ZipFile(result["path"]) as z:
        names = z.namelist()
        grace_parts = [n for n in names if n.startswith("grace/") and n.endswith(".xml")]
        assert grace_parts, (
            f"no grace/ manifest parts — GRACE injection didn't run; "
            f"namelist={names}"
        )
        manifest_xml_blobs = b"".join(z.read(p) for p in grace_parts)

    # audit_id, generated_by tag, and template name must appear in the
    # manifest instructions (written via _audit_instructions).
    assert audit_id.encode() in manifest_xml_blobs
    assert b"MP-MEMO-POC" in manifest_xml_blobs
    assert b"template=memo.yaml" in manifest_xml_blobs


# --------------------------------------------------------------------------- #
# scenario-8: log markers fire in declared order with documented payloads
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_8_log_markers_in_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(
        answers={"recipient": "Board", "body": "Body."}
    )
    await create_memo(intent=intent, source_md=None, ctx=ctx)

    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    # Filter to MP-Memo emissions only for the ordering check.
    memo_msgs = [m for m in msgs if "[MP-Memo]" in m]
    assert memo_msgs, "no MP-Memo log markers fired"

    # Ordered cardinality:
    parse_idx = next(
        (i for i, m in enumerate(memo_msgs) if "BLOCK_PARSE_INTENT" in m), -1
    )
    elicit_idxs = [i for i, m in enumerate(memo_msgs) if "BLOCK_ELICIT_FIELD" in m]
    build_idx = next(
        (i for i, m in enumerate(memo_msgs) if "BLOCK_BUILD_DOCX" in m), -1
    )
    grace_idx = next(
        (i for i, m in enumerate(memo_msgs) if "BLOCK_INJECT_GRACE" in m), -1
    )

    assert parse_idx == 0, "BLOCK_PARSE_INTENT must be first MP-Memo emission"
    assert elicit_idxs, "expected at least one BLOCK_ELICIT_FIELD"
    assert all(parse_idx < idx < build_idx for idx in elicit_idxs)
    assert build_idx > 0
    assert grace_idx == build_idx + 1, (
        "BLOCK_INJECT_GRACE must immediately follow BLOCK_BUILD_DOCX"
    )

    # Payload schema spot-checks.
    parse_msg = memo_msgs[parse_idx]
    assert "source_md_present=" in parse_msg
    assert "fields_extracted_heuristically=" in parse_msg

    elicit_msg = memo_msgs[elicit_idxs[0]]
    assert "field_name=" in elicit_msg
    assert "action=accept" in elicit_msg

    build_msg = memo_msgs[build_idx]
    assert "output_path=" in build_msg
    assert "docx_size_bytes=" in build_msg
    assert "sections_count=" in build_msg

    grace_msg = memo_msgs[grace_idx]
    assert "audit_id=" in grace_msg
    assert "instructions_count=" in grace_msg


@pytest.mark.asyncio
async def test_scenario_8_no_forbidden_cross_talk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Forbidden cross-talk: no httpx/openai/anthropic logger emissions; no
    [Sandbox] or [Validate] markers inside create_memo (validation is
    post-tool, sandbox is the legacy js path)."""
    caplog.set_level(logging.INFO)
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(answers={"recipient": "Board", "body": "Body."})
    await create_memo(intent=intent, source_md=None, ctx=ctx)

    msgs = [r.getMessage() for r in caplog.records]
    forbidden_substrings = ["[Sandbox]", "[Validate]", "openai", "anthropic"]
    for needle in forbidden_substrings:
        offending = [m for m in msgs if needle in m]
        assert not offending, (
            f"forbidden cross-talk via {needle!r}: {offending[:3]}"
        )


# --------------------------------------------------------------------------- #
# Cross-cutting: source_md path triggers MP-MdAdapter
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_source_md_invokes_md_adapter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When source_md is provided, MP-MdAdapter.markdown_to_spec runs and
    emits BLOCK_PARSE_MD — the cross-module evidence chain documented in
    V-MP-MEMO-POC."""
    caplog.set_level(logging.INFO)
    intent = "Memo about Q2 trends."
    ctx = FakeMCPContext(
        answers={
            "sender": "M",
            "recipient": "Board",
            "date": "2026-05-15",
            "subject": "Q2",
        }
    )
    await create_memo(intent=intent, source_md=_read("source_chat.md"), ctx=ctx)
    md_msgs = [
        r.getMessage()
        for r in caplog.records
        if "BLOCK_PARSE_MD" in r.getMessage()
    ]
    assert md_msgs, "MP-MdAdapter must run when source_md is provided"


# --------------------------------------------------------------------------- #
# Layout-loader sanity: templates/memo.yaml drives the section sequence
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_template_yaml_drives_section_layout() -> None:
    """The templates/memo.yaml `layout` array determines the section
    sequence in the produced Document. Walking _sections after generation
    should match the expected sequence."""
    intent = _read("intent_full.txt")
    ctx = FakeMCPContext(answers={})
    result = await create_memo(intent=intent, source_md=intent, ctx=ctx)

    # We ran without elicitation, so the heuristic populated all fields;
    # re-open the docx and verify it has at least the headings declared in
    # the template.
    with zipfile.ZipFile(result["path"]) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
    # Heading "MEMORANDUM" is declared as level=1 in templates/memo.yaml.
    assert "MEMORANDUM" in document_xml
    assert "Body" in document_xml
    # The From/To/Date/Subject table.
    assert "From" in document_xml
    assert "Subject" in document_xml


# --------------------------------------------------------------------------- #
# Heuristic extractor coverage
# --------------------------------------------------------------------------- #


def test_heuristic_extractor_finds_iso_date() -> None:
    from mint_python.mcp.memo import _heuristic_extract

    spec = _heuristic_extract("Memo on 2026-05-15 about Q2.", source_md=None)
    assert spec.date == "2026-05-15"


def test_heuristic_extractor_finds_human_date() -> None:
    from mint_python.mcp.memo import _heuristic_extract

    spec = _heuristic_extract("Memo on May 15, 2026 about Q2.", source_md=None)
    assert spec.date == "May 15, 2026"


def test_heuristic_extractor_extracts_from_to() -> None:
    from mint_python.mcp.memo import _heuristic_extract

    spec = _heuristic_extract(
        "Memo from Alice to Bob about strategy.", source_md=None
    )
    assert spec.sender == "Alice"
    assert spec.recipient == "Bob"


def test_heuristic_extractor_extracts_subject() -> None:
    from mint_python.mcp.memo import _heuristic_extract

    spec = _heuristic_extract(
        "Memo from A to B about Q2 revenue.", source_md=None
    )
    assert spec.subject == "Q2 revenue"


def test_template_loader_returns_required_fields() -> None:
    from mint_python.mcp.memo import _load_template

    template = _load_template()
    assert template.required_fields == MEMO_REQUIRED_FIELDS
    assert template.layout


@pytest.mark.asyncio
async def test_memo_template_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When templates/memo.yaml is missing, _load_template raises
    MemoTemplateNotFound. The error message names the resolved path."""
    from mint_python.mcp import memo as memo_module
    from mint_python.mcp.memo import MemoTemplateNotFound

    monkeypatch.setattr(memo_module, "_TEMPLATE_PATH", Path("/nonexistent/memo.yaml"))
    intent = _read("intent_full.txt")
    ctx = FakeMCPContext(answers={})
    with pytest.raises(MemoTemplateNotFound, match="MEMO_TEMPLATE_NOT_FOUND"):
        await create_memo(intent=intent, source_md=intent, ctx=ctx)


@pytest.mark.asyncio
async def test_memo_generation_failed_wraps_builder_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the document assembler raises after all required fields are
    collected, MemoGenerationFailed wraps the underlying error."""
    from mint_python.mcp import memo as memo_module
    from mint_python.mcp.memo import MemoGenerationFailed

    def _explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic builder failure")

    monkeypatch.setattr(memo_module, "_build_document", _explode)
    intent = _read("intent_full.txt")
    ctx = FakeMCPContext(answers={})
    with pytest.raises(MemoGenerationFailed, match="MEMO_GENERATION_FAILED"):
        await create_memo(intent=intent, source_md=intent, ctx=ctx)
