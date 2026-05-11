# FILE: tests/integration/test_mp_doc_tech_spec.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-DOC-TECH-SPEC verification — proves that templates/
#     technical-spec.yaml ships a fully working engineering-doc doc_type
#     using all 5 engine-supported layout block kinds (heading / spacer /
#     paragraph / table / callout) and stays anonymity-friendly (no
#     sender / author in required_fields). Demonstrates that the
#     create_document pipeline genuinely supports `kind: table` and
#     `kind: callout` from YAML, which V-MP-DOC-BUNDLE forbidden-1
#     mis-froze in Phase-16; Phase-17 W17-1 amends + this suite pins.
#   SCOPE: Integration tests — exercise _run_pipeline against
#     FakeMCPContext with scripted answers, assert produced docx exists,
#     OOXML round-trips through mint_python.ooxml.unpack, GRACE manifest
#     names template='technical-spec.yaml', and the walker's behaviour
#     at every block-kind edge (ragged, empty, unknown, unicode, multi-
#     section, default callout kind, structured-field stringification,
#     uniform _substitute application) is locked.
#   DEPENDS: pytest, mint_python.mcp.document, mint_python.templates
#     .registry, mint_python.ooxml.unpack, mint_python.core.table
#     .TableRaggedRowsError, tests._helpers.fake_mcp_context, stdlib
#     zipfile + re for docx body XML inspection.
#   LINKS: docs/development-plan.xml#MP-DOC-TECH-SPEC,
#     docs/verification-plan.xml#V-MP-DOC-TECH-SPEC,
#     docs/knowledge-graph.xml#MP-DOC-TECH-SPEC
# END_MODULE_CONTRACT
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from mint_python.core.table import TableRaggedRowsError
from mint_python.mcp import document as document_module
from mint_python.mcp.document import _build_document, _load_template, _run_pipeline
from mint_python.ooxml import unpack
from mint_python.templates import registry as registry_module
from mint_python.templates.registry import (
    TemplateRegistry,
    reset_default_registry,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"


# --------------------------------------------------------------------------- #
# Fixtures — hermetic templates/ snapshot + output dir per test. Mirrors the
# test_mp_doc_bundle isolation pattern: snapshot the file under test (plus
# the canonical memo so DocumentTypeNotFound message-sanity still works in
# adjacent suites) into tmp_path/templates, monkeypatch the document module
# + registry singleton at it, and route MINT_MEMO_DIR to tmp_path/doc_out.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "doc_out"))


@pytest.fixture(autouse=True)
def _isolate_templates_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Snapshot technical-spec.yaml (and memo.yaml as baseline) into a
    tmp_path templates/ dir, then point both the document module and the
    registry singleton at it. Yields the fixture dir for tests that want
    to construct their own TemplateRegistry against the same snapshot."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in ("memo", "technical-spec"):
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
# Fixtures (content) — two shapes: full happy-path + unicode/OOXML-special.
# --------------------------------------------------------------------------- #


_TECH_SPEC_FIXTURE: dict[str, str] = dict(
    title="ETL Pipeline v2 — Reference Spec",
    purpose=(
        "Migrate the Q3 finance ETL from cron-bash to Airflow.\n"
        "Reduce mean batch latency from 4h to 90min.\n"
        "Add lineage telemetry to every transform."
    ),
    sections=(
        "1. Source contracts — invariants & schema\n"
        "2. Transform layer — pure-function constraints (no I/O)\n"
        "3. Sink contracts — idempotency & retry policy"
    ),
    requirement_1="System MUST process > 10K rows/sec at P99 < 50ms",
    requirement_2="All transforms emit OpenLineage events to Marquez",
    priority_1="P0",
    priority_2="P1",
    scope_warning="MLOps integration is OUT OF SCOPE — see ADR-042.",
    notes="Reviewed by infra & data-platform; sign-off pending.",
)


# Cyrillic + OOXML-special chars are intentional in this fixture
# (scenario-7 exercises unicode round-trip + entity escaping).
_UNICODE_OOXML_FIXTURE: dict[str, str] = dict(
    title="Спецификация & проектные рамки <v2>",
    purpose='Поддержка символов "& < > \' " в OOXML — round-trip safe.',
    sections="Раздел 1 — Архитектура\nРаздел 2 — Контракты данных",  # noqa: RUF001 — Cyrillic intentional (digit/letter mix triggers RUF001)
    requirement_1="Cyrillic mix: «кавычки» & angle <brackets>",
    requirement_2="Apostrophes & ampersands: O'Reilly & sons",
    priority_1="P0",
    priority_2="P1",
    scope_warning='Out-of-scope: legacy "v1" pipeline',
    notes="Reviewed — entities <should> remain &amp; escaped",
)


_MINIMAL_FIXTURE: dict[str, str] = dict(
    title="Minimal Tech Spec",
    purpose="One-paragraph purpose.",
    sections="One-line sections summary.",
)


# --------------------------------------------------------------------------- #
# Helpers — extract body text runs, GRACE manifest blob.
# --------------------------------------------------------------------------- #


def _read_grace_manifest(docx_path: Path) -> bytes:
    """Concatenate every grace/*.xml part inside a docx zip. Asserts that
    at least one such part exists — every produced doc should carry a
    manifest (audit Priority-4 guarantee)."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        grace_parts = [
            n for n in zf.namelist()
            if n.startswith("grace/") and n.endswith(".xml")
        ]
        assert grace_parts, f"GRACE injection didn't run for {docx_path.name}"
        return b"".join(zf.read(p) for p in grace_parts)


_W_T_RE = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")


def _docx_body_texts(docx_path: Path) -> list[str]:
    """Extract w:t text runs in document order from word/document.xml.
    Tests use this for content-presence + paragraph-ordering checks."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        body_xml = zf.read("word/document.xml").decode("utf-8")
    return _W_T_RE.findall(body_xml)


def _read_body_xml(docx_path: Path) -> str:
    """Raw word/document.xml text — for entity-escape assertions where the
    `<w:t>...</w:t>` regex would already have decoded entities away."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        return zf.read("word/document.xml").decode("utf-8")


# --------------------------------------------------------------------------- #
# Scenario-1 — full end-to-end render exercising every block kind.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_renders_with_table_and_callout() -> None:
    """create_document(doc_type='technical-spec', spec={…}) produces a docx
    that opens; body contains heading + paragraph + table + callout. Every
    block kind in the layout is exercised.

    Note on pipeline reach: the pipeline only elicits fields listed in
    template.required_fields = (title, purpose, sections). Optional
    placeholders (requirement_*, scope_warning, notes) resolve to "" via
    _substitute when the spec field stays None — they don't get filled
    by FakeMCPContext.answers because the elicit loop doesn't iterate
    them. We exercise the full block-kind reach in scenarios 3, 9, 15 by
    constructing DocumentSpec directly; scenario-1 here pins the pipeline
    happy-path including the static H2 / callout title literals."""
    ctx = FakeMCPContext(answers=dict(_TECH_SPEC_FIXTURE))
    result = await _run_pipeline(
        intent="draft a tech spec",
        doc_type="technical-spec",
        source_md=None,
        ctx=ctx,
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "technical-spec"
    assert result["template_version"] == "1.0"

    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.name.startswith("technical-spec_")

    body_xml = _read_body_xml(output_path)
    texts = _docx_body_texts(output_path)
    joined = "\n".join(texts)
    # Heading kind exercised: H1 title + 3 H2 literals.
    assert _TECH_SPEC_FIXTURE["title"] in joined
    assert "Purpose" in joined
    assert "Requirements" in joined
    assert "Sections" in joined
    # Paragraph kind exercised: purpose paragraph resolves and renders.
    # (The purpose value is multi-line; we probe a known fragment that
    # survives line-by-line w:t splitting.)
    assert "Migrate the Q3 finance ETL" in joined
    # Table kind exercised: 1 Requirements table + 2 callout tables = >=1
    # w:tbl in body XML (callouts box content in their own w:tbl).
    assert body_xml.count("<w:tbl>") >= 1, (
        "expected at least 1 w:tbl in body XML"
    )
    # Requirements header cells survive substitution and rendering.
    for cell in ("ID", "Requirement", "Priority"):
        assert cell in joined, f"header cell {cell!r} missing"
    # Callout kind exercised: both callout titles ("Scope boundary",
    # "Notes") are STATIC strings, not placeholders, so they survive
    # even when the callout bodies resolve to "" (scope_warning/notes
    # not in required_fields → not elicited → spec stays None).
    assert "Scope boundary" in joined
    assert "Notes" in joined


# --------------------------------------------------------------------------- #
# Scenario-2 — table block renders as real OOXML w:tbl + survives unpack().
# --------------------------------------------------------------------------- #


def test_scenario_2_table_round_trip(tmp_path: Path) -> None:
    """The Requirements table renders as OOXML w:tbl with the declared
    header row + body rows. mint_python.ooxml.unpack round-trips the docx
    and the unpacked word/document.xml still contains the table cells.

    Uses _build_document directly with a fully populated DocumentSpec so
    the requirement_* / priority_* cell substitutions resolve. The
    pipeline path only elicits required_fields; non-required placeholders
    resolve to "" — useful for the happy-path (scenario-1) but not for a
    round-trip that needs cell content to inspect."""
    from mint_python.mcp.document import DocumentSpec

    spec = DocumentSpec(**_TECH_SPEC_FIXTURE)
    template = _load_template("technical-spec")
    doc = _build_document(spec, template)
    output_path = tmp_path / "table_round_trip.docx"
    doc.save(output_path)

    body_xml = _read_body_xml(output_path)
    # Header cells: `ID`, `Requirement`, `Priority` all live as w:t inside
    # the table region. We don't try to parse OOXML strictly; literal
    # substring assertions suffice for round-trip evidence.
    for cell in ("ID", "Requirement", "Priority"):
        assert cell in body_xml, f"header cell {cell!r} missing in body XML"
    # Body rows — substituted requirement_* and priority_* values appear.
    assert "R-1" in body_xml
    assert "R-2" in body_xml
    # Walker passes cell text verbatim to Table.from_list; '<' / '>' /
    # '&' get entity-escaped on OOXML serialise. Probe the escaped form.
    escaped_req1 = (
        _TECH_SPEC_FIXTURE["requirement_1"]
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    assert escaped_req1 in body_xml, (
        f"requirement_1 cell missing — expected escaped form "
        f"{escaped_req1!r} in body XML"
    )

    # Round-trip via mint_python.ooxml.unpack — the unpacked
    # word/document.xml still carries the table cell content.
    unpack_dir = tmp_path / "unpacked"
    unpack(output_path, unpack_dir)
    unpacked_doc_xml = (unpack_dir / "word" / "document.xml").read_text(
        encoding="utf-8"
    )
    assert "<w:tbl>" in unpacked_doc_xml
    # The body w:tbl carries the requirement cells after round-trip.
    assert "R-1" in unpacked_doc_xml
    assert "R-2" in unpacked_doc_xml


# --------------------------------------------------------------------------- #
# Scenario-3 — callout block renders via existing kind:callout walker branch.
# --------------------------------------------------------------------------- #


def test_scenario_3_callout_kind_preserved(tmp_path: Path) -> None:
    """The two callouts in the layout (kind_of: warning, kind_of: info)
    render via the Phase-14 W2 kind:callout walker branch. Produced docx
    contains both callout bodies + their titles, and the rendered
    Callout block instances carry the declared CalloutKind values.

    Uses _build_document directly so the optional scope_warning / notes
    fields resolve in the substitution path (the pipeline elicits only
    required_fields)."""
    from mint_python.core.callout import Callout, CalloutKind
    from mint_python.mcp.document import DocumentSpec

    spec = DocumentSpec(**_TECH_SPEC_FIXTURE)
    template = _load_template("technical-spec")
    doc = _build_document(spec, template)
    output_path = tmp_path / "callouts.docx"
    doc.save(output_path)

    texts = _docx_body_texts(output_path)
    joined = "\n".join(texts)
    assert "Scope boundary" in joined  # warning callout title
    assert _TECH_SPEC_FIXTURE["scope_warning"] in joined  # warning body
    assert "Notes" in joined  # info callout title
    # `&` in notes body is entity-escaped to `&amp;` inside w:t — our
    # _W_T_RE doesn't decode entities, so we compare against the
    # escaped form.
    notes_escaped = _TECH_SPEC_FIXTURE["notes"].replace("&", "&amp;")
    assert notes_escaped in joined  # info body

    # Inspect the rendered Callout blocks: declared kind values survive
    # the walker's kind-string → CalloutKind translation.
    callouts: list[Callout] = []
    for section in doc._sections:
        for block in section._blocks:
            if isinstance(block, Callout):
                callouts.append(block)
    assert len(callouts) == 2, f"expected 2 callouts, got {len(callouts)}"
    assert callouts[0].kind is CalloutKind.WARNING, (
        f"first callout should be WARNING, got {callouts[0].kind!r}"
    )
    assert callouts[1].kind is CalloutKind.INFO, (
        f"second callout should be INFO, got {callouts[1].kind!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-4 — GRACE manifest names template='technical-spec.yaml'.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_manifest_template_name() -> None:
    """The GRACE manifest of a produced tech-spec carries
    `template=technical-spec.yaml` + `template_version=1.0`. This test
    does NOT assert preset_version (that's V-MP-AUDIT-EXTEND
    scenario-1/2 territory — V-MP-DOC-TECH-SPEC forbidden-6)."""
    ctx = FakeMCPContext(answers=dict(_TECH_SPEC_FIXTURE))
    result = await _run_pipeline(
        intent="", doc_type="technical-spec", source_md=None, ctx=ctx
    )
    assert result["status"] == "complete"

    manifest_blob = _read_grace_manifest(Path(result["path"]))
    assert b"template=technical-spec.yaml" in manifest_blob
    assert b"template_version=1.0" in manifest_blob
    assert b"generated_by=MP-DOC-GENERIC" in manifest_blob


# --------------------------------------------------------------------------- #
# Scenario-5 — required_fields elicitation order is exactly
# (title, purpose, sections). No personae fields are elicited.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_required_fields_order() -> None:
    """With an empty intent, the pipeline elicits exactly (title, purpose,
    sections) — in declaration order, and ONLY those three fields. sender
    / author / contact / signer / recipient never get elicited (V-MP-DOC-
    TECH-SPEC forbidden-5 — anonymity-friendly)."""
    answers = {name: f"value-for-{name}" for name in ("title", "purpose", "sections")}
    ctx = FakeMCPContext(answers=answers)

    result = await _run_pipeline(
        intent="generate doc",  # empty-of-signals heuristic
        doc_type="technical-spec",
        source_md=None,
        ctx=ctx,
    )
    assert result["status"] == "complete"

    elicited_order = tuple(field_name for field_name, _msg in ctx.elicited_calls)
    assert elicited_order == ("title", "purpose", "sections"), (
        f"unexpected elicit order: {elicited_order!r}"
    )

    # forbidden-5 enforcement: none of the personae fields appear in the
    # elicit transcript.
    elicited_names = {name for name, _ in ctx.elicited_calls}
    forbidden_personae = {
        "sender", "author", "contact", "signer", "signature", "recipient"
    }
    assert not (elicited_names & forbidden_personae), (
        f"forbidden personae fields elicited: "
        f"{elicited_names & forbidden_personae!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-6 — template loads cleanly via TemplateRegistry.
# --------------------------------------------------------------------------- #


def test_scenario_6_schema_validates(_isolate_templates_dir: Path) -> None:
    """Construct a fresh TemplateRegistry over the fixture templates/ dir;
    assert technical-spec appears in summaries() with its declared
    doc_type='technical-spec' + version='1.0'. No
    TemplateInvalidSchema raised at load time."""
    registry = TemplateRegistry(templates_dir=_isolate_templates_dir)
    by_name = {s.name: s for s in registry.summaries()}
    assert "technical-spec" in by_name, "technical-spec template did not load"
    assert by_name["technical-spec"].doc_type == "technical-spec"
    assert by_name["technical-spec"].version == "1.0"

    schema = registry.get("technical-spec")
    assert schema.required_fields == ("title", "purpose", "sections")
    # Every layout entry must declare one of the 5 supported `kind` values
    # (V-MP-DOC-TECH-SPEC forbidden-3 — guards against drift).
    allowed_kinds = {"heading", "spacer", "paragraph", "table", "callout"}
    for entry in schema.layout:
        assert entry.get("kind") in allowed_kinds, (
            f"unsupported layout kind in template: {entry.get('kind')!r}"
        )


# --------------------------------------------------------------------------- #
# Scenario-7 — Unicode + OOXML-special chars in cells: produced XML has
# entities properly escaped (&amp; / &lt; / &gt;) and the unicode round-
# trips through mint_python.ooxml.unpack without mojibake.
# --------------------------------------------------------------------------- #


def test_scenario_7_cell_special_chars_escaped(tmp_path: Path) -> None:
    """Cyrillic + `& < > " '` in spec values survive OOXML serialisation:
    the produced docx escapes them as `&amp;` / `&lt;` / `&gt;` literal
    entities in the XML; round-trip via mint_python.ooxml.unpack
    preserves the Cyrillic text.

    Uses _build_document directly so the cell-level placeholders
    (requirement_*) resolve — the pipeline only elicits required_fields
    and the cell values are interesting precisely because they carry the
    OOXML-special chars."""
    from mint_python.mcp.document import DocumentSpec

    spec = DocumentSpec(**_UNICODE_OOXML_FIXTURE)
    template = _load_template("technical-spec")
    doc = _build_document(spec, template)
    output_path = tmp_path / "unicode.docx"
    doc.save(output_path)

    body_xml = _read_body_xml(output_path)
    # Entity escaping — at least one occurrence of each.
    assert "&amp;" in body_xml, "ampersand not escaped to &amp; in body XML"
    assert "&lt;" in body_xml, "less-than not escaped to &lt; in body XML"
    assert "&gt;" in body_xml, "greater-than not escaped to &gt; in body XML"

    # Cyrillic literally present (UTF-8 decoded body).
    assert "Спецификация" in body_xml
    assert "Раздел" in body_xml

    # Round-trip via unpack — Cyrillic survives the zip → file → re-read
    # path; entities still XML-encoded in the unpacked document.xml.
    unpack_dir = tmp_path / "unpacked"
    unpack(output_path, unpack_dir)
    unpacked = (unpack_dir / "word" / "document.xml").read_text(
        encoding="utf-8"
    )
    assert "Спецификация" in unpacked
    assert "&amp;" in unpacked


# --------------------------------------------------------------------------- #
# Scenario-8 — multi-line resolved placeholder in cell: newlines preserved.
# --------------------------------------------------------------------------- #


def test_scenario_8_multiline_placeholder_in_cell(tmp_path: Path) -> None:
    """A placeholder that resolves to a multi-line string renders as a
    single cell whose value carries the joined string. _substitute does
    not split or wrap on newlines — its contract is "stringify and
    insert". We park a 3-line value into requirement_1 and check the
    resolved cell text contains all 3 line fragments.

    Uses _build_document directly because requirement_1 is not in
    required_fields and would otherwise stay None on the pipeline path."""
    from mint_python.mcp.document import DocumentSpec

    multiline_req = "Line 1: throughput\nLine 2: latency\nLine 3: lineage"
    spec = DocumentSpec(
        title=_MINIMAL_FIXTURE["title"],
        purpose=_MINIMAL_FIXTURE["purpose"],
        sections=_MINIMAL_FIXTURE["sections"],
        requirement_1=multiline_req,
    )
    template = _load_template("technical-spec")
    doc = _build_document(spec, template)
    out_path = tmp_path / "multiline_cell.docx"
    doc.save(out_path)
    body_xml = _read_body_xml(out_path)
    # All three line fragments must appear in the body. The cell text
    # carries the literal "\n" which OOXML serialises inside w:t (or as
    # an inline w:br depending on the emitter — we don't care; the
    # invariant under test is "newline does NOT split the cell value
    # across separate cells / rows").
    for fragment in ("Line 1: throughput", "Line 2: latency", "Line 3: lineage"):
        assert fragment in body_xml, (
            f"multiline fragment {fragment!r} missing from cell body XML"
        )


# --------------------------------------------------------------------------- #
# Scenario-9 — multi-section chaining: H1 → ... → H2 → ... produces 2
# sections, each owning its block subset.
# --------------------------------------------------------------------------- #


def test_scenario_9_block_kind_chaining_within_sections() -> None:
    """The canonical layout has one H1 (`{{ title }}`) and three H2s
    (Purpose / Requirements / Sections). Per walker:526-532 each heading
    opens a NEW Section instance; total = 4 sections. Spacers + paragraphs
    + table + callouts attach to the section that opened most recently.

    This test confirms the walker chains all 5 block kinds across multiple
    sections without losing any. We assert exactly 4 sections (tightens
    V-MP-DOC-TECH-SPEC scenario-9's >=2 floor to the actual walker shape)
    and that each block kind shows up at least once across them.

    Uses _build_document directly to inspect Document._sections — the
    pipeline doesn't return the Document object, only the saved path."""
    from mint_python.mcp.document import DocumentSpec

    spec = DocumentSpec(**_TECH_SPEC_FIXTURE)
    template = _load_template("technical-spec")
    doc = _build_document(spec, template)
    sections = doc._sections
    # 1 H1 + 3 H2 = 4 sections. Acceptance: at least 2 (scenario contract
    # in V-MP-DOC-TECH-SPEC); we tighten to exactly 4 to pin the walker.
    assert len(sections) == 4, (
        f"expected 4 sections (1 H1 + 3 H2), got {len(sections)}"
    )
    # The H1 owns no body content — it's just the title. The 3 H2s own
    # the table + callouts + paragraphs.
    section_titles = [s.title for s in sections]
    assert section_titles[0] == _TECH_SPEC_FIXTURE["title"]
    assert "Purpose" in section_titles
    assert "Requirements" in section_titles
    assert "Sections" in section_titles

    # Collect every block kind across all sections — every supported kind
    # appears at least once (paragraph, table, callout). Spacers count as
    # Paragraph instances; heading is implicit in Section.title so doesn't
    # appear in _blocks.
    from mint_python.core.callout import Callout
    from mint_python.core.content import Paragraph
    from mint_python.core.table import Table

    seen_kinds: set[str] = set()
    for section in sections:
        for block in section._blocks:
            if isinstance(block, Paragraph):
                seen_kinds.add("paragraph_or_spacer")
            elif isinstance(block, Table):
                seen_kinds.add("table")
            elif isinstance(block, Callout):
                seen_kinds.add("callout")
    assert {"paragraph_or_spacer", "table", "callout"} <= seen_kinds, (
        f"expected paragraph+table+callout blocks across sections, got "
        f"{seen_kinds!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-10 — ragged table at render: TableRaggedRowsError propagates,
# message names the offending row index.
# --------------------------------------------------------------------------- #


def test_scenario_10_ragged_table_raises() -> None:
    """Walker:547-555 builds Table.from_list([header_cells, *rows], header=
    True) with NO try/except around it; a malformed YAML where header has
    3 cols but a row has 2 cols propagates TableRaggedRowsError from
    Table.from_list. The error message names the offending row index."""
    from mint_python.mcp.document import DocumentSpec, DocumentTemplate

    ragged_template = DocumentTemplate(
        name="technical-spec",
        version="1.0",
        required_fields=("title", "purpose", "sections"),
        layout=(
            {"kind": "heading", "level": 1, "text": "{{ title }}"},
            {
                "kind": "table",
                "header": ["A", "B", "C"],
                "rows": [["1", "2"]],  # ragged — 2 cols, header is 3
            },
        ),
    )
    spec = DocumentSpec(title="T", purpose="P", sections="S")
    with pytest.raises(TableRaggedRowsError) as excinfo:
        _build_document(spec, ragged_template)
    # Error message names the offending row index. row 0 is the header
    # (3 cols); row 1 is the ragged data row (2 cols).
    assert "row 1" in str(excinfo.value), (
        f"expected error message naming 'row 1', got {excinfo.value!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-11 — empty table (`header: []`, `rows: []`) renders without
# crashing; document opens.
# --------------------------------------------------------------------------- #


def test_scenario_11_empty_table_renders(tmp_path: Path) -> None:
    """Walker:547-555 with `header: []` + `rows: []` calls
    Table.from_list([[]], header=True) -> Table with 1 row x 0 cols.
    Table.render's empty-grid branch (table.py:467-473) emits a 1x1
    placeholder docx table. Document still opens — no exception. Pins
    the engine's permissive behaviour."""
    from mint_python.mcp.document import DocumentSpec, DocumentTemplate

    empty_table_template = DocumentTemplate(
        name="technical-spec",
        version="1.0",
        required_fields=("title", "purpose", "sections"),
        layout=(
            {"kind": "heading", "level": 1, "text": "{{ title }}"},
            {"kind": "table", "header": [], "rows": []},
        ),
    )
    spec = DocumentSpec(title="T", purpose="P", sections="S")
    doc = _build_document(spec, empty_table_template)
    out_path = tmp_path / "empty_table.docx"
    doc.save(out_path)
    assert out_path.exists()
    # And the resulting docx is a valid zip with word/document.xml.
    assert zipfile.is_zipfile(out_path)
    with zipfile.ZipFile(out_path) as zf:
        names = zf.namelist()
        assert "word/document.xml" in names


# --------------------------------------------------------------------------- #
# Scenario-12 — callout default-kind fallback: missing kind → INFO;
# unrecognized kind → INFO.
# --------------------------------------------------------------------------- #


def test_scenario_12_callout_kind_fallback(tmp_path: Path) -> None:
    """Walker:565-573 callout-kind resolution:
    - missing `kind_of` AND missing `type` → 'info' default → CalloutKind.INFO
    - unrecognised value (`kind_of: critical`) → also CalloutKind.INFO
    Both branches map to INFO; this locks the behaviour so a typo in YAML
    doesn't silently change rendering."""
    from mint_python.core.callout import CalloutKind
    from mint_python.mcp.document import DocumentSpec, DocumentTemplate

    template = DocumentTemplate(
        name="technical-spec",
        version="1.0",
        required_fields=("title", "purpose", "sections"),
        layout=(
            {"kind": "heading", "level": 1, "text": "{{ title }}"},
            # Missing kind_of / type entirely.
            {"kind": "callout", "title": "Missing-kind", "body": "body1"},
            # Unrecognised kind_of value.
            {
                "kind": "callout",
                "kind_of": "critical",  # not in {info, warning, code}
                "title": "Unrecognised-kind",
                "body": "body2",
            },
        ),
    )
    spec = DocumentSpec(title="T", purpose="P", sections="S")
    doc = _build_document(spec, template)
    # Inspect every Callout block — both should carry CalloutKind.INFO.
    from mint_python.core.callout import Callout

    callouts: list[Callout] = []
    for section in doc._sections:
        for block in section._blocks:
            if isinstance(block, Callout):
                callouts.append(block)
    assert len(callouts) == 2
    assert callouts[0].kind is CalloutKind.INFO, (
        f"missing-kind callout did not default to INFO: {callouts[0].kind!r}"
    )
    assert callouts[1].kind is CalloutKind.INFO, (
        f"unrecognised-kind callout did not fall back to INFO: "
        f"{callouts[1].kind!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-13 — unknown layout kind is silently skipped.
# --------------------------------------------------------------------------- #


def test_scenario_13_unknown_kind_silently_skipped(tmp_path: Path) -> None:
    """A layout entry `{kind: image, src: x.png}` falls through the
    walker's if/elif chain (walker:528-583) and is dropped. The
    surrounding heading + paragraph still render. Documents the
    expected behaviour: catch the failure mode at template-validation
    time, NOT silently at render — but locks the current contract so a
    future regression (someone adds a strict-mode raise) shows up here.
    """
    from mint_python.mcp.document import DocumentSpec, DocumentTemplate

    template = DocumentTemplate(
        name="technical-spec",
        version="1.0",
        required_fields=("title", "purpose", "sections"),
        layout=(
            {"kind": "heading", "level": 1, "text": "{{ title }}"},
            {"kind": "image", "src": "x.png"},  # unknown — silently dropped
            {"kind": "paragraph", "text": "after the unknown block"},
        ),
    )
    spec = DocumentSpec(title="T-known", purpose="P", sections="S")
    doc = _build_document(spec, template)
    out_path = tmp_path / "unknown_kind.docx"
    doc.save(out_path)
    body_xml = _read_body_xml(out_path)
    assert "T-known" in body_xml
    assert "after the unknown block" in body_xml
    # No image part embedded — the unknown kind was dropped, not surfaced.
    # word/media/* would only be present if an Image block actually ran.
    with zipfile.ZipFile(out_path) as zf:
        media_parts = [n for n in zf.namelist() if n.startswith("word/media/")]
    assert media_parts == [], (
        f"unknown kind:image should have been dropped, but media parts "
        f"appeared: {media_parts!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-14 — structured `sections` field (Python list) stringifies to
# `str(list)` — pins the "no nested-loop support yet" contract.
# --------------------------------------------------------------------------- #


def test_scenario_14_sections_field_string_only(tmp_path: Path) -> None:
    """When DocumentSpec.sections is set to a Python list (not a str), the
    `{{ sections }}` placeholder resolves to the literal repr of the list
    via str(value). This pins Phase-17 contract: structured-field
    iteration is NOT yet supported — sections are flat string blobs.

    If _substitute ever changes to raise TypeError on non-str, the
    expectations in V-MP-DOC-TECH-SPEC scenario-14 should be revised."""
    from mint_python.mcp.document import DocumentSpec

    spec = DocumentSpec(
        title="Stringification Test",
        purpose="P",
        # DocumentSpec.sections is typed `str | None`; we deliberately
        # pass a list here to exercise the str(value) stringification
        # contract of _substitute on a structured value.
        sections=["section-a", "section-b", "section-c"],
    )
    template = _load_template("technical-spec")
    doc = _build_document(spec, template)
    out_path = tmp_path / "stringified_sections.docx"
    doc.save(out_path)
    body_xml = _read_body_xml(out_path)
    # The list stringifies to repr-form: ['section-a', 'section-b',
    # 'section-c']. Apostrophes get XML-escaped to &#39; in OOXML on
    # some serialisers, so we check the bare list-element substrings.
    assert "section-a" in body_xml
    assert "section-b" in body_xml
    assert "section-c" in body_xml
    # The list-bracket marker `[` survives — confirms str(list) and NOT
    # any unrolling into separate paragraphs.
    assert "[" in body_xml and "]" in body_xml


# --------------------------------------------------------------------------- #
# Scenario-15 — _substitute applied symmetrically across all 5 kinds.
# --------------------------------------------------------------------------- #


def test_scenario_15_substitute_uniform_across_block_kinds(
    tmp_path: Path,
) -> None:
    """The same `{{ purpose }}` placeholder used in (a) heading.text,
    (b) paragraph.text, (c) table cell, (d) callout body, (e) callout
    title — all five locations resolve to the same value in the produced
    docx. Guards against regressions where one walker branch forgets
    _substitute(c, spec)."""
    from mint_python.mcp.document import DocumentSpec, DocumentTemplate

    sentinel = "UNIQUE-PURPOSE-SENTINEL-7e21"
    template = DocumentTemplate(
        name="technical-spec",
        version="1.0",
        required_fields=("title", "purpose", "sections"),
        layout=(
            # (a) heading.text — opens a new section
            {"kind": "heading", "level": 1, "text": "{{ purpose }}"},
            # (b) paragraph.text
            {"kind": "paragraph", "text": "{{ purpose }}"},
            # (c) table cell
            {
                "kind": "table",
                "header": ["H"],
                "rows": [["{{ purpose }}"]],
            },
            # (d) callout body + (e) callout title
            {
                "kind": "callout",
                "kind_of": "info",
                "title": "{{ purpose }}",
                "body": "{{ purpose }}",
            },
        ),
    )
    spec = DocumentSpec(title="T", purpose=sentinel, sections="S")
    doc = _build_document(spec, template)
    out_path = tmp_path / "uniform_substitute.docx"
    doc.save(out_path)
    body_xml = _read_body_xml(out_path)
    # Sentinel must appear at least 5 times (once per location). We don't
    # demand an exact count — Word may emit run-splits, but the literal
    # substring is preserved in each.
    count = body_xml.count(sentinel)
    assert count >= 5, (
        f"expected >=5 sentinel occurrences (heading + paragraph + "
        f"table cell + callout title + callout body), got {count}"
    )
    # And the section's title literal IS the sentinel — confirms branch
    # (a) ran the substitution.
    assert doc._sections[0].title == sentinel
