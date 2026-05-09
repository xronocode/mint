# FILE: src/mint_python/validate.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Validate saved OOXML documents against YAML rules.
#   SCOPE: Format detection, XML parse from ZIP, rule eval, violation classify, pass/fail.
#   DEPENDS: MP-RULES (Rule, Violation, Severity, FixCategory, evaluate, all_rules, get_hint)
#     plus lxml, zipfile, logging.
#   LINKS: docs/verification-plan.xml#V-MP-VALIDATE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ValidationReport - report dataclass with violations, counts, pass/fail
#   SeverityMode - self-defined StrEnum decoupled from mint.config
#   InvalidDocumentError - raised when document cannot be opened/parsed
#   _detect_format - detect "docx" | "pptx" from file extension
#   _get_main_xml_path - return internal OOXML XML path for format
#   _open_document_xml - open ZIP, parse XML via lxml, return (tree, doc_format)
#   run_checks - execute all rules against document, return ValidationReport
#   validate - convenience alias for run_checks (legacy-compatible)
#   classify_violations - group violations by severity and fix category
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-9-2 initial implementation — pure Python validation engine,
#     SeverityMode decoupled from mint.config, 11 verification scenarios,
#     3 forbidden behaviors guarded.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from lxml import etree

from mint_python.rules import (
    FixCategory,
    Severity,
    Violation,
    all_rules,
    evaluate,
)

logger = logging.getLogger(__name__)

_LOG_PREFIX = "MP-Validate"


class InvalidDocumentError(Exception):
    """Raised when the document cannot be opened or parsed."""


# START_CONTRACT: SeverityMode
#   PURPOSE: Validation strictness mode, self-defined to decouple from mint.config
#   INPUTS: { string value: AUDIT | LENIENT | STRICT }
#   OUTPUTS: { SeverityMode enum member }
#   SIDE_EFFECTS: none
# END_CONTRACT: SeverityMode
class SeverityMode(StrEnum):
    AUDIT = "audit"
    LENIENT = "lenient"
    STRICT = "strict"


# START_CONTRACT: ValidationReport
#   PURPOSE: Dataclass holding validation results with convenience properties
#   INPUTS: { field values }
#   OUTPUTS: { ValidationReport }
#   SIDE_EFFECTS: none
# END_CONTRACT: ValidationReport
@dataclass(frozen=True)
class ValidationReport:
    violations: list[Violation]
    total: int
    hard_count: int
    soft_count: int
    mode: str
    passed: bool
    document_format: str = ""

    @property
    def hard_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.HARD]

    @property
    def soft_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.SOFT]

    @property
    def hard_reject(self) -> list[Violation]:
        return [
            v
            for v in self.violations
            if v.severity == Severity.HARD and v.fix_category == FixCategory.DESTRUCTIVE
        ]

    @property
    def safe_fixable(self) -> list[Violation]:
        return [v for v in self.violations if v.fix_category != FixCategory.DESTRUCTIVE]

    @property
    def visual_fixable(self) -> list[Violation]:
        return [v for v in self.violations if v.fix_category == FixCategory.VISUAL]

    @property
    def destructive(self) -> list[Violation]:
        return [v for v in self.violations if v.fix_category == FixCategory.DESTRUCTIVE]

    @property
    def soft_warn(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.SOFT]


def _detect_format(doc_path: str | Path) -> str:
    p = Path(doc_path)
    suffix = p.suffix.lower()
    if suffix == ".docx":
        return "docx"
    elif suffix == ".pptx":
        return "pptx"
    raise InvalidDocumentError(f"Unsupported document format: {suffix!r} (expected .docx or .pptx)")


def _get_main_xml_path(doc_format: str) -> str:
    if doc_format == "docx":
        return "word/document.xml"
    return "ppt/slides/slide1.xml"


def _open_document_xml(doc_path: Path) -> tuple[etree._Element, str]:
    doc_format = _detect_format(doc_path)
    xml_path = _get_main_xml_path(doc_format)
    if not doc_path.exists():
        raise InvalidDocumentError(f"Document not found: {doc_path}")
    try:
        with zipfile.ZipFile(doc_path) as z:
            xml_bytes = z.read(xml_path)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise InvalidDocumentError(
            f"Cannot read {xml_path} from {doc_path}: {exc}"
        ) from exc
    try:
        tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise InvalidDocumentError(
            f"XML parse error in {xml_path}: {exc}"
        ) from exc
    return (tree, doc_format)


def _determine_passed(violations: list[Violation], mode: SeverityMode) -> bool:
    if mode == SeverityMode.AUDIT:
        return True
    elif mode == SeverityMode.LENIENT:
        return all(v.severity != Severity.HARD for v in violations)
    elif mode == SeverityMode.STRICT:
        return len(violations) == 0
    return True


def _default_rules_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "rules"


# START_CONTRACT: run_checks
#   PURPOSE: Execute all rules against document, return report
#   INPUTS: { doc_path: Path, severity_mode: SeverityMode, rules_dir: Path | None }
#   OUTPUTS: { ValidationReport }
#   SIDE_EFFECTS: reads filesystem, emits INFO log
#   LINKS: V-MP-VALIDATE scenario-1..11
# END_CONTRACT: run_checks
def run_checks(
    doc_path: str | Path,
    severity_mode: SeverityMode = SeverityMode.AUDIT,
    rules_dir: Path | None = None,
) -> ValidationReport:
    # START_BLOCK_RUN_CHECKS
    path = Path(doc_path)
    try:
        tree, doc_format = _open_document_xml(path)
    except InvalidDocumentError as exc:
        report = ValidationReport(
            violations=[
                Violation(
                    rule_id="XML-001",
                    severity=Severity.HARD,
                    fix_category=FixCategory.DESTRUCTIVE,
                    message=str(exc),
                    hint="Document XML is malformed. LLM likely generated invalid OOXML structure.",
                )
            ],
            total=1,
            hard_count=1,
            soft_count=0,
            mode=str(severity_mode),
            passed=False,
            document_format="",
        )
        logger.info(
            f"[{_LOG_PREFIX}][run_checks][BLOCK_RUN_CHECKS] "
            f"mode={severity_mode}, violations=1, hard=1, soft=0, passed=False"
        )
        return report

    rules = all_rules(rules_dir=str(rules_dir) if rules_dir else None, doc_format=doc_format)

    violations: list[Violation] = []
    for rule in rules:
        violation = evaluate(rule, tree)
        if violation is not None:
            violations.append(violation)

    hard_count = sum(1 for v in violations if v.severity == Severity.HARD)
    soft_count = sum(1 for v in violations if v.severity == Severity.SOFT)

    passed = _determine_passed(violations, severity_mode)

    logger.info(
        f"[{_LOG_PREFIX}][run_checks][BLOCK_RUN_CHECKS] "
        f"mode={severity_mode}, violations={len(violations)}, "
        f"hard={hard_count}, soft={soft_count}, passed={passed}"
    )
    # END_BLOCK_RUN_CHECKS

    return ValidationReport(
        violations=violations,
        total=len(violations),
        hard_count=hard_count,
        soft_count=soft_count,
        mode=str(severity_mode),
        passed=passed,
        document_format=doc_format,
    )


# START_CONTRACT: validate
#   PURPOSE: Convenience alias for run_checks — legacy-compatible entry point
#   INPUTS: { doc_path: Path, severity_mode: SeverityMode, rules_dir: Path | None }
#   OUTPUTS: { ValidationReport }
#   SIDE_EFFECTS: delegates to run_checks
# END_CONTRACT: validate
def validate(
    doc_path: str | Path,
    severity_mode: SeverityMode = SeverityMode.AUDIT,
    rules_dir: Path | None = None,
) -> ValidationReport:
    path = Path(doc_path)
    return run_checks(path, severity_mode, rules_dir)


# START_CONTRACT: classify_violations
#   PURPOSE: Group violations by severity and auto-fix category
#   INPUTS: { violations: list[Violation] }
#   OUTPUTS: { dict with hard_reject, safe_fixable, visual_fixable, destructive, soft_warn }
#   SIDE_EFFECTS: none
# END_CONTRACT: classify_violations
def classify_violations(violations: list[Violation]) -> dict[str, list[Violation]]:
    hard_reject: list[Violation] = []
    safe_fixable: list[Violation] = []
    visual_fixable: list[Violation] = []
    destructive: list[Violation] = []
    warnings: list[Violation] = []

    for v in violations:
        if v.severity == Severity.HARD:
            if v.fix_category == FixCategory.DESTRUCTIVE:
                destructive.append(v)
            elif v.fix_category == FixCategory.SAFE:
                safe_fixable.append(v)
            elif v.fix_category == FixCategory.VISUAL:
                visual_fixable.append(v)
            else:
                hard_reject.append(v)
        else:
            warnings.append(v)

    return {
        "hard_reject": hard_reject,
        "safe_fixable": safe_fixable,
        "visual_fixable": visual_fixable,
        "destructive": destructive,
        "soft_warn": warnings,
    }
