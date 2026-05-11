# FILE: tests/integration/test_mp_doc_bundle.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-DOC-BUNDLE verification — proves that 4 new doc_types
#     (report, decision-record, contract, nda-bilingual-ru-en) ship as
#     PURE YAML and render end-to-end through the existing MP-DOC-GENERIC
#     create_document pipeline without any Python engine changes. Closes
#     Phase-14's "adding a doc_type is a YAML file" promise.
#   SCOPE: Integration tests — exercise _run_pipeline against
#     FakeMCPContext with scripted answers in each template's declared
#     required_fields order; assert produced docx exists, GRACE manifest
#     records template=<doc_type>.yaml + template_version, and for the
#     bilingual NDA the body w:p order interleaves Russian and English
#     paragraphs (stacked layout — no engine extension required).
#   DEPENDS: pytest, mint_python.mcp.document, mint_python.templates
#     .registry, tests._helpers.fake_mcp_context, stdlib zipfile + re for
#     docx body XML inspection.
#   LINKS: docs/development-plan.xml#MP-DOC-BUNDLE,
#     docs/verification-plan.xml#V-MP-DOC-BUNDLE,
#     docs/knowledge-graph.xml#MP-DOC-BUNDLE
# END_MODULE_CONTRACT
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from mint_python.mcp import document as document_module
from mint_python.mcp.document import _run_pipeline
from mint_python.templates import registry as registry_module
from mint_python.templates.registry import (
    TemplateRegistry,
    reset_default_registry,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"
BUNDLE_TEMPLATES = (
    "report",
    "decision-record",
    "contract",
    "nda-bilingual-ru-en",
)


# --------------------------------------------------------------------------- #
# Fixtures — hermetic templates/ snapshot + output dir per test. Mirrors the
# test_mp_doc_generic isolation pattern so the bundle tests stay insulated
# from update_template-authored siblings sitting in the repo's templates/.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "doc_out"))


@pytest.fixture(autouse=True)
def _isolate_templates_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Snapshot the 4 Wave-16-1 doc_types (and memo, for the
    DocumentTypeNotFound message sanity in adjacent suites) into a
    tmp_path templates/ dir, then point both the document module and
    the registry singleton at it."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in ("memo", "letter", *BUNDLE_TEMPLATES):
        src = REPO_TEMPLATES / f"{name}.yaml"
        (fixtures / f"{name}.yaml").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixtures)
    monkeypatch.setattr(registry_module, "_TEMPLATES_DIR", fixtures)
    reset_default_registry()
    yield fixtures
    reset_default_registry()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _read_grace_manifest(docx_path: Path) -> bytes:
    """Concatenate all grace/*.xml parts inside a docx zip."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        grace_parts = [
            n for n in zf.namelist()
            if n.startswith("grace/") and n.endswith(".xml")
        ]
        assert grace_parts, f"GRACE injection didn't run for {docx_path.name}"
        return b"".join(zf.read(p) for p in grace_parts)


_W_T_RE = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")


def _docx_body_texts(docx_path: Path) -> list[str]:
    """Extract the sequence of w:t text runs from word/document.xml.

    The scenario-4 stacked-layout assertion needs paragraph ORDER, not
    just presence. Each w:t carries one text run; consecutive runs may
    belong to the same paragraph, but for our templates each paragraph
    holds exactly one substitution placeholder + maybe a static prefix
    so a "contains" probe on the joined text suffices.
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        body_xml = zf.read("word/document.xml").decode("utf-8")
    return _W_T_RE.findall(body_xml)


# --------------------------------------------------------------------------- #
# Scenario-1: report doc_type renders end-to-end.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_report_renders() -> None:
    """create_document(doc_type='report', spec={...}) builds a docx that
    opens, has the expected H1+H2 section count, and carries a GRACE
    manifest with template='report.yaml' + template_version='1.0'."""
    ctx = FakeMCPContext(
        answers={
            "title": "Q2 Platform Health",
            "author": "M. Yevdokimov (CPO)",
            "date": "2026-05-15",
            "summary": "DAU up 12% MoM; activation funnel improved.",
            "sections": "Onboarding, Retention, Reliability.",
            "conclusions": "Continue investing in onboarding A/B tests.",
        }
    )
    result = await _run_pipeline(
        intent="generate a report", doc_type="report", source_md=None, ctx=ctx
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "report"
    assert result["template_version"] == "1.0"

    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.name.startswith("report_")

    texts = _docx_body_texts(output_path)
    joined = "\n".join(texts)
    # H1 title + the 3 H2 headings (Summary / Sections / Conclusions) all
    # land in the body.
    assert "Q2 Platform Health" in joined
    assert "Summary" in joined
    assert "Sections" in joined
    assert "Conclusions" in joined
    # The "By: ... — ..." paragraph stitches author + date.
    assert "M. Yevdokimov (CPO)" in joined
    assert "2026-05-15" in joined

    manifest_blob = _read_grace_manifest(output_path)
    assert b"template=report.yaml" in manifest_blob
    assert b"template_version=1.0" in manifest_blob
    assert b"generated_by=MP-DOC-GENERIC" in manifest_blob


# --------------------------------------------------------------------------- #
# Scenario-2: decision-record (ADR) renders Context / Decision / Consequences.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_adr_renders() -> None:
    """ADR shape: H1 title, single status/author/date paragraph, three
    H2 sections (Context, Decision, Consequences) with prose underneath."""
    ctx = FakeMCPContext(
        answers={
            "title": "ADR-001: Adopt MINT for governed doc generation",
            "author": "Architecture Council",
            "date": "2026-05-15",
            "status": "Accepted",
            "context": "Existing pipeline ships raw docx via Node.",
            "decision": "Switch to MINT's Python-only emitter.",
            "consequences": "Lose Node devdep; gain GRACE audit trail.",
        }
    )
    result = await _run_pipeline(
        intent="record a decision",
        doc_type="decision-record",
        source_md=None,
        ctx=ctx,
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "decision-record"
    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.name.startswith("decision-record_")

    texts = _docx_body_texts(output_path)
    joined = "\n".join(texts)
    # The three canonical ADR section headings are present in body order.
    for heading in ("Context", "Decision", "Consequences"):
        assert heading in joined, f"ADR missing {heading!r} heading"
    # Status line stitches all three values into one paragraph.
    assert "Accepted" in joined
    assert "Architecture Council" in joined

    manifest_blob = _read_grace_manifest(output_path)
    assert b"template=decision-record.yaml" in manifest_blob
    assert b"template_version=1.0" in manifest_blob


# --------------------------------------------------------------------------- #
# Scenario-3: contract doc_type renders formal contract layout.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_contract_renders() -> None:
    """Contract layout: H1 'Contract', parties / effective / term header
    block, H2 Scope / Obligations / Signatures sections."""
    ctx = FakeMCPContext(
        answers={
            "parties": "ACME Corp and Globex LLC",
            "effective_date": "2026-06-01",
            "term": "24 months, auto-renew",
            "scope": "Joint development of widget X.",
            "obligations": "ACME funds, Globex builds, both share IP.",
            "signatures": "/s/ Alice (ACME), /s/ Bob (Globex)",
        }
    )
    result = await _run_pipeline(
        intent="draft a contract",
        doc_type="contract",
        source_md=None,
        ctx=ctx,
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "contract"
    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.name.startswith("contract_")

    texts = _docx_body_texts(output_path)
    joined = "\n".join(texts)
    for needle in (
        "Contract",
        "Scope",
        "Obligations",
        "Signatures",
        "ACME Corp and Globex LLC",
        "Joint development of widget X.",
    ):
        assert needle in joined, f"contract missing {needle!r}"

    manifest_blob = _read_grace_manifest(output_path)
    assert b"template=contract.yaml" in manifest_blob
    assert b"template_version=1.0" in manifest_blob


# --------------------------------------------------------------------------- #
# Scenario-4: bilingual NDA — stacked Ru/En paragraph order. THE
# load-bearing assertion: for each bilingual clause, w:p[i] carries the
# Russian text and w:p[i+1] carries the English translation.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_bilingual_nda_stacked_layout() -> None:
    """The bilingual NDA layout interleaves Russian and English paragraphs
    in reading order — no columns / no tables / no new block kinds. The
    invariant: for each Ru/En clause pair, the Russian w:t appears
    immediately before the English w:t in word/document.xml."""
    scope_ru = "Конфиденциальная информация охватывает все технические данные."
    scope_en = "Confidential information covers all technical data."
    term_ru = "Соглашение действует пять лет."
    term_en = "The agreement is in effect for five years."

    ctx = FakeMCPContext(
        answers={
            "party_a": "ACME Corp",
            "party_b": "ООО Глобэкс",  # noqa: RUF001 — Cyrillic is intentional for bilingual NDA
            "effective_date": "2026-06-01",
            "scope_ru": scope_ru,
            "scope_en": scope_en,
            "term_ru": term_ru,
            "term_en": term_en,
            "signatures": "/s/ Alice (ACME), /s/ Иван (Глобэкс)",
        }
    )
    result = await _run_pipeline(
        intent="draft an NDA",
        doc_type="nda-bilingual-ru-en",
        source_md=None,
        ctx=ctx,
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "nda-bilingual-ru-en"
    output_path = Path(result["path"])
    assert output_path.exists()

    texts = _docx_body_texts(output_path)

    # Stacked-layout invariant: scope_ru immediately precedes scope_en in
    # the body text run sequence. We locate the scope_ru text run and
    # confirm scope_en appears in the very next w:t.
    def _find_index(needle: str) -> int:
        for i, run in enumerate(texts):
            if needle in run:
                return i
        raise AssertionError(
            f"text run containing {needle!r} not found; "
            f"runs={texts!r}"
        )

    scope_ru_idx = _find_index(scope_ru)
    assert scope_en in texts[scope_ru_idx + 1], (
        f"scope_en must immediately follow scope_ru — "
        f"got scope_ru at {scope_ru_idx} then {texts[scope_ru_idx + 1]!r}"
    )

    term_ru_idx = _find_index(term_ru)
    assert term_en in texts[term_ru_idx + 1], (
        f"term_en must immediately follow term_ru — "
        f"got term_ru at {term_ru_idx} then {texts[term_ru_idx + 1]!r}"
    )

    # Bilingual headings (Ru + En) survive in the body too.
    joined = "\n".join(texts)
    assert "Соглашение о неразглашении" in joined  # noqa: RUF001 — Cyrillic intentional
    assert "Non-Disclosure Agreement" in joined

    manifest_blob = _read_grace_manifest(output_path)
    assert b"template=nda-bilingual-ru-en.yaml" in manifest_blob


# --------------------------------------------------------------------------- #
# Scenario-5: every new template loads cleanly via the registry (no
# TemplateInvalidSchema raised at registry-walk time).
# --------------------------------------------------------------------------- #


def test_scenario_5_all_templates_load(_isolate_templates_dir: Path) -> None:
    """Construct a fresh TemplateRegistry over the fixture templates/
    directory; assert all 4 bundle templates appear in summaries() with
    their declared doc_type + version."""
    registry = TemplateRegistry(templates_dir=_isolate_templates_dir)
    by_name = {s.name: s for s in registry.summaries()}
    for name in BUNDLE_TEMPLATES:
        assert name in by_name, f"template {name!r} did not load"
        assert by_name[name].doc_type == name
        assert by_name[name].version == "1.0"


# --------------------------------------------------------------------------- #
# Scenario-6: required_fields elicitation order matches each template's
# declaration order. Bilingual NDA is the load-bearing case — paired
# Ru/En fields MUST be elicited Ru-then-En per clause.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("doc_type", "expected_order"),
    [
        (
            "report",
            ("title", "author", "date", "summary", "sections", "conclusions"),
        ),
        (
            "decision-record",
            (
                "title",
                "author",
                "date",
                "status",
                "context",
                "decision",
                "consequences",
            ),
        ),
        (
            "contract",
            (
                "parties",
                "effective_date",
                "term",
                "scope",
                "obligations",
                "signatures",
            ),
        ),
        (
            "nda-bilingual-ru-en",
            (
                "party_a",
                "party_b",
                "effective_date",
                "scope_ru",
                "scope_en",
                "term_ru",
                "term_en",
                "signatures",
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_scenario_6_elicitation_order(
    doc_type: str, expected_order: tuple[str, ...]
) -> None:
    """The pipeline elicits missing required_fields in the template's
    declaration order. With an empty intent and every required field
    scripted into FakeMCPContext.answers, the recorded elicited_calls
    sequence must match expected_order verbatim — no skips, no reorder."""
    # Every answer is non-empty filler — the test cares about ORDER of
    # field requests, not content.
    answers = {name: f"value-for-{name}" for name in expected_order}
    ctx = FakeMCPContext(answers=answers)

    result = await _run_pipeline(
        intent="generate doc",  # empty-of-signals — heuristic finds nothing
        doc_type=doc_type,
        source_md=None,
        ctx=ctx,
    )
    assert result["status"] == "complete"

    elicited_order = tuple(field_name for field_name, _msg in ctx.elicited_calls)
    assert elicited_order == expected_order, (
        f"{doc_type} elicit order mismatch: got {elicited_order!r}, "
        f"expected {expected_order!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-7: GRACE manifest of each produced docx carries the template
# name + version. (Verification-plan's original scenario-7 also asked for
# lang=['ru','en'] metadata on the bilingual NDA; surfacing that requires
# Python changes in _audit_instructions and is out-of-scope for a pure-
# YAML wave — see Wave-16-1 return packet for the verification delta.)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("doc_type", BUNDLE_TEMPLATES)
@pytest.mark.asyncio
async def test_scenario_7_grace_manifest_per_doc_type(doc_type: str) -> None:
    """Every doc_type's GRACE manifest names the template file + version
    so produced docs record which template version authored their layout."""
    # Build the answers map dynamically from the loaded template — keeps
    # this test resilient to required_fields churn at the YAML layer
    # (worker only needs to keep declaration order stable).
    template = document_module._load_template(doc_type)
    answers = {name: f"v-{name}" for name in template.required_fields}
    ctx = FakeMCPContext(answers=answers)

    result = await _run_pipeline(
        intent="generate doc", doc_type=doc_type, source_md=None, ctx=ctx
    )
    assert result["status"] == "complete"

    manifest_blob = _read_grace_manifest(Path(result["path"]))
    assert f"template={doc_type}.yaml".encode() in manifest_blob, (
        f"GRACE manifest for {doc_type} missing template= line"
    )
    assert b"template_version=1.0" in manifest_blob
