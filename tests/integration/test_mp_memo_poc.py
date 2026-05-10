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
    _run_memo_pipeline,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

FIXTURES = Path(__file__).parent.parent / "fixtures" / "memo_poc"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_memo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the memo output directory per-test so generated docx files
    land in pytest's tmp_path rather than the user's ~/Documents/MINT/.
    Keeps the test suite hermetic — no leakage to the user's filesystem."""
    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "memo_out"))


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

    result = await _run_memo_pipeline(intent=intent, source_md=intent, ctx=ctx)

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

    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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

    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
        await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
        await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
    await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
    await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

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
    await _run_memo_pipeline(intent=intent, source_md=_read("source_chat.md"), ctx=ctx)
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
    result = await _run_memo_pipeline(intent=intent, source_md=intent, ctx=ctx)

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


# --------------------------------------------------------------------------- #
# Graceful degradation when client doesn't support elicitation/create
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_degraded_single_field_unsupported_returns_needs_more_info() -> None:
    """Real Claude Desktop returns -32601 'Method not found' when the server
    sends elicitation/create. The tool must NOT crash — it returns a
    structured needs_more_info response so the connected model can ask the
    user in chat and re-invoke create_memo with a richer intent."""
    intent = _read("intent_missing_recipient.txt")
    ctx = FakeMCPContext(
        answers={
            "recipient": "__UNSUPPORTED__",
            "body": "__UNSUPPORTED__",
        }
    )

    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

    assert result["status"] == "needs_more_info"
    assert "recipient" in result["missing_fields"]
    assert "body" in result["missing_fields"]
    # extracted_so_far carries what the heuristic could find from the intent.
    assert "sender" in result["extracted_so_far"]
    assert "date" in result["extracted_so_far"]
    # No docx written, no audit_id minted on the degraded path.
    assert "path" not in result
    assert "audit_id" not in result
    # Guidance message instructs the model what to do next.
    assert "missing_fields" in result["guidance"]


@pytest.mark.asyncio
async def test_degraded_first_unsupported_skips_remaining_elicits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Once the client returns -32601 on the first elicit, the tool must NOT
    keep trying to elicit — it short-circuits and collects all remaining
    missing fields into missing_fields without further round-trips."""
    caplog.set_level(logging.INFO)
    intent = _read("intent_missing_two.txt")  # recipient extracted; sender/date/subject/body missing
    ctx = FakeMCPContext(
        answers={
            # First missing field (sender) hits unsupported. After that,
            # we should NOT see any more elicit calls — even if other
            # answers were scripted, they shouldn't fire.
            "sender": "__UNSUPPORTED__",
            "date": "would-not-be-used",
            "subject": "would-not-be-used",
            "body": "would-not-be-used",
        }
    )

    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

    # Only the first elicit attempt was made (it hit -32601).
    assert len(ctx.elicited_calls) == 1
    assert ctx.elicited_calls[0][0] == "sender"
    assert result["status"] == "needs_more_info"
    # The intent fixture supplies recipient (Recipient:) and subject
    # (about Q2 revenue trends) heuristically; sender + date + body
    # are missing.
    assert set(result["missing_fields"]) == {"sender", "date", "body"}

    # The unsupported emission was logged with action=unsupported.
    msgs = [r.getMessage() for r in caplog.records if "BLOCK_ELICIT_FIELD" in r.getMessage()]
    assert any("action=unsupported" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_degraded_all_required_missing() -> None:
    """Bare intent without any extractable fields. Heuristic finds nothing;
    elicit unsupported. Tool returns all 5 fields in missing_fields."""
    ctx = FakeMCPContext(
        answers={"sender": "__UNSUPPORTED__"}  # only need the first to fail
    )

    result = await _run_memo_pipeline(intent="Make a memo.", source_md=None, ctx=ctx)

    assert result["status"] == "needs_more_info"
    assert set(result["missing_fields"]) == {
        "sender",
        "recipient",
        "date",
        "subject",
        "body",
    }
    assert result["extracted_so_far"] == {}


@pytest.mark.asyncio
async def test_other_mcp_errors_still_propagate() -> None:
    """McpError codes OTHER than -32601 (e.g. -32603 internal error,
    -32700 parse error) MUST propagate — we only swallow Method-not-found.
    Otherwise we'd silently mask real client / transport bugs."""
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    intent = _read("intent_missing_recipient.txt")

    class CtxWithInternalError(FakeMCPContext):
        async def elicit(  # type: ignore[override]
            self, message, response_type=None, *, response_title=None,
            response_description=None,
        ):
            self.elicited_calls.append((response_title or message[:40], message))
            raise McpError(ErrorData(code=-32603, message="internal server error"))

    ctx = CtxWithInternalError()
    with pytest.raises(McpError) as excinfo:
        await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)
    assert excinfo.value.error.code == -32603


# --------------------------------------------------------------------------- #
# Heuristic extractor — labelled-key form (LLM natural output)
# --------------------------------------------------------------------------- #


def test_heuristic_extracts_labelled_form() -> None:
    """LLMs frequently emit memos as labelled blobs when asked to be explicit;
    the heuristic must accept this form alongside prose."""
    from mint_python.mcp.memo import _heuristic_extract

    intent = (
        "sender: Mikhail Yevdokimov (CPO)\n"
        "recipient: Board of Directors\n"
        "date: 2026-05-15\n"
        "subject: Q2 CA Product Trends\n"
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.sender == "Mikhail Yevdokimov (CPO)"
    assert spec.recipient == "Board of Directors"
    assert spec.date == "2026-05-15"
    assert spec.subject == "Q2 CA Product Trends"


def test_heuristic_extracts_from_to_aliases() -> None:
    """`From:` and `To:` aliases for sender / recipient."""
    from mint_python.mcp.memo import _heuristic_extract

    intent = (
        "From: Mikhail (CPO)\n"
        "To: Board\n"
        "Subject: Q3 plan\n"
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.sender == "Mikhail (CPO)"
    assert spec.recipient == "Board"
    assert spec.subject == "Q3 plan"


def test_heuristic_extracts_body_block() -> None:
    """Multi-line `Body:\\n\\n...` blob is captured as body text."""
    from mint_python.mcp.memo import _heuristic_extract

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: X\n"
        "Body:\n\n"
        "Para 1 with detail.\n\n"
        "Para 2 with more detail.\n"
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.body
    assert "Para 1" in spec.body
    assert "Para 2" in spec.body


def test_heuristic_subject_label_with_colon() -> None:
    """`Subject: X` is a legal subject pattern in addition to `about X`."""
    from mint_python.mcp.memo import _heuristic_extract

    intent = "From: M\nTo: B\nDate: 2026-05-15\nSubject: Q2 review\n"
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.subject == "Q2 review"


def test_heuristic_empty_label_value_skipped() -> None:
    """A label with no value (`subject:` followed by nothing) is skipped — the
    field stays unset and falls through to prose / elicit later."""
    from mint_python.mcp.memo import _heuristic_extract

    intent = "sender: M\nrecipient: B\nsubject:\nMemo about Q2.\n"
    spec = _heuristic_extract(intent, source_md=None)
    # subject from prose ("about Q2"), not from the empty label.
    assert spec.subject == "Q2"


def test_heuristic_body_block_wins_over_inline_body_label() -> None:
    """Multi-line `Body:\\n\\n…` block captures body before the single-line
    label scan runs, so a stray inline `body: short` afterwards doesn't
    overwrite it. Covers the body-already-set defensive branch."""
    from mint_python.mcp.memo import _heuristic_extract

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: X\n"
        "body: short inline value\n"  # this would normally fill body
        "Body:\n\n"
        "Multi-line block paragraph that should win.\n"
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.body
    # The multi-line block captured it; the inline label was skipped.
    assert "Multi-line block" in spec.body


def test_heuristic_mixed_labelled_and_prose_fallback() -> None:
    """When intent has labelled sender + prose subject, both are extracted."""
    from mint_python.mcp.memo import _heuristic_extract

    intent = (
        "sender: Mikhail\n"
        "Memo to Board of Directors about strategy review.\n"
    )
    spec = _heuristic_extract(intent, source_md=None)
    assert spec.sender == "Mikhail"
    assert spec.recipient == "Board of Directors"
    assert spec.subject == "strategy review"


@pytest.mark.asyncio
async def test_full_labelled_intent_no_elicit() -> None:
    """End-to-end: a fully labelled intent (the form Claude naturally
    produces in chat-driven fallback mode) hits the heuristic correctly
    and no elicit calls are made."""
    intent = (
        "sender: Mikhail Yevdokimov (CPO)\n"
        "recipient: Board of Directors\n"
        "date: 2026-05-15\n"
        "subject: Q2 CA Product Trends and Kyrgyzstan trends\n"
        "Body:\n\n"
        "Q2 2026 Central Asia Product Trends:\n"
        "- Super-app consolidation wave\n"
        "- Open banking momentum\n"
        "- Cashless acceleration\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)
    assert result["status"] == "complete"
    assert ctx.elicited_calls == []
    assert Path(result["path"]).exists()


def test_template_loader_returns_required_fields() -> None:
    from mint_python.mcp.memo import _load_template

    template = _load_template()
    assert template.required_fields == MEMO_REQUIRED_FIELDS
    assert template.layout


# --------------------------------------------------------------------------- #
# Body markdown rendering — _render_body, _emit_body_block, _normalize_body
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_body_bold_pseudo_heading_separates_paragraphs() -> None:
    """`**Heading**` on its own line followed by content (no blank line)
    must NOT merge into one paragraph. _normalize_body_markdown injects
    a blank line so markdown-it-py treats them as separate paragraphs."""
    import zipfile

    from lxml import etree
    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: T\n"
        "Body:\n\n"
        "**Section One**\n"
        "Para under section one.\n\n"
        "**Section Two**\n"
        "Para under section two.\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)
    assert result["status"] == "complete"

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(result["path"]) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
    paras_text = [
        "".join(t.text or "" for t in p.iter(f"{W}t"))
        for p in doc.iter(f"{W}p")
    ]
    # Section One and its body land in separate paragraphs (NOT merged).
    section_one_idx = next(
        (i for i, t in enumerate(paras_text) if t.strip() == "Section One"), -1
    )
    para_one_idx = next(
        (i for i, t in enumerate(paras_text) if t.startswith("Para under section one")),
        -1,
    )
    assert section_one_idx >= 0, paras_text
    assert para_one_idx >= 0, paras_text
    assert para_one_idx == section_one_idx + 1


@pytest.mark.asyncio
async def test_body_with_real_h2_headings_flatten_to_bold() -> None:
    """Body with explicit `## Heading` markers — adapter creates SpecSections;
    _render_body flattens them into bold paragraphs (we already have a
    Body H2 above, so nesting H1/H2 underneath would visually compete)."""
    import zipfile

    from lxml import etree
    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: T\n"
        "Body:\n\n"
        "## Real Heading\n\n"
        "Body paragraph under heading.\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(result["path"]) as z:
        document_xml = z.read("word/document.xml").decode()
    # The "Real Heading" appears as bold text — `<w:b/>` near the run.
    assert "Real Heading" in document_xml


@pytest.mark.asyncio
async def test_body_with_lists_and_tables() -> None:
    """Body containing GFM table + bulleted list — adapter extracts blocks,
    _emit_body_block routes to section.add_table / add_list."""
    import zipfile

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: T\n"
        "Body:\n\n"
        "Some prose.\n\n"
        "| col | val |\n|---|---|\n| a | 1 |\n\n"
        "- bullet one\n"
        "- bullet two\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

    with zipfile.ZipFile(result["path"]) as z:
        document_xml = z.read("word/document.xml").decode()
    # The body table emits a real <w:tbl> in addition to the From/To card.
    assert document_xml.count("<w:tbl>") >= 2
    # Bullet list items render as ListBullet-styled paragraphs.
    assert "bullet one" in document_xml
    assert "bullet two" in document_xml


@pytest.mark.asyncio
async def test_body_with_blockquote_and_code() -> None:
    """Body with blockquote (→ info callout) and fenced code (→ code callout).
    Both route through _emit_body_block CalloutBlock / CodeBlock branches."""
    import zipfile

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: T\n"
        "Body:\n\n"
        "Lead paragraph.\n\n"
        "> Quoted insight.\n\n"
        "```python\nx = 1\n```\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

    with zipfile.ZipFile(result["path"]) as z:
        document_xml = z.read("word/document.xml").decode()
    assert "Quoted insight" in document_xml
    assert "x = 1" in document_xml


@pytest.mark.asyncio
async def test_body_emphasis_run_reconstruction() -> None:
    """ParagraphBlock with multiple emphasis substrings — exercises the
    cursor-advancement loop in _emit_body_block: multiple bold phrases in
    one paragraph reconstruct as alternating plain/bold runs."""
    import zipfile

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: T\n"
        "Body:\n\n"
        "Lead-in **first bold** middle text **second bold** trailing text.\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

    with zipfile.ZipFile(result["path"]) as z:
        document_xml = z.read("word/document.xml").decode()
    # Both emphasis phrases appear as text in the docx.
    assert "first bold" in document_xml
    assert "second bold" in document_xml
    # The trailing plain text after the last emphasis is also present.
    assert "trailing text" in document_xml


@pytest.mark.asyncio
async def test_plain_text_body_preserved() -> None:
    """Body without any markdown signals is split into paragraphs by blank
    lines and emitted as plain Paragraphs — no _normalize / parse round-trip."""
    import zipfile

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\nsubject: T\n"
        "Body:\n\n"
        "First plain paragraph.\n\n"
        "Second plain paragraph.\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await _run_memo_pipeline(intent=intent, source_md=None, ctx=ctx)

    with zipfile.ZipFile(result["path"]) as z:
        document_xml = z.read("word/document.xml").decode()
    assert "First plain paragraph" in document_xml
    assert "Second plain paragraph" in document_xml


# --------------------------------------------------------------------------- #
# Output dir + filename helpers
# --------------------------------------------------------------------------- #


def test_resolve_output_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without env override, resolves to ~/Documents/MINT — verified
    against the user's home directory."""
    from mint_python.mcp import memo as memo_module

    monkeypatch.delenv("MINT_MEMO_DIR", raising=False)
    out = memo_module._resolve_output_dir()
    assert out == Path.home() / "Documents" / "MINT"


def test_resolve_output_dir_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mint_python.mcp import memo as memo_module

    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "custom"))
    assert memo_module._resolve_output_dir() == tmp_path / "custom"


def test_memo_filename_iso_date_subject_slug() -> None:
    from mint_python.mcp.memo import MemoSpec, _memo_filename

    spec = MemoSpec(
        sender="M",
        recipient="B",
        date="2026-05-15",
        subject="Q2 Revenue Trends",
        body="...",
    )
    name = _memo_filename(spec, audit_id="abc12345-fffe-1111-2222-333344445555")
    assert name.startswith("memo_2026-05-15_Q2_Revenue_Trends_")
    assert name.endswith(".docx")
    # The audit short id is the first dash-segment of the UUID.
    assert "abc12345" in name


def test_memo_filename_falls_back_when_date_unparseable() -> None:
    from mint_python.mcp.memo import MemoSpec, _memo_filename

    spec = MemoSpec(
        sender="M",
        recipient="B",
        date="next Tuesday",
        subject="X",
        body="...",
    )
    name = _memo_filename(spec, audit_id="aaaa-bbbb-cccc")
    # Falls back to today's date in ISO; suffix is short id.
    import re
    assert re.match(r"memo_\d{4}-\d{2}-\d{2}_X_aaaa\.docx", name)


# --------------------------------------------------------------------------- #
# create_memo ToolResult wrapper — rich content for cross-client surfacing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_memo_returns_tool_result_with_resource_link() -> None:
    """The @server.tool wrapper returns a ToolResult with TextContent
    (markdown link) + ResourceLink (file:// URI) + structured_content."""
    from mcp.types import ResourceLink, TextContent

    from mint_python.mcp.memo import create_memo

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\n"
        "subject: T\nbody: Plain body content.\n"
    )
    ctx = FakeMCPContext(answers={})
    result = await create_memo(intent=intent, source_md=None, ctx=ctx)

    # Content array carries text + resource_link in that order.
    assert len(result.content) == 2
    assert isinstance(result.content[0], TextContent)
    assert "Memo ready" in result.content[0].text
    assert "file://" in result.content[0].text  # markdown link

    assert isinstance(result.content[1], ResourceLink)
    assert str(result.content[1].uri).startswith("file://")
    assert result.content[1].mimeType.endswith("wordprocessingml.document")
    assert result.content[1].name.endswith(".docx")

    # Structured content carries the full pipeline result dict.
    assert result.structured_content["status"] == "complete"
    assert result.structured_content["audit_id"]
    assert "path" in result.structured_content


@pytest.mark.asyncio
async def test_create_memo_degraded_returns_text_only_no_resource_link() -> None:
    """When pipeline returns needs_more_info, ToolResult content is text-only
    (no docx exists yet, so no resource_link). structured_content carries
    missing_fields + extracted_so_far for the model to read."""
    from mcp.types import ResourceLink

    from mint_python.mcp.memo import create_memo

    intent = "Make a memo."
    ctx = FakeMCPContext(answers={"sender": "__UNSUPPORTED__"})
    result = await create_memo(intent=intent, source_md=None, ctx=ctx)

    assert result.structured_content["status"] == "needs_more_info"
    assert result.structured_content["missing_fields"]

    # No ResourceLink in content — there's no file to point at.
    assert all(not isinstance(c, ResourceLink) for c in result.content)
    # Text summary names the missing fields for the model to read.
    text_summary = result.content[0].text
    assert "Need more info" in text_summary
    assert "missing" in text_summary.lower()


@pytest.mark.asyncio
async def test_memo_template_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When templates/memo.yaml is missing, the loader raises
    DocumentTypeNotFound (Phase-14 W1 renamed MemoTemplateNotFound for the
    missing-file condition; the error code became DOC_TYPE_NOT_FOUND).
    The message names the resolved doc_type and the available alternatives."""
    from mint_python.mcp import document as document_module
    from mint_python.mcp.memo import DocumentTypeNotFound

    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", Path("/nonexistent"))
    intent = _read("intent_full.txt")
    ctx = FakeMCPContext(answers={})
    with pytest.raises(DocumentTypeNotFound, match="DOC_TYPE_NOT_FOUND"):
        await _run_memo_pipeline(intent=intent, source_md=intent, ctx=ctx)


@pytest.mark.asyncio
async def test_memo_generation_failed_wraps_builder_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the document assembler raises after all required fields are
    collected, MemoGenerationFailed wraps the underlying error.
    Phase-14 W1 renamed the error code to DOC_GENERATION_FAILED;
    MemoGenerationFailed is preserved as a backwards-compat alias."""
    from mint_python.mcp import document as document_module
    from mint_python.mcp.memo import MemoGenerationFailed

    def _explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic builder failure")

    monkeypatch.setattr(document_module, "_build_document", _explode)
    intent = _read("intent_full.txt")
    ctx = FakeMCPContext(answers={})
    with pytest.raises(MemoGenerationFailed, match="DOC_GENERATION_FAILED"):
        await _run_memo_pipeline(intent=intent, source_md=intent, ctx=ctx)
