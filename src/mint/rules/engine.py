# FILE: src/mint/rules/engine.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Core rule evaluation engine — load YAML rules and evaluate against OOXML documents
#   SCOPE: Rule parsing, evaluation, severity classification, hint generation
#   DEPENDS: none
#   LINKS: docs/knowledge-graph.xml#M-RULES, docs/verification-plan.xml#V-M-RULES
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Rule - validation rule dataclass
#   Violation - violation result dataclass
#   Severity - hard/soft severity enum
#   FixCategory - safe/visual/destructive enum
#   evaluate - evaluate a single rule against document XML
#   classify_severity - classify violation severity and fix category
#   get_hint - return educational hint for a rule ID
#   all_rules - load all rules from YAML directory
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Updated module contract markup
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from lxml import etree

logger = logging.getLogger(__name__)

_LOG_PREFIX = "Rules"

RULES_DIR = Path(__file__).parent.parent.parent.parent / "rules"

NAMESPACES = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}


class Severity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class FixCategory(StrEnum):
    SAFE = "safe"
    VISUAL = "visual"
    DESTRUCTIVE = "destructive"


class RuleLoadError(Exception):
    """Raised when rule YAML cannot be loaded or parsed."""


# START_CONTRACT: Rule
#   PURPOSE: Validation rule definition loaded from YAML
#   INPUTS: { field values }
#   OUTPUTS: { Rule }
#   SIDE_EFFECTS: none
# END_CONTRACT: Rule
@dataclass(frozen=True)
class Rule:
    id: str
    format: str
    severity: Severity
    xpath: str
    check: str
    fix_category: FixCategory
    hint: str
    description: str = ""


# START_CONTRACT: Violation
#   PURPOSE: Detected violation from rule evaluation
#   INPUTS: { field values }
#   OUTPUTS: { Violation }
#   SIDE_EFFECTS: none
# END_CONTRACT: Violation
@dataclass(frozen=True)
class Violation:
    rule_id: str
    severity: Severity
    fix_category: FixCategory
    message: str
    hint: str
    location: str = ""


# START_CONTRACT: evaluate
#   PURPOSE: Evaluate one rule against an lxml-parsed document XML tree
#   INPUTS: { rule: Rule, tree: etree._ElementTree }
#   OUTPUTS: { Violation | None }
#   SIDE_EFFECTS: none
#   LINKS: V-M-RULES scenario-1..7
# END_CONTRACT: evaluate
def evaluate(rule: Rule, tree: etree._Element | etree._ElementTree) -> Violation | None:
    # START_BLOCK_EVALUATE_RULE
    try:
        raw_results = tree.xpath(rule.xpath, namespaces=NAMESPACES)
    except etree.XPathError as exc:
        logger.warning(
            f"[{_LOG_PREFIX}][evaluate][BLOCK_EVALUATE_RULE] "
            f"XPath error for rule {rule.id}: {rule.xpath}: {exc}"
        )
        return None

    results = raw_results if isinstance(raw_results, list) else [raw_results]

    if rule.check == "exists":
        if results:
            return Violation(
                rule_id=rule.id,
                severity=rule.severity,
                fix_category=rule.fix_category,
                message=rule.description or f"Rule {rule.id} violated",
                hint=rule.hint,
                location=str(results[0]) if results else "",
            )
    elif rule.check == "not_exists":
        if not results:
            return Violation(
                rule_id=rule.id,
                severity=rule.severity,
                fix_category=rule.fix_category,
                message=rule.description or f"Rule {rule.id} violated: expected element not found",
                hint=rule.hint,
            )
    elif rule.check == "count_gt_zero":
        if results and len(results) > 0:
            return Violation(
                rule_id=rule.id,
                severity=rule.severity,
                fix_category=rule.fix_category,
                message=rule.description or f"Rule {rule.id}: found {len(results)} matches",
                hint=rule.hint,
                location=str(results[0]) if results else "",
            )
    elif rule.check == "sum_mismatch" and results and len(results) >= 2:
        try:
            vals = [int(getattr(r, "text", "0") or "0") for r in results]
            if vals and vals[0] != sum(vals[1:]):
                return Violation(
                    rule_id=rule.id,
                    severity=rule.severity,
                    fix_category=rule.fix_category,
                    message=rule.description or f"Sum mismatch: {vals[0]} != {sum(vals[1:])}",
                    hint=rule.hint,
                    location=str(results[0]),
                )
        except (ValueError, TypeError):
            pass
    # END_BLOCK_EVALUATE_RULE
    return None


# START_CONTRACT: classify_severity
#   PURPOSE: Classify violation as hard/soft with fix category
#   INPUTS: { violation: Violation }
#   OUTPUTS: { tuple[Severity, FixCategory] }
#   SIDE_EFFECTS: none
# END_CONTRACT: classify_severity
def classify_severity(violation: Violation) -> tuple[Severity, FixCategory]:
    return (violation.severity, violation.fix_category)


# START_CONTRACT: get_hint
#   PURPOSE: Return educational reject hint dict for a rule ID
#   INPUTS: { rule_id: str, rules: list[Rule] }
#   OUTPUTS: { dict with rule, reason, fix_instruction }
#   SIDE_EFFECTS: none
# END_CONTRACT: get_hint
def get_hint(rule_id: str, rules: list[Rule] | None = None) -> dict[str, str]:
    if rules is None:
        rules = all_rules()
    for rule in rules:
        if rule.id == rule_id:
            return {
                "rule": rule.id,
                "reason": rule.description,
                "fix_instruction": rule.hint,
            }
    return {"rule": rule_id, "reason": "Unknown rule", "fix_instruction": ""}


def _load_yaml_rules(path: Path, doc_format: str) -> list[Rule]:
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data or "rules" not in data:
        raise RuleLoadError(f"Invalid rule file {path}: missing 'rules' key")
    rules: list[Rule] = []
    for entry in data["rules"]:
        rules.append(
            Rule(
                id=entry["id"],
                format=doc_format,
                severity=Severity(entry.get("severity", "hard")),
                xpath=entry["xpath"],
                check=entry.get("check", "exists"),
                fix_category=FixCategory(entry.get("fix_category", "safe")),
                hint=entry.get("hint", ""),
                description=entry.get("description", ""),
            )
        )
    return rules


# START_CONTRACT: all_rules
#   PURPOSE: Registry — load all YAML rules for a given format
#   INPUTS: { rules_dir: Path | None, format: str }
#   OUTPUTS: { list[Rule] }
#   SIDE_EFFECTS: reads filesystem
#   LINKS: V-M-RULES scenario-4..7
# END_CONTRACT: all_rules
def all_rules(
    rules_dir: Path | None = None,
    doc_format: str = "docx",
) -> list[Rule]:
    base = rules_dir or RULES_DIR
    result: list[Rule] = []
    for path in sorted(base.glob("*.yaml")):
        if doc_format == "docx" and path.name.startswith("d-"):
            result.extend(_load_yaml_rules(path, "docx"))
        elif doc_format == "pptx" and path.name.startswith("p-"):
            result.extend(_load_yaml_rules(path, "pptx"))
    return result
