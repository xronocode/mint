# FILE: tests/integration/test_mp_templates_write.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-TEMPLATES-WRITE verification — covers scenarios 1-4 of
#     the update_template tool (Phase-14 W3). Scenario-5 (cross-model
#     handoff) is documented procedure in docs/cross-model-handoff-
#     smoke.md, not pytest-runnable.
#   SCOPE: Integration tests — exercise the registry's write path,
#     versioned-sibling discovery, audit log append, and the GRACE
#     manifest's template_author lineage carried through create_document.
#   DEPENDS: pytest, mint_python.templates.registry,
#     mint_python.mcp.document, tests._helpers.fake_mcp_context.
# END_MODULE_CONTRACT
from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

import pytest
import yaml

from mint_python.mcp.document import _run_pipeline
from mint_python.templates.registry import (
    TemplateAuthorRequired,
    TemplateRegistry,
    TemplateVersionConflict,
    get_template,
    list_templates,
    reset_default_registry,
    update_template,
)
from tests._helpers.fake_mcp_context import FakeMCPContext

REPO_TEMPLATES = Path(__file__).parent.parent.parent / "templates"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def fixture_templates_dir(tmp_path: Path) -> Path:
    """Clone the canonical templates/ into a tmp dir so writes don't
    pollute the repo. update_template's writes target this dir."""
    fixtures = tmp_path / "templates"
    fixtures.mkdir()
    for name in ("memo.yaml", "letter.yaml"):
        (fixtures / name).write_text(
            (REPO_TEMPLATES / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return fixtures


@pytest.fixture
def server_registry_over_fixture(
    fixture_templates_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the lazy default registry at the fixture dir AND
    document.py's _TEMPLATES_DIR (so create_document also resolves
    templates from the same isolated directory) — required for
    scenario-4 (GRACE manifest carries the new template version)."""
    from mint_python.mcp import document as document_module
    from mint_python.templates import registry as reg_module

    monkeypatch.setattr(reg_module, "_TEMPLATES_DIR", fixture_templates_dir)
    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixture_templates_dir)
    reset_default_registry()
    yield fixture_templates_dir
    reset_default_registry()


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic output dir — no leakage to ~/Documents/MINT/."""
    monkeypatch.setenv("MINT_MEMO_DIR", str(tmp_path / "doc_out"))


# Concrete new-content YAML used across multiple scenarios. Differs from
# memo.yaml v1.0 by (a) version string (overridden by registry to bumped
# value) and (b) layout — adds a "Confidentiality" callout. Validates
# that the new version actually changes layout, not just metadata.
NEW_MEMO_V11_YAML = """
name: memo
version: "0.0"
description: "Standard business memo, v1.1 — adds Confidentiality callout"
doc_type: memo
required_fields:
  - sender
  - recipient
  - date
  - subject
  - body
layout:
  - kind: heading
    level: 1
    text: MEMORANDUM
  - kind: spacer
  - kind: callout
    kind_of: warning
    title: Confidentiality
    body: "Internal use only. Do not forward."
  - kind: spacer
  - kind: table
    header: ["Field", "Value"]
    rows:
      - ["From", "{{ sender }}"]
      - ["To", "{{ recipient }}"]
      - ["Date", "{{ date }}"]
      - ["Subject", "{{ subject }}"]
  - kind: spacer
  - kind: heading
    level: 2
    text: Body
  - kind: paragraph
    text: "{{ body }}"
"""


# --------------------------------------------------------------------------- #
# Scenario-1: update_template('memo', new_yaml, 'Claude') writes
# memo_v1.1.yaml; returns the bump metadata; _audit.jsonl gains one entry.
# --------------------------------------------------------------------------- #


def test_scenario_1_update_writes_versioned_sibling_and_audit_entry(
    fixture_templates_dir: Path,
) -> None:
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    outcome = registry.update("memo", NEW_MEMO_V11_YAML, author="Claude")

    assert outcome["name"] == "memo"
    assert outcome["version"] == "1.1"
    assert outcome["predecessor_version"] == "1.0"
    assert outcome["audit_id"]
    assert Path(outcome["written_to"]).exists()
    assert Path(outcome["written_to"]).name == "memo_v1.1.yaml"

    # The on-disk YAML's `version` field must match the bumped value
    # (forbidden-1: filename and content version never disagree).
    written_raw = yaml.safe_load(Path(outcome["written_to"]).read_text())
    assert written_raw["version"] == "1.1"
    assert written_raw["_authored_by"] == "Claude"

    # Audit log gains exactly one entry, JSONL-shaped.
    audit_path = fixture_templates_dir / "_audit.jsonl"
    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["audit_id"] == outcome["audit_id"]
    assert entry["name"] == "memo"
    assert entry["version"] == "1.1"
    assert entry["predecessor_version"] == "1.0"
    assert entry["author"] == "Claude"
    assert "content_sha256" in entry


# --------------------------------------------------------------------------- #
# Scenario-2: update_template without author elicits via ctx; on decline
# raises TemplateAuthorRequired naming the missing field. The MCP-tool
# wrapper handles the elicit fallback; the registry method itself rejects
# empty authors directly.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_missing_author_elicits_then_writes(
    server_registry_over_fixture: Path,
) -> None:
    """update_template MCP tool with empty author triggers ctx.elicit;
    on accept it proceeds with the elicited identity."""
    ctx = FakeMCPContext(answers={"author": "Mikhail"})
    outcome = await update_template(
        "memo", NEW_MEMO_V11_YAML, ctx=ctx, author=""
    )
    assert outcome["version"] == "1.1"
    assert ("author", "Who should be recorded as the template author?") in [
        (label, msg) for label, msg in ctx.elicited_calls
    ]
    audit_lines = (server_registry_over_fixture / "_audit.jsonl").read_text().splitlines()
    assert json.loads(audit_lines[0])["author"] == "Mikhail"


@pytest.mark.asyncio
async def test_scenario_2_b_decline_raises_author_required(
    server_registry_over_fixture: Path,
) -> None:
    """User declines the author elicit → TemplateAuthorRequired."""
    ctx = FakeMCPContext(answers={"author": "__DECLINE__"})
    with pytest.raises(TemplateAuthorRequired):
        await update_template("memo", NEW_MEMO_V11_YAML, ctx=ctx, author="")


@pytest.mark.asyncio
async def test_scenario_2_c_unsupported_elicit_raises_author_required(
    server_registry_over_fixture: Path,
) -> None:
    """Client without elicitation support gets a clean
    TEMPLATE_AUTHOR_REQUIRED error suggesting they retry inline."""
    ctx = FakeMCPContext(answers={"author": "__UNSUPPORTED__"})
    with pytest.raises(TemplateAuthorRequired, match="does not support elicitation"):
        await update_template("memo", NEW_MEMO_V11_YAML, ctx=ctx, author="")


def test_scenario_2_d_registry_method_rejects_empty_author_directly(
    fixture_templates_dir: Path,
) -> None:
    """The lower-level Registry.update method skips elicitation entirely
    and refuses an empty author — the elicit path is purely an MCP-tool
    affordance, not a registry concern."""
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    with pytest.raises(TemplateAuthorRequired):
        registry.update("memo", NEW_MEMO_V11_YAML, author="")


# --------------------------------------------------------------------------- #
# Scenario-3: list_templates surfaces both versions; get_template
# resolves 'latest' to the bumped version; older versions remain
# accessible by exact pin.
# --------------------------------------------------------------------------- #


def test_scenario_3_versions_coexist_and_latest_resolves_to_bumped(
    fixture_templates_dir: Path,
) -> None:
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    registry.update("memo", NEW_MEMO_V11_YAML, author="Claude")

    summaries = registry.summaries()
    by_pair = {(s.name, s.version) for s in summaries}
    assert ("memo", "1.0") in by_pair
    assert ("memo", "1.1") in by_pair
    assert ("letter", "1.0") in by_pair

    latest = registry.get("memo")  # default = 'latest'
    assert latest.version == "1.1"
    assert latest.author == "Claude"

    # Older version still accessible by explicit pin.
    pinned = registry.get("memo", version="1.0")
    assert pinned.version == "1.0"
    assert pinned.author == ""

    # versions() helper returns both ascending.
    assert registry.versions("memo") == ["1.0", "1.1"]


@pytest.mark.asyncio
async def test_scenario_3_b_mcp_tools_see_new_version_after_update(
    server_registry_over_fixture: Path,
) -> None:
    ctx = FakeMCPContext(answers={})
    await update_template("memo", NEW_MEMO_V11_YAML, ctx=ctx, author="Claude")

    listing = await list_templates(ctx=ctx)
    pairs = {(entry["name"], entry["version"]) for entry in listing}
    assert ("memo", "1.0") in pairs
    assert ("memo", "1.1") in pairs

    latest = await get_template("memo", ctx=ctx)
    assert latest["version"] == "1.1"

    pinned = await get_template("memo", ctx=ctx, version="1.0")
    assert pinned["version"] == "1.0"


# --------------------------------------------------------------------------- #
# Scenario-4: create_document with the new template version produces a
# docx whose GRACE manifest names {template, template_version,
# template_author} — the lineage is embedded in the audit-trail.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_grace_manifest_carries_template_author_lineage(
    server_registry_over_fixture: Path,
) -> None:
    ctx_update = FakeMCPContext(answers={})
    await update_template(
        "memo", NEW_MEMO_V11_YAML, ctx=ctx_update, author="Claude-Opus-4.7"
    )

    intent = (
        "sender: M\nrecipient: B\ndate: 2026-05-15\n"
        "subject: T\nbody: Body for scenario-4 audit-lineage test.\n"
    )
    ctx_doc = FakeMCPContext(answers={})
    result = await _run_pipeline(
        intent=intent, doc_type="memo", source_md=None, ctx=ctx_doc
    )

    assert result["status"] == "complete"
    assert result["doc_type"] == "memo"
    assert result["template_version"] == "1.1"

    output_path = Path(result["path"])
    with zipfile.ZipFile(output_path, "r") as zf:
        grace_parts = [
            n for n in zf.namelist()
            if n.startswith("grace/") and n.endswith(".xml")
        ]
        assert grace_parts
        manifest_blob = b"".join(zf.read(p) for p in grace_parts)

    assert b"template=memo.yaml" in manifest_blob
    assert b"template_version=1.1" in manifest_blob
    assert b"template_author=Claude-Opus-4.7" in manifest_blob


# --------------------------------------------------------------------------- #
# Forbidden-1: refuse to overwrite an existing version file.
# --------------------------------------------------------------------------- #


def test_forbidden_1_concurrent_write_to_same_version_raises_conflict(
    fixture_templates_dir: Path,
) -> None:
    """Two writers race: writer A's registry sees memo at 1.0 only;
    between A's bump-computation and A's write, writer B has already
    created memo_v1.1.yaml on disk. A's write must refuse to clobber.

    We simulate by building the registry first (so its in-memory state
    pins predecessor=1.0 and target=memo_v1.1.yaml), THEN dropping
    memo_v1.1.yaml from another process. Registry.update doesn't
    re-walk before computing the bump, so the stale cache + new file
    is the exact race condition forbidden-1 protects against."""
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    # Writer B sneaks in.
    (fixture_templates_dir / "memo_v1.1.yaml").write_text(
        "name: memo\nversion: \"1.1\"\nrequired_fields: []\nlayout: []\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateVersionConflict, match="TEMPLATE_VERSION_CONFLICT"):
        registry.update("memo", NEW_MEMO_V11_YAML, author="Claude")


# --------------------------------------------------------------------------- #
# Forbidden-2: audit log is append-only across multiple writes.
# --------------------------------------------------------------------------- #


def test_forbidden_2_audit_log_is_append_only(
    fixture_templates_dir: Path,
) -> None:
    """Two consecutive updates produce two distinct audit lines; neither
    overwrites the other. Versions monotonically increase."""
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    out1 = registry.update("memo", NEW_MEMO_V11_YAML, author="Claude")
    # Second update — the new latest is now 1.1; bump to 1.2.
    next_yaml = NEW_MEMO_V11_YAML.replace(
        "Internal use only. Do not forward.",
        "Internal — Phase-14 W3 verification, do not forward.",
    )
    out2 = registry.update("memo", next_yaml, author="Mikhail")

    assert out1["version"] == "1.1"
    assert out2["version"] == "1.2"
    assert out2["predecessor_version"] == "1.1"

    audit_lines = (
        (fixture_templates_dir / "_audit.jsonl").read_text().splitlines()
    )
    assert len(audit_lines) == 2
    entries = [json.loads(line) for line in audit_lines]
    assert entries[0]["version"] == "1.1"
    assert entries[1]["version"] == "1.2"
    # IDs must be distinct.
    assert entries[0]["audit_id"] != entries[1]["audit_id"]


# --------------------------------------------------------------------------- #
# Logging: BLOCK_UPDATE_TEMPLATE fires once per update with full payload.
# --------------------------------------------------------------------------- #


def test_update_template_log_marker(
    fixture_templates_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    with caplog.at_level(logging.INFO, logger="mint_python.templates.registry"):
        outcome = registry.update("memo", NEW_MEMO_V11_YAML, author="Claude")

    update_msgs = [
        r.getMessage()
        for r in caplog.records
        if "BLOCK_UPDATE_TEMPLATE" in r.getMessage()
    ]
    assert len(update_msgs) == 1
    msg = update_msgs[0]
    assert "[MP-TemplatesWrite]" in msg
    assert "name=memo" in msg
    assert "version=1.1" in msg
    assert "predecessor_version=1.0" in msg
    assert "author=Claude" in msg
    assert outcome["audit_id"] in msg


# --------------------------------------------------------------------------- #
# Edge: update_template against unknown name raises TemplateNotFound
# (no first-version-from-scratch path in W3).
# --------------------------------------------------------------------------- #


def test_update_template_unknown_name_raises(
    fixture_templates_dir: Path,
) -> None:
    from mint_python.templates.registry import TemplateNotFound

    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    with pytest.raises(TemplateNotFound):
        registry.update("nonexistent", NEW_MEMO_V11_YAML, author="Claude")


# --------------------------------------------------------------------------- #
# Edge: update_template with non-mapping content raises
# TemplateInvalidSchema (caught before any disk write).
# --------------------------------------------------------------------------- #


def test_update_template_non_mapping_content_rejected_before_write(
    fixture_templates_dir: Path,
) -> None:
    from mint_python.templates.registry import TemplateInvalidSchema

    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    with pytest.raises(TemplateInvalidSchema, match="did not parse to a mapping"):
        registry.update("memo", "- just a list\n- nothing else", author="Claude")
    # Crucially: no memo_v1.1.yaml file was created and no audit entry appended.
    assert not (fixture_templates_dir / "memo_v1.1.yaml").exists()
    assert not (fixture_templates_dir / "_audit.jsonl").exists()


# --------------------------------------------------------------------------- #
# Edge: registry rejects a YAML whose filename version disagrees with
# its 'version' field.
# --------------------------------------------------------------------------- #


def test_registry_rejects_filename_version_mismatch(
    fixture_templates_dir: Path,
) -> None:
    """memo_v2.5.yaml carrying 'version: 1.0' inside is a lie about its
    lineage — registry refuses to load."""
    from mint_python.templates.registry import TemplateInvalidSchema

    (fixture_templates_dir / "memo_v2.5.yaml").write_text(
        "name: memo\nversion: \"1.0\"\nrequired_fields: [a]\nlayout: []\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateInvalidSchema, match="does not match"):
        TemplateRegistry(templates_dir=fixture_templates_dir)


# --------------------------------------------------------------------------- #
# Edge: duplicate (name, version) across files (canonical memo.yaml at
# 1.0 + memo_v1.0.yaml at 1.0) raises at load.
# --------------------------------------------------------------------------- #


def test_get_with_unknown_version_pin_raises_with_known_versions(
    fixture_templates_dir: Path,
) -> None:
    """get(name, version='9.9') for a registered name but missing
    version raises TemplateNotFound with the available versions
    enumerated in the message."""
    from mint_python.templates.registry import TemplateNotFound

    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    registry.update("memo", NEW_MEMO_V11_YAML, author="Claude")
    with pytest.raises(TemplateNotFound, match="has no version") as exc_info:
        registry.get("memo", version="9.9")
    msg = str(exc_info.value)
    assert "1.0" in msg
    assert "1.1" in msg


def test_available_doc_types_recognizes_versioned_siblings(
    fixture_templates_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """document._available_doc_types must NOT double-count when memo.yaml
    and memo_v1.1.yaml coexist — both contribute the doc_type 'memo'."""
    from mint_python.mcp import document as document_module

    registry = TemplateRegistry(templates_dir=fixture_templates_dir)
    registry.update("memo", NEW_MEMO_V11_YAML, author="Claude")

    monkeypatch.setattr(document_module, "_TEMPLATES_DIR", fixture_templates_dir)
    types = document_module._available_doc_types()
    assert types == ["letter", "memo"]


def test_registry_rejects_duplicate_name_version(
    fixture_templates_dir: Path,
) -> None:
    from mint_python.templates.registry import TemplateInvalidSchema

    (fixture_templates_dir / "memo_v1.0.yaml").write_text(
        (REPO_TEMPLATES / "memo.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(TemplateInvalidSchema, match="duplicate"):
        TemplateRegistry(templates_dir=fixture_templates_dir)
