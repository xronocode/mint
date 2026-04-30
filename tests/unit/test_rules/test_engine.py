from pathlib import Path

import pytest

from mint.rules import (
    FixCategory,
    Rule,
    Severity,
    Violation,
    all_rules,
    classify_severity,
    evaluate,
    get_hint,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"
RULES_DIR = Path(__file__).parent.parent.parent.parent / "rules"


class TestLoadRules:
    def test_load_docx_hard_rules(self) -> None:
        rules = all_rules(rules_dir=RULES_DIR, doc_format="docx")
        rule_ids = [r.id for r in rules]
        assert "D-H01" in rule_ids
        assert "D-H09" in rule_ids

    def test_load_docx_soft_rules(self) -> None:
        rules = all_rules(rules_dir=RULES_DIR, doc_format="docx")
        soft = [r for r in rules if r.severity == Severity.SOFT]
        assert len(soft) >= 5

    def test_load_pptx_rules(self) -> None:
        rules = all_rules(rules_dir=RULES_DIR, doc_format="pptx")
        rule_ids = [r.id for r in rules]
        assert "P-H01" in rule_ids

    def test_load_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        rules = all_rules(rules_dir=tmp_path / "nonexistent", doc_format="docx")
        assert rules == []


class TestEvaluateDocx:
    @pytest.fixture()
    def docx_rules(self) -> list[Rule]:
        return all_rules(rules_dir=RULES_DIR, doc_format="docx")

    def test_dh01_bad_column_widths(self, docx_rules: list[Rule]) -> None:
        import zipfile

        from lxml import etree

        docx_path = FIXTURES / "bad_column_widths.docx"
        assert docx_path.exists(), f"Fixture missing: {docx_path}"

        with zipfile.ZipFile(docx_path) as z:
            xml = z.read("word/document.xml")
        tree = etree.fromstring(xml)

        rule = next(r for r in docx_rules if r.id == "D-H01")
        violation = evaluate(rule, tree)
        assert violation is not None
        assert violation.rule_id == "D-H01"
        assert violation.severity == Severity.HARD

    def test_dh03_percentage_width(self, docx_rules: list[Rule]) -> None:
        import zipfile

        from lxml import etree

        docx_path = FIXTURES / "percentage_width.docx"
        assert docx_path.exists(), f"Fixture missing: {docx_path}"

        with zipfile.ZipFile(docx_path) as z:
            xml = z.read("word/document.xml")
        tree = etree.fromstring(xml)

        rule = next(r for r in docx_rules if r.id == "D-H03")
        violation = evaluate(rule, tree)
        assert violation is not None
        assert violation.rule_id == "D-H03"
        assert violation.fix_category == FixCategory.DESTRUCTIVE

    def test_dh09_raw_newline(self, docx_rules: list[Rule]) -> None:
        import zipfile

        from lxml import etree

        docx_path = FIXTURES / "raw_newline.docx"
        assert docx_path.exists(), f"Fixture missing: {docx_path}"

        with zipfile.ZipFile(docx_path) as z:
            xml = z.read("word/document.xml")
        tree = etree.fromstring(xml)

        rule = next(r for r in docx_rules if r.id == "D-H09")
        violation = evaluate(rule, tree)
        assert violation is not None
        assert violation.rule_id == "D-H09"

    def test_minimal_valid_docx_no_false_positives(
        self, docx_rules: list[Rule]
    ) -> None:
        import zipfile

        from lxml import etree

        docx_path = FIXTURES / "minimal_valid.docx"
        assert docx_path.exists(), f"Fixture missing: {docx_path}"

        with zipfile.ZipFile(docx_path) as z:
            xml = z.read("word/document.xml")
        tree = etree.fromstring(xml)

        hard_rules = [r for r in docx_rules if r.severity == Severity.HARD]
        for rule in hard_rules:
            violation = evaluate(rule, tree)
            assert violation is None, f"False positive: {rule.id}"


class TestEvaluatePptx:
    @pytest.fixture()
    def pptx_rules(self) -> list[Rule]:
        return all_rules(rules_dir=RULES_DIR, doc_format="pptx")

    def test_ph02_bad_font(self, pptx_rules: list[Rule]) -> None:
        import zipfile

        from lxml import etree

        pptx_path = FIXTURES / "bad_font.pptx"
        assert pptx_path.exists(), f"Fixture missing: {pptx_path}"

        with zipfile.ZipFile(pptx_path) as z:
            xml = z.read("ppt/slides/slide1.xml")
        tree = etree.fromstring(xml)

        rule = next(r for r in pptx_rules if r.id == "P-H02")
        violation = evaluate(rule, tree)
        assert violation is not None
        assert violation.rule_id == "P-H02"

    def test_minimal_valid_pptx_no_false_positives(
        self, pptx_rules: list[Rule]
    ) -> None:
        import zipfile

        from lxml import etree

        pptx_path = FIXTURES / "minimal_valid.pptx"
        assert pptx_path.exists(), f"Fixture missing: {pptx_path}"

        with zipfile.ZipFile(pptx_path) as z:
            xml = z.read("ppt/slides/slide1.xml")
        tree = etree.fromstring(xml)

        for rule in pptx_rules:
            violation = evaluate(rule, tree)
            assert violation is None, f"False positive: {rule.id}"


class TestSoftRulesClassification:
    def test_soft_rules_classify_correctly(self) -> None:
        rules = all_rules(rules_dir=RULES_DIR, doc_format="docx")
        soft = [r for r in rules if r.severity == Severity.SOFT]
        for rule in soft:
            assert rule.fix_category in (FixCategory.SAFE, FixCategory.VISUAL)


class TestGetHint:
    def test_hint_for_dh03(self) -> None:
        hint = get_hint("D-H03", all_rules(rules_dir=RULES_DIR))
        assert "rule" in hint
        assert "reason" in hint
        assert "fix_instruction" in hint
        assert hint["rule"] == "D-H03"

    def test_hint_for_unknown_rule(self) -> None:
        hint = get_hint("UNKNOWN-999")
        assert hint["reason"] == "Unknown rule"


class TestClassifySeverity:
    def test_classify_hard_destructive(self) -> None:
        v = Violation(
            rule_id="D-H03",
            severity=Severity.HARD,
            fix_category=FixCategory.DESTRUCTIVE,
            message="test",
            hint="fix it",
        )
        sev, cat = classify_severity(v)
        assert sev == Severity.HARD
        assert cat == FixCategory.DESTRUCTIVE
