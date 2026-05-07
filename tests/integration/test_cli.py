"""Integration tests for src/mint/cli.py (V-M-CLI).

Covers V-M-CLI scenarios 1-7. Each scenario runs the CLI through subprocess so
the parser, dispatcher, and JSON-stdout contract are exercised end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mint.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def _find_target_text(fixture: Path) -> str:
    import zipfile

    from lxml import etree

    with zipfile.ZipFile(fixture) as zf:
        doc_xml = zf.read("word/document.xml")
    tree = etree.fromstring(doc_xml)
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    for p in tree.iter(f"{{{ns}}}p"):
        text = "".join(t.text or "" for t in p.iter(f"{{{ns}}}t"))
        if text.strip():
            return text
    raise AssertionError("No text in fixture")


class TestCLI:
    def test_scenario_1_validate_emits_json(self) -> None:
        cp = _run(["validate", str(FIXTURES / "minimal_valid.docx")])
        # validate may return non-zero if any rule trips on the fixture; what we
        # care about is that stdout is well-formed JSON with the documented shape.
        report = json.loads(cp.stdout)
        assert "passed" in report
        assert "violations" in report
        assert "mode" in report

    def test_scenario_2_fingerprint_emits_json(self) -> None:
        cp = _run(["fingerprint", str(FIXTURES / "minimal_valid.docx")])
        assert cp.returncode == 0, cp.stderr
        report = json.loads(cp.stdout)
        assert "hash" in report
        assert "format" in report

    def test_scenario_4_extract_emits_json(self) -> None:
        cp = _run(["extract", str(FIXTURES / "minimal_valid.docx")])
        assert cp.returncode == 0, cp.stderr
        report = json.loads(cp.stdout)
        assert "format" in report

    def test_scenario_5_unknown_subcommand_exits_nonzero(self) -> None:
        cp = _run(["nonexistent-subcommand"])
        assert cp.returncode != 0
        # argparse prints usage to stderr.
        assert "usage" in cp.stderr.lower() or "invalid choice" in cp.stderr.lower()

    def test_scenario_6_validate_missing_file_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        bogus = tmp_path / "does_not_exist.docx"
        cp = _run(["validate", str(bogus)])
        assert cp.returncode != 0

    def test_scenario_7_edit_subcommand_happy_path(self, tmp_path: Path) -> None:
        # Copy a fixture into tmp so we don't pollute the repo
        src = FIXTURES / "minimal_valid.docx"
        work = tmp_path / "doc.docx"
        work.write_bytes(src.read_bytes())
        target = _find_target_text(work)

        plan = {
            "format": "docx",
            "ops": [
                {
                    "type": "replace_text",
                    "op_id": "op-1",
                    "anchor": {"type": "text", "value": target},
                    "new_text": target + " (edited)",
                }
            ],
            "metadata": {"model": "manual"},
        }
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan))

        cp = _run(["edit", str(work), "--plan", str(plan_path), "--author", "CLI-Test"])
        assert cp.returncode == 0, cp.stderr
        report = json.loads(cp.stdout)
        assert report["success"] is True
        assert report["ops_total"] == 1
        assert report["ops_succeeded"] == 1
        assert Path(report["output_path"]).exists()
        assert Path(report["backup_path"]).exists()

    def test_scenario_7b_edit_missing_plan_file_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        src = FIXTURES / "minimal_valid.docx"
        work = tmp_path / "doc.docx"
        work.write_bytes(src.read_bytes())
        bogus_plan = tmp_path / "missing.json"

        cp = _run(["edit", str(work), "--plan", str(bogus_plan)])
        assert cp.returncode != 0
        assert "plan file not found" in cp.stderr.lower()

    def test_scenario_7c_edit_invalid_plan_json_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        src = FIXTURES / "minimal_valid.docx"
        work = tmp_path / "doc.docx"
        work.write_bytes(src.read_bytes())
        plan_path = tmp_path / "plan.json"
        plan_path.write_text("{not valid json")

        cp = _run(["edit", str(work), "--plan", str(plan_path)])
        assert cp.returncode != 0
        assert "invalid plan json" in cp.stderr.lower()
