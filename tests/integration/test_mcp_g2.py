import json
from dataclasses import asdict
from pathlib import Path

from mint.edit import edit, edit_plan_from_dict
from mint.mcp_g2 import mint_create, mint_edit, mint_extract_style, mint_list_templates

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _build_replace_text_plan(target_text: str, new_text: str) -> dict:
    return {
        "format": "docx",
        "ops": [
            {
                "type": "replace_text",
                "op_id": "op-1",
                "anchor": {"type": "text", "value": target_text},
                "new_text": new_text,
            }
        ],
        "metadata": {"model": "manual"},
    }


class TestMintCreate:
    def test_create_returns_result(self) -> None:
        code = (FIXTURES / "hello_world_docx.js").read_text()
        result = json.loads(
            mint_create(
                format="docx",
                prompt="hello world",
                tier="frontier",
                model_response_override=code,
            )
        )
        assert result["success"] is True
        assert result["execution_mode"] == "code"
        assert result["output_path"] is not None

    def test_create_with_invalid_tier(self) -> None:
        result = json.loads(
            mint_create(
                format="docx",
                prompt="test",
                tier="invalid",
            )
        )
        assert result["success"] is False


class TestMintExtractStyle:
    def test_extract_returns_tokens(self) -> None:
        result = json.loads(mint_extract_style(str(FIXTURES / "minimal_valid.docx")))
        assert "colors" in result
        assert result["format"] == "docx"

    def test_extract_pptx(self) -> None:
        result = json.loads(mint_extract_style(str(FIXTURES / "minimal_valid.pptx")))
        assert result["format"] == "pptx"


class TestMintListTemplates:
    def test_list_returns_array(self) -> None:
        result = json.loads(mint_list_templates())
        assert isinstance(result, list)
        assert len(result) >= 1
        names = [t["name"] for t in result]
        assert "business-memo" in names

    def test_list_entries_have_required_fields(self) -> None:
        result = json.loads(mint_list_templates())
        for t in result:
            assert "name" in t
            assert "format" in t
            assert "source" in t


class TestMintEdit:
    """V-M-MCP-G2 scenarios 4-8 — mint_edit MCP tool."""

    def _find_target_text(self, fixture: Path) -> str:
        """Pull a paragraph text from the fixture so the anchor resolves."""
        import zipfile

        from lxml import etree

        with zipfile.ZipFile(fixture) as zf:
            doc_xml = zf.read("word/document.xml")
        tree = etree.fromstring(doc_xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for p in tree.iter(f"{{{ns['w']}}}p"):
            text = "".join(t.text or "" for t in p.iter(f"{{{ns['w']}}}t"))
            if text.strip():
                return text
        raise AssertionError("No text in fixture")

    def test_scenario_4_happy_path(self, tmp_path: Path) -> None:
        src = FIXTURES / "minimal_valid.docx"
        work = tmp_path / "doc.docx"
        work.write_bytes(src.read_bytes())
        target = self._find_target_text(work)

        plan = _build_replace_text_plan(target, target + " (edited)")
        result = json.loads(
            mint_edit(
                document_path=str(work),
                edit_plan_json=json.dumps(plan),
                author="Tester",
            )
        )
        assert result["success"] is True
        assert result["ops_total"] == 1
        assert result["ops_succeeded"] == 1
        assert result["output_path"] is not None
        assert Path(result["output_path"]).exists()
        assert result["backup_path"] is not None
        assert Path(result["backup_path"]).exists()

    def test_scenario_5_malformed_json_returns_error(self) -> None:
        result = json.loads(
            mint_edit(
                document_path="/tmp/whatever.docx",
                edit_plan_json="{not valid json",
            )
        )
        assert result["success"] is False
        assert "MCP_TOOL_ERROR" in result["error"]
        assert result["ops_total"] == 0

    def test_scenario_6_pptx_rejected_before_backup(self, tmp_path: Path) -> None:
        plan = {
            "format": "pptx",
            "ops": [
                {
                    "type": "replace_text",
                    "op_id": "op-1",
                    "anchor": {"type": "text", "value": "x"},
                    "new_text": "y",
                }
            ],
            "metadata": {"model": "manual"},
        }
        src = FIXTURES / "minimal_valid.pptx"
        work = tmp_path / "doc.pptx"
        work.write_bytes(src.read_bytes())
        result = json.loads(
            mint_edit(
                document_path=str(work),
                edit_plan_json=json.dumps(plan),
            )
        )
        assert result["success"] is False
        assert "EDIT_OP_UNSUPPORTED" in result["error"]
        # Backup must NOT have been created (oos-2: rejected before backup).
        bak = work.with_suffix(work.suffix + ".bak")
        assert not bak.exists()

    def test_scenario_7_full_trace_sequence(
        self, tmp_path: Path, caplog
    ) -> None:
        import logging

        src = FIXTURES / "minimal_valid.docx"
        work = tmp_path / "doc.docx"
        work.write_bytes(src.read_bytes())
        target = self._find_target_text(work)
        plan = _build_replace_text_plan(target, target + " (edited)")

        with (
            caplog.at_level(logging.INFO, logger="mint.edit"),
            caplog.at_level(logging.INFO, logger="mint.ooxml"),
            caplog.at_level(logging.INFO, logger="mint.validate"),
        ):
            json.loads(
                mint_edit(
                    document_path=str(work),
                    edit_plan_json=json.dumps(plan),
                )
            )

        markers_seen = [
            m
            for m in [
                "BLOCK_EDIT_PLAN_VALIDATE",
                "BLOCK_EDIT_BACKUP",
                "BLOCK_OOXML_UNPACK",
                "BLOCK_EDIT_EXTRACT_TEXT",
                "BLOCK_EDIT_RESOLVE_ANCHOR",
                "BLOCK_EDIT_APPLY_OP",
                "BLOCK_OOXML_PACK",
                "BLOCK_RUN_CHECKS",
            ]
            if any(m in r.getMessage() for r in caplog.records)
        ]
        assert markers_seen == [
            "BLOCK_EDIT_PLAN_VALIDATE",
            "BLOCK_EDIT_BACKUP",
            "BLOCK_OOXML_UNPACK",
            "BLOCK_EDIT_EXTRACT_TEXT",
            "BLOCK_EDIT_RESOLVE_ANCHOR",
            "BLOCK_EDIT_APPLY_OP",
            "BLOCK_OOXML_PACK",
            "BLOCK_RUN_CHECKS",
        ]

    def test_scenario_8_byte_identical_to_direct_edit(
        self, tmp_path: Path
    ) -> None:
        """MCP-layer adds no semantic drift over direct M-EDIT.edit()."""
        # Direct path
        src = FIXTURES / "minimal_valid.docx"
        direct_doc = tmp_path / "direct.docx"
        direct_doc.write_bytes(src.read_bytes())
        target = self._find_target_text(direct_doc)
        plan_dict = _build_replace_text_plan(target, target + " (edited)")
        plan_obj = edit_plan_from_dict(plan_dict)
        direct_result = edit(direct_doc, plan_obj, author="Tester")

        # MCP path on a separate copy
        mcp_doc = tmp_path / "mcp.docx"
        mcp_doc.write_bytes(src.read_bytes())
        mcp_result = json.loads(
            mint_edit(
                document_path=str(mcp_doc),
                edit_plan_json=json.dumps(plan_dict),
                author="Tester",
            )
        )

        # Mirror the json shape produced by mcp_g2.mint_edit so we can compare
        # the controller-relevant fields.
        direct_mirror = {
            "success": direct_result.success,
            "ops_total": direct_result.ops_total,
            "ops_succeeded": direct_result.ops_succeeded,
            "ops_failed": direct_result.ops_failed,
            "diff": [asdict(o) for o in direct_result.diff],
        }
        mcp_mirror = {
            "success": mcp_result["success"],
            "ops_total": mcp_result["ops_total"],
            "ops_succeeded": mcp_result["ops_succeeded"],
            "ops_failed": mcp_result["ops_failed"],
            "diff": mcp_result["diff"],
        }
        assert direct_mirror == mcp_mirror
