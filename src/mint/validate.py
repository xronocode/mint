# FILE: src/mint/validate.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Run all applicable rules against an OOXML document with severity mode enforcement
#   SCOPE: Load document, run rules, classify violations, produce report
#   DEPENDS: M-CONFIG, M-RULES
#   LINKS: docs/knowledge-graph.xml#M-VALIDATE, docs/verification-plan.xml#V-M-VALIDATE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ValidationReport - report dataclass with violations grouped by severity
#   InvalidDocumentError - raised when document cannot be opened/parsed
#   run_checks - execute all rules against document
#   classify_violations - group violations by severity and fix category
#   validate - main validation entry point
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from mint.config import MintConfig, SeverityMode
from mint.rules import (
    FixCategory,
    Severity,
    Violation,
    all_rules,
    evaluate,
)

logger = logging.getLogger(__name__)

_LOG_PREFIX = "Validate"


class InvalidDocumentError(Exception):
    """Raised when the document cannot be opened or parsed."""


# START_CONTRACT: ValidationReport
#   PURPOSE: Dataclass holding validation results
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

    @property
    def hard_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.HARD]

    @property
    def soft_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.SOFT]


def _open_document_xml(document_path: Path, internal_path: str) -> etree._Element:
    if not document_path.exists():
        raise InvalidDocumentError(f"Document not found: {document_path}")
    try:
        with zipfile.ZipFile(document_path) as z:
            xml_bytes = z.read(internal_path)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise InvalidDocumentError(
            f"Cannot read {internal_path} from {document_path}: {exc}"
        ) from exc
    try:
        return etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise InvalidDocumentError(
            f"XML parse error in {internal_path}: {exc}"
        ) from exc


def _detect_format(document_path: Path) -> str:
    suffix = document_path.suffix.lower()
    if suffix == ".docx":
        return "docx"
    elif suffix == ".pptx":
        return "pptx"
    raise InvalidDocumentError(f"Unsupported format: {suffix}")


def _get_main_xml_path(doc_format: str) -> str:
    if doc_format == "docx":
        return "word/document.xml"
    return "ppt/slides/slide1.xml"


# START_CONTRACT: run_checks
#   PURPOSE: Execute all rules against document, return report
#   INPUTS: { document_path: Path, severity_mode: SeverityMode, rules_dir: Path | None }
#   OUTPUTS: { ValidationReport }
#   SIDE_EFFECTS: reads filesystem
#   LINKS: V-M-VALIDATE scenario-1..7
# END_CONTRACT: run_checks
def run_checks(
    document_path: Path,
    severity_mode: SeverityMode = SeverityMode.AUDIT,
    rules_dir: Path | None = None,
) -> ValidationReport:
    # START_BLOCK_RUN_CHECKS
    doc_format = _detect_format(document_path)
    xml_path = _get_main_xml_path(doc_format)

    try:
        tree = _open_document_xml(document_path, xml_path)
    except InvalidDocumentError as exc:
        logger.warning(
            f"[{_LOG_PREFIX}][run_checks][BLOCK_RUN_CHECKS] "
            f"Cannot parse document: {exc}"
        )
        return ValidationReport(
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
            mode=severity_mode.value,
            passed=False,
        )

    rules = all_rules(rules_dir=rules_dir, doc_format=doc_format)

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
        f"mode={severity_mode.value}, violations={len(violations)}, "
        f"hard={hard_count}, soft={soft_count}, passed={passed}"
    )
    # END_BLOCK_RUN_CHECKS

    return ValidationReport(
        violations=violations,
        total=len(violations),
        hard_count=hard_count,
        soft_count=soft_count,
        mode=severity_mode.value,
        passed=passed,
    )


def _determine_passed(violations: list[Violation], mode: SeverityMode) -> bool:
    if mode == SeverityMode.AUDIT:
        return True
    elif mode == SeverityMode.LENIENT:
        return all(v.severity != Severity.HARD for v in violations)
    elif mode == SeverityMode.STRICT:
        return len(violations) == 0
    return True


# START_CONTRACT: classify_violations
#   PURPOSE: Group violations by severity and auto-fix category
#   INPUTS: { violations: list[Violation] }
#   OUTPUTS: { dict with hard_reject, soft_fixable, soft_warn, destructive }
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


# START_CONTRACT: validate
#   PURPOSE: Main validation entry point accepting document path + config
#   INPUTS: { document_path: str | Path, config: MintConfig | None }
#   OUTPUTS: { ValidationReport }
#   SIDE_EFFECTS: reads filesystem
#   LINKS: docs/knowledge-graph.xml#M-VALIDATE
# END_CONTRACT: validate
def validate(
    document_path: str | Path,
    config: MintConfig | None = None,
) -> ValidationReport:
    path = Path(document_path)
    mode = config.severity_mode if config else SeverityMode.AUDIT
    rules_dir = config.rules_dir if config else None
    return run_checks(path, mode, rules_dir)
