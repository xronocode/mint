# FILE: src/mint_python/rules/__init__.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Load YAML OOXML validation rules (D-H*, D-S*, P-H*) and evaluate via lxml XPath.
#   SCOPE: YAML parsing, Rule/Violation dataclasses, Severity/FixCategory enums,
#     evaluate via XPath, all_rules registry.
#   DEPENDS: pyyaml (yaml.safe_load), lxml (etree), logging (stdlib), signal (stdlib) — NO MP-* production deps.
#   LINKS: docs/knowledge-graph.xml#MP-RULES, docs/verification-plan.xml#V-MP-RULES
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Rule - validation rule dataclass
#   Violation - violation result dataclass
#   Severity - HARD/SOFT enum
#   FixCategory - SAFE/VISUAL/DESTRUCTIVE enum
#   RuleLoadError - raised on broken/missing YAML
#   XPathTimeoutError - raised when XPath evaluation exceeds 5 sec timeout
#   evaluate - evaluate a single rule against an lxml-parsed document XML tree
#   all_rules - load all YAML rules for a given format
#   classify_severity - classify violation severity and fix category
#   get_hint - return educational hint dict for a rule ID
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: Post-review fix — XPath timeout guard (signal.alarm 5s) + sum_mismatch
#     error logging (no more silent swallow on int()/sum() parse errors).
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Any

import yaml
from lxml import etree
from lxml.etree import _Element

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-Rules"

NAMESPACES: dict[str, str] = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
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


class XPathTimeoutError(Exception):
    """Raised when XPath evaluation exceeds the timeout."""


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
#   INPUTS: { rule: Rule, tree: _Element }
#   OUTPUTS: { Violation | None }
#   SIDE_EFFECTS: none
#   LINKS: V-MP-RULES scenario-1..7
# END_CONTRACT: evaluate
def evaluate(rule: Rule, tree: _Element) -> Violation | None:
    # START_BLOCK_EVALUATE_RULE
    logger.info(
        f"[{_LOG_PREFIX}][evaluate][BLOCK_EVALUATE_RULE] "
        f"rule_id={rule.id} check={rule.check}"
    )
    try:
        def _handler(signum: int, frame: FrameType | None) -> None:  # pragma: no cover — signal-based, untestable in CI
            raise XPathTimeoutError("XPath evaluation timed out")

        old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(5)
        try:
            raw_results = tree.xpath(rule.xpath, namespaces=NAMESPACES)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    except XPathTimeoutError:  # pragma: no cover — requires exponential XPath fixture
        logger.warning(
            f"[{_LOG_PREFIX}][evaluate][BLOCK_EVALUATE_RULE] "
            f"rule_id={rule.id} xpath timed out"
        )
        return None
    except etree.XPathError as exc:
        logger.warning(
            f"[{_LOG_PREFIX}][evaluate][BLOCK_EVALUATE_RULE] "
            f"XPath error for rule {rule.id}: {rule.xpath}: {exc}"
        )
        return None

    results: list[Any] = list(raw_results) if isinstance(raw_results, list) else [raw_results]

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
        except (ValueError, TypeError) as exc:
            logger.warning(
                f"[{_LOG_PREFIX}][evaluate][BLOCK_EVALUATE_RULE] "
                f"rule_id={rule.id} sum_mismatch parse error: {exc}"
            )
    elif rule.check == "tbl_width_mismatch":
        w_ns = NAMESPACES["w"]
        for tbl in tree.iter(f"{{{w_ns}}}tbl"):
            grid = tbl.find(f"{{{w_ns}}}tblGrid")
            if grid is None:
                continue
            grid_widths: list[int] = []
            for gc in grid.findall(f"{{{w_ns}}}gridCol"):
                w_attr = gc.get(f"{{{w_ns}}}w")
                if w_attr and w_attr.lstrip("-").isdigit():
                    val = int(w_attr)
                    if val >= 0:
                        grid_widths.append(val)
            if not grid_widths:
                continue
            tbl_pr = tbl.find(f"{{{w_ns}}}tblPr")
            if tbl_pr is None:
                continue
            tbl_w = tbl_pr.find(f"{{{w_ns}}}tblW")
            if tbl_w is None:
                continue
            if tbl_w.get(f"{{{w_ns}}}type", "dxa") != "dxa":
                continue
            declared = tbl_w.get(f"{{{w_ns}}}w", "0")
            if not declared.lstrip("-").isdigit():
                continue
            if int(declared) == sum(grid_widths):
                continue
            return Violation(
                rule_id=rule.id,
                severity=rule.severity,
                fix_category=rule.fix_category,
                message=(
                    rule.description
                    or f"Table widths mismatch: tblW={declared} != "
                    f"sum(gridCol)={sum(grid_widths)}"
                ),
                hint=rule.hint,
                location=str(tbl),
            )
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
#   INPUTS: { rule_id: str, rules: list[Rule] | None }
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
    return {"reason": "Unknown rule"}


def _load_yaml_rules(path: Path, doc_format: str) -> list[Rule]:
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"Failed to load rule file {path}: {exc}") from exc
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


def _default_rules_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "rules"


# START_CONTRACT: all_rules
#   PURPOSE: Registry — load all YAML rules for a given format
#   INPUTS: { rules_dir: str | None, doc_format: str }
#   OUTPUTS: { list[Rule] }
#   SIDE_EFFECTS: reads filesystem
#   LINKS: V-MP-RULES scenario-4..7
# END_CONTRACT: all_rules
def all_rules(
    rules_dir: str | None = None,
    doc_format: str = "docx",
) -> list[Rule]:
    # START_BLOCK_LOAD_RULES
    base = Path(rules_dir) if rules_dir is not None else _default_rules_dir()
    result: list[Rule] = []
    for path in sorted(base.glob("*.yaml")):
        if doc_format == "docx" and path.name.startswith("d-"):
            result.extend(_load_yaml_rules(path, "docx"))
        elif doc_format == "pptx" and path.name.startswith("p-"):
            result.extend(_load_yaml_rules(path, "pptx"))
    logger.info(
        f"[{_LOG_PREFIX}][all_rules][BLOCK_LOAD_RULES] "
        f"doc_format={doc_format} rule_count={len(result)}"
    )
    # END_BLOCK_LOAD_RULES
    return result
