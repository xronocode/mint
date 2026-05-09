# FILE: tests/unit/test_mp_rules.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Verify V-MP-RULES — pure Python OOXML rule engine (YAML loading, XPath evaluation,
#     check types, error handling, trace markers).
#   SCOPE: 14 verification scenarios + 5 forbidden-behavior guards per Wave-9-1 packet.
#   DEPENDS: pytest, lxml, mint_python.rules, tests/unit/conftest fixtures (mp_clean_env,
#     caplog_at_info, marker_counter, tmp_path).
#   LINKS: docs/verification-plan.xml#V-MP-RULES, docs/development-plan.xml#Wave-9-1
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TestExports - verify all 8 required exports present
#   TestYamlLoading - scenario-1 (DOCX), scenario-2 (PPTX)
#   TestExistsCheck - scenario-3
#   TestCountGtZero - scenario-4
#   TestTblWidthMismatch - scenario-5 (including edge cases)
#   TestSumMismatch - scenario-6
#   TestXPathError - scenario-7 and forbid-1
#   TestPptxEvaluate - scenario-8
#   TestClassifyAndHint - scenario-9
#   TestTraceMarkers - scenario-10
#   TestErrorPaths - scenarios 11-13 and forbid-3, forbid-5
#   TestScalarXPathResult - scenario-14 and forbid-4
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-9-1 initial implementation — 14 scenarios + 5 forbidden guards
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from lxml import etree

from mint_python.rules import (
    FixCategory,
    Rule,
    RuleLoadError,
    Severity,
    Violation,
    all_rules,
    classify_severity,
    evaluate,
    get_hint,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _docx_tree(xml_string: str) -> etree._Element:
    wrapped = f'<w:document xmlns:w="{W_NS}" xmlns:a="{A_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{xml_string}</w:document>'
    return etree.fromstring(wrapped)


def _pptx_tree(xml_string: str) -> etree._Element:
    wrapped = f'<p:presentation xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{xml_string}</p:presentation>'
    return etree.fromstring(wrapped)


def _make_tmp_yaml(tmp_path: Path, filename: str, content: dict) -> Path:
    p = tmp_path / filename
    p.write_text(yaml.dump(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Scenario helpers: rule + tree factories
# ---------------------------------------------------------------------------


def _rule_dh03() -> Rule:
    return Rule(
        id="D-H03",
        format="docx",
        severity=Severity.HARD,
        xpath="//w:tblW[@w:w and @w:type='pct']",
        check="exists",
        fix_category=FixCategory.DESTRUCTIVE,
        hint="Use fixed twips (w:type='dxa') instead of percentage widths.",
        description="Percentage-based table width may render inconsistently",
    )


def _rule_dh01() -> Rule:
    return Rule(
        id="D-H01",
        format="docx",
        severity=Severity.HARD,
        xpath="//w:tbl",
        check="tbl_width_mismatch",
        fix_category=FixCategory.VISUAL,
        hint="Reconcile sum(gridCol/@w:w) with tblW/@w:w.",
        description="Table column widths mismatch table width",
    )


def _rule_ph03() -> Rule:
    return Rule(
        id="P-H03",
        format="pptx",
        severity=Severity.HARD,
        xpath="//p:sp[p:spPr/a:xfrm/a:off and p:spPr/a:xfrm/a:ext]",
        check="count_gt_zero",
        fix_category=FixCategory.VISUAL,
        hint="Ensure shapes do not overlap slide boundaries.",
        description="Shape potentially outside slide boundaries",
    )


def _rule_ph02() -> Rule:
    return Rule(
        id="P-H02",
        format="pptx",
        severity=Severity.HARD,
        xpath=(
            "//a:rPr/a:latin[contains(@typeface, 'Arial') "
            "or contains(@typeface, 'Times') "
            "or contains(@typeface, 'Courier')]"
        ),
        check="exists",
        fix_category=FixCategory.DESTRUCTIVE,
        hint="Embed fonts or use theme fonts for cross-platform compatibility.",
        description="Non-embedded system font",
    )


def _rule_sum() -> Rule:
    return Rule(
        id="SUM-01",
        format="docx",
        severity=Severity.HARD,
        xpath="//w:t",
        check="sum_mismatch",
        fix_category=FixCategory.VISUAL,
        hint="",
        description="Sum mismatch test rule",
    )


# ===================================================================
# Scenario 14 + forbid-4: Scalar XPath result wrapping
# ===================================================================


class TestScalarXPathResult:
    """Scenario-14: XPath that returns a non-list (scalar / bool / float) must be
    wrapped in a list before check-type dispatch.  forbid-4: SCALAR-RESULT-PASSTHROUGH.
    """

    def test_scalar_bool_result_wrapped_to_list(self):
        """XPath returning True (boolean) → wrapped as [True] → exists check fires."""
        rule = Rule(
            id="SCALAR-01",
            format="docx",
            severity=Severity.HARD,
            xpath="boolean(//w:document)",
            check="exists",
            fix_category=FixCategory.SAFE,
            hint="",
        )
        tree = _docx_tree("")
        result = evaluate(rule, tree)
        assert result is not None
        assert result.rule_id == "SCALAR-01"

    def test_scalar_number_result_wrapped_to_list(self):
        """XPath returning a number → wrapped → empty list if 0? No, still non-empty."""
        rule = Rule(
            id="SCALAR-02",
            format="docx",
            severity=Severity.HARD,
            xpath="count(//w:tbl)",
            check="count_gt_zero",
            fix_category=FixCategory.SAFE,
            hint="",
        )
        tree = _docx_tree("<w:tbl></w:tbl>")
        result = evaluate(rule, tree)
        assert result is not None
        assert result.rule_id == "SCALAR-02"


# ===================================================================
# Scenario 1-2: YAML loading DOCX / PPTX
# ===================================================================


class TestYamlLoading:
    """Scenario-1 (DOCX: d-hard + d-soft), Scenario-2 (PPTX: p-hard only)."""

    def test_all_rules_docx_loads_real_files(self):
        rules = all_rules(doc_format="docx")
        assert len(rules) > 0
        ids = {r.id for r in rules}
        assert "D-H01" in ids
        assert "D-S01" in ids
        assert all(r.format == "docx" for r in rules)

    def test_all_rules_pptx_loads_real_files(self):
        rules = all_rules(doc_format="pptx")
        assert len(rules) > 0
        ids = {r.id for r in rules}
        assert "P-H01" in ids
        assert "P-H03" in ids
        # D-H* must be excluded for pptx
        assert "D-H01" not in ids
        assert all(r.format == "pptx" for r in rules)

    def test_all_rules_docx_from_explicit_dir(self, tmp_path: Path):
        content = {
            "rules": [
                {
                    "id": "D-CUSTOM",
                    "severity": "hard",
                    "xpath": "//w:tbl",
                    "check": "exists",
                    "fix_category": "safe",
                    "hint": "test",
                    "description": "custom",
                }
            ]
        }
        _make_tmp_yaml(tmp_path, "d-custom.yaml", content)
        rules = all_rules(rules_dir=str(tmp_path), doc_format="docx")
        assert len(rules) == 1
        assert rules[0].id == "D-CUSTOM"


# ===================================================================
# Scenario 3: exists check (positive + negative)
# ===================================================================


class TestExistsCheck:
    """Scenario-3: D-H03 on percentage-width docx → Violation; clean docx → None."""

    def test_exists_positive(self):
        rule = _rule_dh03()
        tree = _docx_tree('<w:tblW w:w="5000" w:type="pct"/>')
        result = evaluate(rule, tree)
        assert result is not None
        assert result.rule_id == "D-H03"
        assert result.severity == Severity.HARD
        assert result.fix_category == FixCategory.DESTRUCTIVE

    def test_exists_negative(self):
        rule = _rule_dh03()
        tree = _docx_tree('<w:tblW w:w="5000" w:type="dxa"/>')
        result = evaluate(rule, tree)
        assert result is None

    def test_exists_empty_document(self):
        rule = _rule_dh03()
        tree = _docx_tree("")
        result = evaluate(rule, tree)
        assert result is None


# ===================================================================
# Scenario 4: count_gt_zero check
# ===================================================================


class TestCountGtZero:
    """Scenario-4: P-H03 on slide with shapes → Violation; zero matches → None."""

    def test_count_gt_zero_positive(self):
        rule = _rule_ph03()
        tree = _pptx_tree(
            '<p:sp><p:spPr><a:xfrm>'
            '<a:off x="100" y="100"/><a:ext cx="200" cy="200"/>'
            '</a:xfrm></p:spPr></p:sp>'
        )
        result = evaluate(rule, tree)
        assert result is not None
        assert result.rule_id == "P-H03"

    def test_count_gt_zero_negative(self):
        rule = _rule_ph03()
        tree = _pptx_tree(
            '<p:sp><p:spPr><a:xfrm></a:xfrm></p:spPr></p:sp>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_count_gt_zero_empty_tree(self):
        rule = _rule_ph03()
        tree = _pptx_tree("")
        result = evaluate(rule, tree)
        assert result is None


# ===================================================================
# Scenario 5: tbl_width_mismatch check
# ===================================================================


class TestTblWidthMismatch:
    """Scenario-5: D-H01 on bad_column_widths → Violation; minimal_valid → None.
    Edge cases: missing tblGrid → None; w:type='auto' → None.
    """

    def test_tbl_width_mismatch_positive(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr><w:tblW w:w="5000" w:type="dxa"/></w:tblPr>'
            '<w:tblGrid>'
            '<w:gridCol w:w="2000"/>'
            '<w:gridCol w:w="2000"/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is not None
        assert result.rule_id == "D-H01"

    def test_tbl_width_mismatch_negative_match(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr><w:tblW w:w="4000" w:type="dxa"/></w:tblPr>'
            '<w:tblGrid>'
            '<w:gridCol w:w="2000"/>'
            '<w:gridCol w:w="2000"/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_no_tbl_grid(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr><w:tblW w:w="5000" w:type="dxa"/></w:tblPr>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_auto_type(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr><w:tblW w:w="5000" w:type="auto"/></w:tblPr>'
            '<w:tblGrid>'
            '<w:gridCol w:w="2000"/>'
            '<w:gridCol w:w="2000"/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_negative_widths_filtered(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr><w:tblW w:w="2000" w:type="dxa"/></w:tblPr>'
            '<w:tblGrid>'
            '<w:gridCol w:w="2000"/>'
            '<w:gridCol w:w="-500"/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_empty_gridcols(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr><w:tblW w:w="5000" w:type="dxa"/></w:tblPr>'
            '<w:tblGrid>'
            '<w:gridCol/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_no_tblpr(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblGrid>'
            '<w:gridCol w:w="2000"/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_no_tblw(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr></w:tblPr>'
            '<w:tblGrid>'
            '<w:gridCol w:w="2000"/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_non_digit_declared(self):
        rule = _rule_dh01()
        tree = _docx_tree(
            '<w:tbl>'
            '<w:tblPr><w:tblW w:w="abc" w:type="dxa"/></w:tblPr>'
            '<w:tblGrid>'
            '<w:gridCol w:w="2000"/>'
            '</w:tblGrid>'
            '</w:tbl>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_tbl_width_mismatch_on_pptx_is_noop(self):
        rule = _rule_dh01()
        tree = _pptx_tree("")
        result = evaluate(rule, tree)
        assert result is None


# ===================================================================
# Scenario 6: sum_mismatch check
# ===================================================================


class TestSumMismatch:
    """Scenario-6: mismatched → Violation; matching → None; non-numeric → None."""

    def test_sum_mismatch_positive(self):
        rule = _rule_sum()
        tree = _docx_tree(
            '<w:r><w:t>100</w:t></w:r>'
            '<w:r><w:t>30</w:t></w:r>'
            '<w:r><w:t>40</w:t></w:r>'
        )
        result = evaluate(rule, tree)
        assert result is not None
        assert result.rule_id == "SUM-01"

    def test_sum_mismatch_negative_match(self):
        rule = _rule_sum()
        tree = _docx_tree(
            '<w:r><w:t>70</w:t></w:r>'
            '<w:r><w:t>30</w:t></w:r>'
            '<w:r><w:t>40</w:t></w:r>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_sum_mismatch_non_numeric(self):
        rule = _rule_sum()
        tree = _docx_tree(
            '<w:r><w:t>abc</w:t></w:r>'
            '<w:r><w:t>30</w:t></w:r>'
        )
        result = evaluate(rule, tree)
        assert result is None

    def test_sum_mismatch_insufficient_nodes(self):
        rule = _rule_sum()
        tree = _docx_tree('<w:r><w:t>100</w:t></w:r>')
        result = evaluate(rule, tree)
        assert result is None


# ===================================================================
# Scenario 7 + forbid-1: XPath error handling
# ===================================================================


class TestXPathError:
    """Scenario-7: any etree.XPathError → logs WARNING + returns None.
    forbid-1: SILENT-XPATH-ERROR — must catch and log.
    """

    def test_xpath_syntax_error_returns_none_and_logs_warning(self, caplog_at_info):
        rule = Rule(
            id="BAD-XPATH",
            format="docx",
            severity=Severity.HARD,
            xpath="///[[[invalid",
            check="exists",
            fix_category=FixCategory.SAFE,
            hint="",
        )
        tree = _docx_tree("")
        result = evaluate(rule, tree)
        assert result is None
        warnings = [r for r in caplog_at_info.records if r.levelno == logging.WARNING]
        assert len(warnings) >= 1
        assert "XPath error" in warnings[0].getMessage()
        assert "BAD-XPATH" in warnings[0].getMessage()

    def test_xpath_namespace_error(self, caplog_at_info):
        rule = Rule(
            id="BAD-NS",
            format="docx",
            severity=Severity.HARD,
            xpath="//unknown:foo",
            check="exists",
            fix_category=FixCategory.SAFE,
            hint="",
        )
        tree = _docx_tree("")
        result = evaluate(rule, tree)
        assert result is None
        warnings = [r for r in caplog_at_info.records if r.levelno == logging.WARNING]
        assert len(warnings) >= 1


# ===================================================================
# Scenario 8: PPTX evaluate
# ===================================================================


class TestPptxEvaluate:
    """Scenario-8: P-H02 on bad_font → Violation; minimal_valid → None."""

    def test_ph02_positive(self):
        rule = _rule_ph02()
        tree = _pptx_tree(
            '<p:sp>'
            '<p:txBody>'
            '<a:bodyPr/>'
            '<a:p>'
            '<a:r>'
            '<a:rPr>'
            '<a:latin typeface="Arial"/>'
            '</a:rPr>'
            '<a:t>Hello</a:t>'
            '</a:r>'
            '</a:p>'
            '</p:txBody>'
            '</p:sp>'
        )
        result = evaluate(rule, tree)
        assert result is not None
        assert result.rule_id == "P-H02"

    def test_ph02_negative(self):
        rule = _rule_ph02()
        tree = _pptx_tree(
            '<p:sp>'
            '<p:txBody>'
            '<a:bodyPr/>'
            '<a:p>'
            '<a:r>'
            '<a:rPr>'
            '<a:latin typeface="Calibri"/>'
            '</a:rPr>'
            '<a:t>Hello</a:t>'
            '</a:r>'
            '</a:p>'
            '</p:txBody>'
            '</p:sp>'
        )
        result = evaluate(rule, tree)
        assert result is None


# ===================================================================
# Scenario 9: classify_severity + get_hint
# ===================================================================


class TestClassifyAndHint:
    """Scenario-9: classify_severity (HARD, DESTRUCTIVE), (SOFT, VISUAL).
    get_hint('D-H01') → dict; get_hint('UNKNOWN-999') → {'reason': 'Unknown rule'}.
    """

    def test_classify_severity_hard_destructive(self):
        v = Violation(
            rule_id="D-H01",
            severity=Severity.HARD,
            fix_category=FixCategory.DESTRUCTIVE,
            message="test",
            hint="hint",
        )
        sev, cat = classify_severity(v)
        assert sev == Severity.HARD
        assert cat == FixCategory.DESTRUCTIVE

    def test_classify_severity_soft_visual(self):
        v = Violation(
            rule_id="D-S01",
            severity=Severity.SOFT,
            fix_category=FixCategory.VISUAL,
            message="test",
            hint="hint",
        )
        sev, cat = classify_severity(v)
        assert sev == Severity.SOFT
        assert cat == FixCategory.VISUAL

    def test_get_hint_known_rule(self):
        hint = get_hint("D-H01")
        assert "reason" in hint
        assert hint["reason"] != "Unknown rule"

    def test_get_hint_unknown_rule(self):
        hint = get_hint("UNKNOWN-999")
        assert hint == {"reason": "Unknown rule"}

    def test_get_hint_with_explicit_rules_list(self):
        rules = [
            Rule(
                id="CUSTOM", format="docx", severity=Severity.HARD,
                xpath="//w:t", check="exists", fix_category=FixCategory.SAFE,
                hint="do this", description="custom desc",
            )
        ]
        hint = get_hint("CUSTOM", rules=rules)
        assert hint["rule"] == "CUSTOM"
        assert hint["reason"] == "custom desc"
        assert hint["fix_instruction"] == "do this"


# ===================================================================
# Scenario 10: Trace markers
# ===================================================================


class TestTraceMarkers:
    """Scenario-10: BLOCK_LOAD_RULES at INFO; BLOCK_EVALUATE_RULE at INFO per evaluate;
    XPath error at WARNING.
    """

    def test_load_rules_trace_marker(self, caplog_at_info, tmp_path: Path):
        content = {
            "rules": [
                {
                    "id": "D-TRACE",
                    "severity": "hard",
                    "xpath": "//w:tbl",
                    "check": "exists",
                    "fix_category": "safe",
                    "hint": "test",
                    "description": "trace test",
                }
            ]
        }
        _make_tmp_yaml(tmp_path, "d-trace.yaml", content)
        all_rules(rules_dir=str(tmp_path), doc_format="docx")
        records = [r for r in caplog_at_info.records if r.levelno == logging.INFO]
        markers = [r.getMessage() for r in records]
        assert any("BLOCK_LOAD_RULES" in m and "doc_format=docx" in m for m in markers)

    def test_evaluate_trace_marker(self, caplog_at_info):
        rule = _rule_dh03()
        tree = _docx_tree("")
        evaluate(rule, tree)
        records = [r for r in caplog_at_info.records if r.levelno == logging.INFO]
        markers = [r.getMessage() for r in records]
        assert any(
            "BLOCK_EVALUATE_RULE" in m and "rule_id=D-H03" in m and "check=exists" in m
            for m in markers
        )

    def test_xpath_error_trace_marker(self, caplog_at_info):
        rule = Rule(
            id="ERR-TRACE",
            format="docx",
            severity=Severity.HARD,
            xpath="///",
            check="exists",
            fix_category=FixCategory.SAFE,
            hint="",
        )
        evaluate(rule, _docx_tree(""))
        warnings = [r for r in caplog_at_info.records if r.levelno == logging.WARNING]
        assert any("BLOCK_EVALUATE_RULE" in r.getMessage() for r in warnings)

    def test_marker_counter_fixture(self, marker_counter, caplog_at_info, tmp_path: Path):
        content = {
            "rules": [
                {
                    "id": "D-MARK",
                    "severity": "hard",
                    "xpath": "//w:tbl",
                    "check": "exists",
                    "fix_category": "safe",
                    "hint": "test",
                    "description": "marker test",
                }
            ]
        }
        _make_tmp_yaml(tmp_path, "d-mark.yaml", content)
        all_rules(rules_dir=str(tmp_path), doc_format="docx")
        evaluate(
            Rule(
                id="EVAL-MARK", format="docx", severity=Severity.HARD,
                xpath="//w:tbl", check="exists", fix_category=FixCategory.SAFE,
                hint="",
            ),
            _docx_tree(""),
        )
        counter = marker_counter(caplog_at_info)
        assert counter.get("BLOCK_LOAD_RULES", 0) >= 1
        assert counter.get("BLOCK_EVALUATE_RULE", 0) >= 1


# ===================================================================
# Scenarios 11-13 + forbid-3, forbid-5: Error paths
# ===================================================================


class TestErrorPaths:
    """Scenario-11: broken YAML → RuleLoadError.
    Scenario-12: missing 'rules' key → RuleLoadError.
    Scenario-13: empty rules dir → [].
    forbid-3: NO-LOG-ON-FAILURE — broken YAML must raise RuleLoadError, not swallow.
    forbid-5: UNPROTECTED-ALL_RULES — yaml.YAMLError must convert to RuleLoadError.
    """

    def test_broken_yaml_raises_rule_load_error(self, tmp_path: Path):
        p = tmp_path / "d-broken.yaml"
        p.write_text(":::[broken: yaml: [", encoding="utf-8")
        with pytest.raises(RuleLoadError, match="Failed to load rule file"):
            all_rules(rules_dir=str(tmp_path), doc_format="docx")

    def test_missing_rules_key_raises_rule_load_error(self, tmp_path: Path):
        p = tmp_path / "d-norules.yaml"
        p.write_text("other_key: [1, 2, 3]\n", encoding="utf-8")
        with pytest.raises(RuleLoadError, match="missing 'rules' key"):
            all_rules(rules_dir=str(tmp_path), doc_format="docx")

    def test_empty_data_raises_rule_load_error(self, tmp_path: Path):
        p = tmp_path / "d-empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(RuleLoadError):
            all_rules(rules_dir=str(tmp_path), doc_format="docx")

    def test_null_data_raises_rule_load_error(self, tmp_path: Path):
        p = tmp_path / "d-null.yaml"
        p.write_text("null\n", encoding="utf-8")
        with pytest.raises(RuleLoadError):
            all_rules(rules_dir=str(tmp_path), doc_format="docx")

    def test_empty_rules_dir_returns_empty_list(self, tmp_path: Path):
        rules = all_rules(rules_dir=str(tmp_path), doc_format="docx")
        assert rules == []

    def test_non_matching_yaml_in_dir_returns_empty(self, tmp_path: Path):
        content = {
            "rules": [
                {
                    "id": "X-CUSTOM",
                    "severity": "hard",
                    "xpath": "//x:foo",
                    "check": "exists",
                    "fix_category": "safe",
                    "hint": "test",
                    "description": "custom",
                }
            ]
        }
        _make_tmp_yaml(tmp_path, "x-other.yaml", content)
        rules = all_rules(rules_dir=str(tmp_path), doc_format="docx")
        assert rules == []

    def test_pptx_only_loads_p_prefix(self, tmp_path: Path):
        content = {
            "rules": [
                {
                    "id": "P-CUSTOM",
                    "severity": "hard",
                    "xpath": "//p:sp",
                    "check": "exists",
                    "fix_category": "safe",
                    "hint": "test",
                    "description": "custom",
                }
            ]
        }
        _make_tmp_yaml(tmp_path, "p-custom.yaml", content)
        rules = all_rules(rules_dir=str(tmp_path), doc_format="pptx")
        assert len(rules) == 1
        assert rules[0].id == "P-CUSTOM"

    def test_missing_file_does_not_raise(self, tmp_path: Path):
        _make_tmp_yaml(tmp_path, "d-valid.yaml", {"rules": []})
        rules = all_rules(rules_dir=str(tmp_path), doc_format="docx")
        assert rules == []

    def test_load_yaml_rules_nonexistent_path_returns_empty(self):
        from mint_python.rules import _load_yaml_rules

        result = _load_yaml_rules(Path("/nonexistent/path/rules.yaml"), "docx")
        assert result == []


# ===================================================================
# Exports verification
# ===================================================================


class TestExports:
    """Verify all 8 required exports present on the module."""

    def test_all_exports_present(self):
        import mint_python.rules as mod
        names = dir(mod)
        required = [
            "Rule", "Violation", "Severity", "FixCategory",
            "evaluate", "all_rules", "classify_severity", "get_hint",
        ]
        for name in required:
            assert name in names, f"Missing export: {name}"

    def test_rule_load_error_is_exception(self):
        assert issubclass(RuleLoadError, Exception)

    def test_severity_enum_values(self):
        assert Severity.HARD == "hard"
        assert Severity.SOFT == "soft"

    def test_fix_category_enum_values(self):
        assert FixCategory.SAFE == "safe"
        assert FixCategory.VISUAL == "visual"
        assert FixCategory.DESTRUCTIVE == "destructive"


# ===================================================================
# Default rules_dir resolution
# ===================================================================


class TestDefaultRulesDir:
    """Verify default rules_dir resolution when rules_dir is None."""

    def test_all_rules_default_dir_resolves_real_rules(self):
        rules = all_rules(doc_format="docx")
        assert len(rules) > 0

    def test_all_rules_with_none_uses_default(self):
        rules = all_rules(rules_dir=None, doc_format="docx")
        assert len(rules) > 0
