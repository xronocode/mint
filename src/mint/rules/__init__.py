# FILE: src/mint/rules/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: OOXML validation rules loaded from YAML
#   SCOPE: Load, evaluate, and classify DOCX/PPTX rules
#   DEPENDS: none
#   LINKS: docs/knowledge-graph.xml#M-RULES, docs/verification-plan.xml#V-M-RULES
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Rule - validation rule dataclass
#   Violation - detected violation dataclass
#   FixCategory - enum: safe, visual, destructive
#   Severity - enum: hard, soft
#   evaluate - evaluate one rule against XML tree
#   classify_severity - classify violation severity
#   get_hint - return educational hint for a rule
#   all_rules - load all rules from YAML
# END_MODULE_MAP

from mint.rules.engine import (
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

__all__ = [
    "FixCategory",
    "Rule",
    "RuleLoadError",
    "Severity",
    "Violation",
    "all_rules",
    "classify_severity",
    "evaluate",
    "get_hint",
]
