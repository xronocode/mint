# FILE: tests/integration/test_mp_visual_qa_hook.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-VISUAL-QA-HOOK + VF-019 verification — covers the 6
#     scenario requirements + the 5 VF-019 invariants for the advisory
#     post-create_document visual quality gate (MP-VISUAL-QA-HOOK).
#   SCOPE: Integration tests — exercise the full create_document pipeline
#     via FakeMCPContext with the real urlopen call monkeypatched to
#     fake_vlm_urlopen, and shutil.which / urlopen monkeypatched via the
#     controller-provided backend_probe_patcher fixture for VF-019 inv-4.
#   DEPENDS: pytest, mint_python.mcp.document (create_document, _run_pipeline),
#     mint_python.qa.visual (score_document, VisualQAReport),
#     tests._helpers.fake_mcp_context, tests._helpers.fake_vlm.
# END_MODULE_CONTRACT
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from mint_python.mcp.document import create_document
from mint_python.qa.visual import VisualQAReport, score_document
from tests._helpers.fake_mcp_context import FakeMCPContext
from tests._helpers.fake_vlm import FakeVlmRecorder, fake_vlm_urlopen
from tests.unit._mp_helpers import assert_marker_sequence, extract_marker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "memo_poc"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _full_intent() -> str:
    return _read("intent_full.txt")


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic output dir per test — same pattern as test_mp_memo_poc.py."""
    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "qa_out"))


@pytest.fixture(autouse=True)
def _caplog_info(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO)
    return caplog


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, variant: str,
                   recorder: FakeVlmRecorder | None = None) -> None:
    """Patch urllib.request.urlopen with the fake VLM. The qa.visual
    module calls `urllib.request.urlopen(...)`, so patching the module
    attribute is sufficient."""
    import urllib.request

    fake = fake_vlm_urlopen(variant, recorder=recorder)  # type: ignore[arg-type]
    monkeypatch.setattr(urllib.request, "urlopen", fake)


def _stub_renderers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                    *, pages: int = 1) -> None:
    """Stub the soffice + pdftoppm subprocesses out of the score_document
    path. Tests that don't care about the real rendering toolchain (i.e.
    every test except VF-019 inv-5/6 which exercise real-file invariants
    and have to drive both real-success and missing-backend paths) consume
    this so the test runs deterministically + fast irrespective of host
    soffice / Java state."""
    import subprocess

    from mint_python.qa import visual as visual_mod

    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-stub")
    png_paths: list[Path] = []
    for i in range(1, pages + 1):
        p = tmp_path / f"stub-page-{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        png_paths.append(p)

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0),
    )
    monkeypatch.setattr(visual_mod, "_render_pdf", lambda d, w: pdf_path)
    monkeypatch.setattr(
        visual_mod, "_render_pngs", lambda p, w, m: list(png_paths)
    )
    monkeypatch.setattr(visual_mod, "_probe_backends", lambda: None)


# ------------------------------------------------------------------ #
# scenario-1: success (above-threshold)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_scenario_1_hook_attaches_visual_qa_to_structured_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _stub_renderers(monkeypatch, tmp_path)
    rec = FakeVlmRecorder()
    _patch_urlopen(monkeypatch, "above_threshold", recorder=rec)

    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    assert result.structured_content is not None
    assert result.structured_content["status"] == "complete"
    qa = result.structured_content["visual_qa"]
    assert qa["score"] >= 70
    assert set(qa["axes"]) == {"typography", "layout", "visual_richness", "professional"}
    assert qa["advisory"] is True
    assert qa["skipped"] is False
    assert qa["preset_name"] == "klawd"
    assert qa["pages_scored"] >= 1
    # Recorder captured at least one VLM round-trip.
    assert rec.captured_bodies


# ------------------------------------------------------------------ #
# scenario-2: success (below-threshold) — WARNING but tool succeeds
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_scenario_2_below_threshold_logs_warning_but_tool_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _stub_renderers(monkeypatch, tmp_path)
    _patch_urlopen(monkeypatch, "below_threshold")

    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    assert result.structured_content is not None
    assert result.structured_content["status"] == "complete"
    qa = result.structured_content["visual_qa"]
    assert qa["score"] < 70
    assert qa["skipped"] is False
    # WARNING marker fired:
    markers = [extract_marker(r.getMessage()) for r in caplog.records]
    assert "BLOCK_QA_BELOW_THRESHOLD" in markers


# ------------------------------------------------------------------ #
# scenario-3: env-skip — visual_qa key absent entirely
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_scenario_3_skip_env_omits_visual_qa_key_entirely(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("MINT_SKIP_VISUAL_QA", "1")

    # Sentinel: shutil.which MUST NOT be called when MINT_SKIP_VISUAL_QA=1.
    import shutil
    sentinel_calls: list[str] = []

    def _trip_which(cmd: str, *args: object, **kwargs: object) -> None:
        sentinel_calls.append(cmd)
        return None  # if hit, force the BACKEND-UNAVAILABLE path so the
        # assertion below would still fail visibly.

    monkeypatch.setattr(shutil, "which", _trip_which)

    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    assert result.structured_content is not None
    assert "visual_qa" not in result.structured_content, (
        "scenario-3 forbids the visual_qa key when MINT_SKIP_VISUAL_QA=1; "
        "got " + repr(result.structured_content.get("visual_qa"))
    )
    # No backend probes executed — sentinel never tripped.
    assert sentinel_calls == [], (
        f"shutil.which should not be invoked when env-skip active; "
        f"got {sentinel_calls!r}"
    )
    # No QA log markers fired.
    qa_markers = [
        m for m in (extract_marker(r.getMessage()) for r in caplog.records)
        if m and m.startswith("BLOCK_QA_")
    ]
    assert qa_markers == [], f"unexpected QA markers under env-skip: {qa_markers!r}"


# ------------------------------------------------------------------ #
# scenario-4: backend unavailable — 3 sub-variants
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_scenario_4_a_soffice_missing(
    backend_probe_patcher,
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend_probe_patcher(missing={"soffice"})
    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )
    assert result.structured_content["status"] == "complete"
    qa = result.structured_content["visual_qa"]
    assert qa["skipped"] is True
    assert "soffice" in (qa["skip_reason"] or "")
    assert qa["advisory"] is True
    markers = [extract_marker(r.getMessage()) for r in caplog.records]
    assert "BLOCK_QA_BACKEND_UNAVAILABLE" in markers


@pytest.mark.asyncio
async def test_scenario_4_b_pdftoppm_missing(
    backend_probe_patcher,
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend_probe_patcher(missing={"pdftoppm"})
    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )
    assert result.structured_content["status"] == "complete"
    qa = result.structured_content["visual_qa"]
    assert qa["skipped"] is True
    assert "pdftoppm" in (qa["skip_reason"] or "")
    markers = [extract_marker(r.getMessage()) for r in caplog.records]
    assert "BLOCK_QA_BACKEND_UNAVAILABLE" in markers


@pytest.mark.asyncio
async def test_scenario_4_c_ollama_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    backend_probe_patcher,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ollama_unreachable=True patches urlopen to raise ConnectionError on
    # the first VLM call. soffice / pdftoppm probes still pass; the
    # failure surfaces at the VLM call (we stub the rendering subprocesses
    # so this test doesn't depend on a working host soffice).
    _stub_renderers(monkeypatch, tmp_path)
    backend_probe_patcher(ollama_unreachable=True)
    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )
    assert result.structured_content["status"] == "complete"
    qa = result.structured_content["visual_qa"]
    assert qa["skipped"] is True
    assert "ollama" in (qa["skip_reason"] or "").lower()
    markers = [extract_marker(r.getMessage()) for r in caplog.records]
    assert "BLOCK_QA_BACKEND_UNAVAILABLE" in markers


# ------------------------------------------------------------------ #
# scenario-5: BLOCK_QA_SCORE payload + position after BLOCK_INJECT_GRACE
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_scenario_5_block_qa_score_payload_and_position_after_grace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _stub_renderers(monkeypatch, tmp_path)
    _patch_urlopen(monkeypatch, "above_threshold")
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    # Find the QA_SCORE record and verify its payload shape.
    qa_score_records = [
        r for r in caplog.records
        if extract_marker(r.getMessage()) == "BLOCK_QA_SCORE"
    ]
    assert len(qa_score_records) == 1
    msg = qa_score_records[0].getMessage()
    assert "preset=klawd" in msg
    assert "pages_scored=" in msg
    assert "score=" in msg
    assert "axes=" in msg

    # Position assertion: BLOCK_INJECT_GRACE must precede BLOCK_QA_SCORE.
    assert_marker_sequence(
        caplog,
        ["BLOCK_INJECT_GRACE", "BLOCK_QA_SCORE"],
        strict=False,
    )


# ------------------------------------------------------------------ #
# scenario-6: VLM garbage / malformed JSON — does not raise
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_scenario_6_vlm_response_garbage_returns_none_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """malformed_json: VLM returns non-parseable content. score_document
    treats per-page parse failure as zero-axes (the `_call_vlm` fallback);
    the aggregate is computed at zeros and the report is emitted with
    skipped=False (the renderer + transport succeeded). The hook does NOT
    raise. Documenting this choice: parse-failure-as-zero is the safer
    advisory signal for ops than dropping the QA payload entirely — the
    `issues` list carries the raw VLM response head for triage."""
    _stub_renderers(monkeypatch, tmp_path)
    _patch_urlopen(monkeypatch, "malformed_json")
    ctx = FakeMCPContext(answers={})
    # NEVER raises into the caller (VF-019 inv-1):
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )
    assert result.structured_content["status"] == "complete"
    # visual_qa key present (skipped=False; we got a parseable transport,
    # just garbage axes). Issues list captures the unparseable payload.
    qa = result.structured_content["visual_qa"]
    assert qa["advisory"] is True
    assert qa["skipped"] is False
    assert any("VLM returned unparseable" in i for i in qa["issues"])


# ------------------------------------------------------------------ #
# VF-019 inv-2: POSITION-AFTER-GRACE
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_vf_019_inv_2_qa_runs_after_grace_injection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _stub_renderers(monkeypatch, tmp_path)
    _patch_urlopen(monkeypatch, "above_threshold")
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )
    # Real marker name is BLOCK_INJECT_GRACE; the verification plan
    # references BLOCK_INJECT_MANIFEST — discrepancy noted in the result
    # packet for the post-wave delta.
    assert_marker_sequence(
        caplog,
        ["BLOCK_INJECT_GRACE", "BLOCK_QA_SCORE"],
        strict=False,
    )


# ------------------------------------------------------------------ #
# VF-019 inv-5: NO-DOC-MUTATION
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_vf_019_inv_5_hook_does_not_mutate_docx(
    monkeypatch: pytest.MonkeyPatch,
    zip_byte_snapshot,
    backend_probe_patcher,
    tmp_path: Path,
) -> None:
    """For each scenario variant: build a docx once, snapshot bytes, then
    run score_document directly (the hook integration is exercised by the
    scenarios above; this test isolates the score_document invocation so
    we can snapshot the exact docx bytes before/after the call)."""
    # Stub the renderers so this test doesn't depend on real soffice.
    _stub_renderers(monkeypatch, tmp_path)

    # Build a saved docx via the happy path first.
    import urllib.request
    real_urlopen = urllib.request.urlopen
    monkeypatch.setattr(
        urllib.request, "urlopen",
        fake_vlm_urlopen("above_threshold"),  # type: ignore[arg-type]
    )

    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )
    saved_path = Path(result.structured_content["path"])
    assert saved_path.exists()

    # Now exercise score_document under each variant and verify bytes
    # never change. Snapshot at the boundary of each call.
    for variant in ("above_threshold", "below_threshold", "malformed_json"):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            fake_vlm_urlopen(variant),  # type: ignore[arg-type]
        )
        snap = zip_byte_snapshot(saved_path)
        score_document(saved_path, preset_name="klawd")
        snap()

    # Backend-unavailable + env-skip variants: these never touch the docx.
    monkeypatch.setattr(urllib.request, "urlopen", real_urlopen)
    backend_probe_patcher(missing={"soffice"})
    snap = zip_byte_snapshot(saved_path)
    score_document(saved_path, preset_name="klawd")
    snap()

    backend_probe_patcher(missing={"pdftoppm"})
    snap = zip_byte_snapshot(saved_path)
    score_document(saved_path, preset_name="klawd")
    snap()

    backend_probe_patcher(missing=set(), ollama_unreachable=True)
    snap = zip_byte_snapshot(saved_path)
    score_document(saved_path, preset_name="klawd")
    snap()


# ------------------------------------------------------------------ #
# VF-019 inv-6: TEMP-FILE-CLEANUP
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_vf_019_inv_6_no_tempfile_leak(
    monkeypatch: pytest.MonkeyPatch,
    tempdir_snapshot,
    backend_probe_patcher,
    tmp_path: Path,
) -> None:
    """No `mint_qa_*` tempdir entries leak across any of the scenarios."""
    _stub_renderers(monkeypatch, tmp_path)
    snap = tempdir_snapshot("mint_qa_*")

    import urllib.request
    real_urlopen = urllib.request.urlopen

    # success path
    monkeypatch.setattr(
        urllib.request, "urlopen",
        fake_vlm_urlopen("above_threshold"),  # type: ignore[arg-type]
    )
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    # below-threshold
    monkeypatch.setattr(
        urllib.request, "urlopen",
        fake_vlm_urlopen("below_threshold"),  # type: ignore[arg-type]
    )
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    # malformed_json
    monkeypatch.setattr(
        urllib.request, "urlopen",
        fake_vlm_urlopen("malformed_json"),  # type: ignore[arg-type]
    )
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    # backend-unavailable: no tempdir created at all (probes fail first).
    monkeypatch.setattr(urllib.request, "urlopen", real_urlopen)
    backend_probe_patcher(missing={"soffice"})
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    # ollama unreachable
    backend_probe_patcher(missing=set(), ollama_unreachable=True)
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )

    snap()  # asserts no leftover mint_qa_* entries.


# ------------------------------------------------------------------ #
# VF-019 inv-7: NO-CALLER-INPUT-IN-VLM-PAYLOAD
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_vf_019_inv_7_no_caller_input_in_vlm_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Inject a sentinel into the create_document caller surface and verify
    NO captured VLM request body contains it. Three injection points:
    intent, source_md, and the elicited `subject` value."""
    _stub_renderers(monkeypatch, tmp_path)

    sentinel = b"MINT_TEST_INTENT_SENTINEL_42"
    sentinel_str = sentinel.decode()

    rec = FakeVlmRecorder()
    _patch_urlopen(monkeypatch, "above_threshold", recorder=rec)

    # 1) Sentinel via intent
    intent_with = (
        f"sender: M\nrecipient: B\ndate: 2026-05-15\n"
        f"subject: T\nbody: body of memo about {sentinel_str}.\n"
    )
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=intent_with, doc_type="memo", source_md=None, ctx=ctx
    )

    # 2) Sentinel via source_md
    rec2 = FakeVlmRecorder()
    _patch_urlopen(monkeypatch, "above_threshold", recorder=rec2)
    intent_clean = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\n"
        "subject: T\nbody: clean body.\n"
    )
    source_md_with = f"Body details {sentinel_str} more text here."
    ctx = FakeMCPContext(answers={})
    await create_document(
        intent=intent_clean, doc_type="memo", source_md=source_md_with, ctx=ctx
    )

    # 3) Sentinel via elicited subject
    rec3 = FakeVlmRecorder()
    _patch_urlopen(monkeypatch, "above_threshold", recorder=rec3)
    # Bare-bones intent forces all five fields to be elicited.
    intent_bare = "Memo about TBD"
    ctx = FakeMCPContext(
        answers={
            "sender": "Alice",
            "recipient": "Bob",
            "date": "2026-05-15",
            "subject": f"Topic {sentinel_str}",
            "body": "Body.",
        }
    )
    await create_document(
        intent=intent_bare, doc_type="memo", source_md=None, ctx=ctx
    )

    # No body across the three runs may contain the sentinel.
    for rec_obj in (rec, rec2, rec3):
        assert rec_obj.captured_bodies, "expected at least one VLM round-trip"
        for body in rec_obj.captured_bodies:
            assert sentinel not in body, (
                f"VF-019 inv-7 violation: sentinel {sentinel_str!r} "
                f"appeared in VLM request body"
            )


# ------------------------------------------------------------------ #
# VF-019 inv-1 ADVISORY-ONLY — explicit cross-variant assertion
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_vf_019_inv_1_advisory_only_across_all_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    backend_probe_patcher,
    tmp_path: Path,
) -> None:
    """For every fake_vlm variant + env-skip + backend-missing path: the
    create_document tool succeeds and NEVER raises. visual_qa, when
    present, always carries advisory=True."""
    _stub_renderers(monkeypatch, tmp_path)
    import urllib.request
    real_urlopen = urllib.request.urlopen

    # 4 fake_vlm variants
    for variant in ("above_threshold", "below_threshold", "malformed_json", "http_error"):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            fake_vlm_urlopen(variant),  # type: ignore[arg-type]
        )
        ctx = FakeMCPContext(answers={})
        result = await create_document(
            intent=_full_intent(), doc_type="memo",
            source_md=_full_intent(), ctx=ctx,
        )
        assert result.structured_content["status"] == "complete"
        if "visual_qa" in result.structured_content:
            assert result.structured_content["visual_qa"]["advisory"] is True

    monkeypatch.setattr(urllib.request, "urlopen", real_urlopen)

    # env-skip case
    monkeypatch.setenv("MINT_SKIP_VISUAL_QA", "1")
    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo",
        source_md=_full_intent(), ctx=ctx,
    )
    assert result.structured_content["status"] == "complete"
    assert "visual_qa" not in result.structured_content
    monkeypatch.delenv("MINT_SKIP_VISUAL_QA")

    # backend-missing case (soffice)
    backend_probe_patcher(missing={"soffice"})
    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo",
        source_md=_full_intent(), ctx=ctx,
    )
    assert result.structured_content["status"] == "complete"
    assert result.structured_content["visual_qa"]["advisory"] is True


# ------------------------------------------------------------------ #
# Library-level coverage: score_document return-shape contracts.
# These exercise the public API directly to lock in the dataclass
# contract documented in the V-MP-VISUAL-QA-HOOK public-interface
# section without going through the full create_document pipeline.
# ------------------------------------------------------------------ #


def test_score_document_env_skip_returns_none(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    monkeypatch.setenv("MINT_SKIP_VISUAL_QA", "1")
    fake_doc = tmp_path / "x.docx"
    fake_doc.write_bytes(b"")  # not actually opened in env-skip path
    assert score_document(fake_doc) is None


def test_score_document_backend_missing_returns_skipped_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **kw: None)
    fake_doc = tmp_path / "x.docx"
    fake_doc.write_bytes(b"")
    report = score_document(fake_doc)
    assert isinstance(report, VisualQAReport)
    assert report.skipped is True
    assert report.advisory is True
    assert report.skip_reason is not None


def test_score_document_render_failure_skipped_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Force soffice subprocess to fail — score_document collapses to a
    skipped report with a render_failed reason."""
    import shutil
    import subprocess
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **kw: f"/fake/{cmd}")

    def _boom(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(returncode=1, cmd="soffice")

    monkeypatch.setattr(subprocess, "run", _boom)
    fake_doc = tmp_path / "x.docx"
    fake_doc.write_bytes(b"")
    report = score_document(fake_doc)
    assert isinstance(report, VisualQAReport)
    assert report.skipped is True
    assert "render_failed" in (report.skip_reason or "")


def test_score_document_no_pages_rendered_skipped_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """soffice + pdftoppm both succeed but produce no PNGs — collapse to
    skipped report with no_pages_rendered reason."""
    import shutil
    import subprocess

    from mint_python.qa import visual as visual_mod

    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **kw: f"/fake/{cmd}")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0),
    )
    monkeypatch.setattr(
        visual_mod, "_render_pdf",
        lambda docx, out: out / "x.pdf",
    )
    monkeypatch.setattr(visual_mod, "_render_pngs", lambda pdf, out, m: [])

    fake_doc = tmp_path / "x.docx"
    fake_doc.write_bytes(b"")
    report = score_document(fake_doc)
    assert isinstance(report, VisualQAReport)
    assert report.skipped is True
    assert report.skip_reason == "no_pages_rendered"


def test_score_document_unexpected_exception_returns_skipped_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Any unexpected exception inside score_document (outside the
    inner render + VLM catches) collapses to a skipped report (final
    defense-in-depth guard for VF-019 inv-1 ADVISORY-ONLY)."""
    import shutil

    from mint_python.qa import visual as visual_mod

    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **kw: f"/fake/{cmd}")

    fake_pdf = tmp_path / "stub.pdf"
    fake_pdf.write_bytes(b"%PDF-")
    fake_png = tmp_path / "page-1.png"
    fake_png.write_bytes(b"\x89PNG")

    monkeypatch.setattr(visual_mod, "_render_pdf", lambda d, w: fake_pdf)
    monkeypatch.setattr(visual_mod, "_render_pngs", lambda p, w, m: [fake_png])
    monkeypatch.setattr(visual_mod, "_call_vlm",
                        lambda *a, **kw: {"typography": 80, "layout": 80,
                                          "visual_richness": 80,
                                          "professional": 80, "issues": []})

    def _aggregate_boom(*args: object, **kwargs: object) -> None:
        raise ValueError("aggregate boom")

    monkeypatch.setattr(visual_mod, "_aggregate", _aggregate_boom)

    fake_doc = tmp_path / "x.docx"
    fake_doc.write_bytes(b"")
    report = score_document(fake_doc)
    assert isinstance(report, VisualQAReport)
    assert report.skipped is True
    assert "unexpected" in (report.skip_reason or "")


@pytest.mark.asyncio
async def test_hook_outer_defense_in_depth_swallows_unexpected_score_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hook caller in _run_pipeline wraps _score_document in a
    try/except Exception (defense-in-depth per VF-019 inv-1). Even though
    score_document promises never to raise, a regression that breaks that
    contract MUST NOT propagate into create_document. Force the outer
    handler to fire by patching _score_document on the imported alias."""
    from mint_python.mcp import document as document_module

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("contract violated for testing")

    monkeypatch.setattr(document_module, "_score_document", _boom)

    ctx = FakeMCPContext(answers={})
    result = await create_document(
        intent=_full_intent(), doc_type="memo", source_md=_full_intent(), ctx=ctx
    )
    assert result.structured_content["status"] == "complete"
    assert "visual_qa" not in result.structured_content
    markers = [extract_marker(r.getMessage()) for r in caplog.records]
    assert "BLOCK_QA_BACKEND_UNAVAILABLE" in markers


def test_render_pdf_invokes_soffice_and_returns_pdf_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Cover the _render_pdf body: subprocess.run invocation + the
    `if not pdf.exists()` guard that raises RuntimeError."""
    import subprocess

    from mint_python.qa import visual as visual_mod

    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        # Honor the real soffice contract: emit <stem>.pdf in --outdir.
        out_idx = cmd.index("--outdir")
        out_dir = Path(cmd[out_idx + 1])
        src = Path(cmd[-1])
        (out_dir / (src.stem + ".pdf")).write_bytes(b"%PDF-")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    src = tmp_path / "doc.docx"
    src.write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    pdf = visual_mod._render_pdf(src, out)
    assert pdf == out / "doc.pdf"
    assert captured["cmd"][0] == "soffice"
    assert "--headless" in captured["cmd"]


def test_render_pdf_raises_when_soffice_silently_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If soffice exits 0 but doesn't produce the pdf, _render_pdf raises
    RuntimeError so the caller's render-block catch can degrade."""
    import subprocess

    from mint_python.qa import visual as visual_mod

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0),
    )
    src = tmp_path / "doc.docx"
    src.write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(RuntimeError, match="soffice did not produce"):
        visual_mod._render_pdf(src, out)


def test_render_pngs_invokes_pdftoppm_and_returns_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Cover the _render_pngs body: pdftoppm invocation + glob sort."""
    import subprocess

    from mint_python.qa import visual as visual_mod

    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        out_dir = Path(cmd[-2]).parent if False else None  # placeholder
        # The base path is the second-to-last positional arg in the call;
        # base = out_dir / "page", so seed the glob with two pages.
        base = Path(cmd[-1])
        out_dir = base.parent
        (out_dir / "page-1.png").write_bytes(b"\x89PNG-1")
        (out_dir / "page-2.png").write_bytes(b"\x89PNG-2")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-")
    out = tmp_path / "rend"
    out.mkdir()
    pngs = visual_mod._render_pngs(pdf, out, 5)
    assert len(pngs) == 2
    assert all(p.suffix == ".png" for p in pngs)
    assert captured["cmd"][0] == "pdftoppm"


def test_call_vlm_recovers_json_via_regex_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When the VLM wraps its JSON in stray prose without code fences
    (so the strip-fence regex leaves a non-JSON outer string), the
    inner regex `\\{.*\\}` extracts the embedded object."""
    import urllib.request

    from mint_python.qa import visual as visual_mod

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def read(self) -> bytes:
            payload = {
                "message": {
                    "content": (
                        "Sure, here is the score. "
                        '{"typography": 91, "layout": 91, '
                        '"visual_richness": 91, "professional": 91, '
                        '"issues": []}'
                        " Hope this helps!"
                    )
                }
            }
            return json.dumps(payload).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Resp())

    png = tmp_path / "p.png"
    png.write_bytes(b"\x89PNG")
    out = visual_mod._call_vlm(png, 1, 1)
    assert out["typography"] == 91


def test_score_document_happy_path_with_fake_vlm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Drive score_document directly with stubs around the rendering
    subprocesses + the fake VLM. Exercises the aggregate path + the
    BLOCK_QA_SCORE log marker without needing real soffice / pdftoppm."""
    import shutil
    import subprocess
    import urllib.request

    from mint_python.qa import visual as visual_mod

    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **kw: f"/fake/{cmd}")
    fake_pdf = tmp_path / "x.pdf"
    fake_pdf.write_bytes(b"%PDF-")
    fake_png = tmp_path / "page-1.png"
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0),
    )
    monkeypatch.setattr(visual_mod, "_render_pdf", lambda d, w: fake_pdf)
    monkeypatch.setattr(visual_mod, "_render_pngs", lambda p, w, m: [fake_png])
    monkeypatch.setattr(
        urllib.request, "urlopen",
        fake_vlm_urlopen("above_threshold"),  # type: ignore[arg-type]
    )

    fake_doc = tmp_path / "x.docx"
    fake_doc.write_bytes(b"")
    report = score_document(fake_doc, preset_name="klawd")
    assert isinstance(report, VisualQAReport)
    assert report.skipped is False
    assert report.score >= 70
    assert report.preset_name == "klawd"
    assert report.pages_scored == 1
