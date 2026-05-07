# FILE: src/mint/qa/__init__.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Two-level QA pipeline: L1 programmatic (instant) + L2 structural (async)
#   SCOPE: QA report generation with confidence scores
#   DEPENDS: M-VALIDATE, M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-QA, docs/verification-plan.xml#V-M-QA
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   L1Report - Level 1 programmatic QA report
#   L2Report - Level 2 structural QA report
#   QAReport - combined QA report with confidence scores
#   run_l1 - run L1 programmatic checks
#   run_l2 - run L2 structural checks
#   run_qa - full QA pipeline (L1 + L2)
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from mint.config import SeverityMode
from mint.fingerprint import FingerprintResult
from mint.fingerprint import compute as fp_compute
from mint.validate import ValidationReport, run_checks

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).parent.parent.parent.parent / "rules"


@dataclass
class L1Report:
    passed: bool
    violations: int = 0
    fingerprint_hash: str = ""
    xml_well_formed: bool = True
    duration_ms: int = 0


@dataclass
class L2Report:
    available: bool = False
    confidence: float = 0.0
    has_styles: bool = False
    has_headers_footers: bool = False
    has_numbering: bool = False
    section_count: int = 0
    error: str | None = None


@dataclass
class QAReport:
    l1: L1Report
    l2: L2Report
    overall_confidence: float = 0.0
    passed: bool = False


# START_BLOCK_L1_CHECK
def run_l1(
    document_path: Path,
    rules_dir: Path | None = None,
) -> L1Report:
    start = time.monotonic()
    if rules_dir is None:
        rules_dir = RULES_DIR

    try:
        validation: ValidationReport = run_checks(
            document_path, SeverityMode.LENIENT, rules_dir=rules_dir
        )
    except Exception:
        logger.exception("[QA][l1][BLOCK_L1_CHECK] Validation failed")
        return L1Report(
            passed=False,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    fingerprint_hash = ""
    try:
        fp_result: FingerprintResult = fp_compute(document_path)
        fingerprint_hash = fp_result.hash
    except Exception:
        pass

    report = L1Report(
        passed=validation.passed,
        violations=len(validation.violations),
        fingerprint_hash=fingerprint_hash,
        xml_well_formed=True,
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    logger.info(
        "[QA][l1][BLOCK_L1_CHECK] L1 done: passed=%s, violations=%d, duration=%dms",
        report.passed,
        report.violations,
        report.duration_ms,
    )
    return report
# END_BLOCK_L1_CHECK


# START_BLOCK_L2_STRUCTURAL
def run_l2(
    document_path: Path,
) -> L2Report:
    start = time.monotonic()

    try:
        with zipfile.ZipFile(document_path, "r") as zf:
            names = zf.namelist()

            has_styles = "word/styles.xml" in names
            has_numbering = "word/numbering.xml" in names

            has_headers_footers = any(
                n.startswith("word/header") or n.startswith("word/footer")
                for n in names
            )

            section_count = 0
            if "word/document.xml" in names:
                import xml.etree.ElementTree as ET

                doc_xml = zf.read("word/document.xml")
                root = ET.fromstring(doc_xml)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                section_count = len(root.findall(".//w:sectPr", ns))

            confidence = 0.5
            if has_styles:
                confidence += 0.15
            if has_headers_footers:
                confidence += 0.15
            if has_numbering:
                confidence += 0.1
            if section_count > 0:
                confidence += 0.1

            report = L2Report(
                available=True,
                confidence=min(1.0, confidence),
                has_styles=has_styles,
                has_headers_footers=has_headers_footers,
                has_numbering=has_numbering,
                section_count=section_count,
            )
            logger.info(
                "[QA][l2][BLOCK_L2_STRUCTURAL] L2 done: confidence=%.2f, "
                "styles=%s, headers_footers=%s, numbering=%s, sections=%d, duration=%dms",
                report.confidence,
                report.has_styles,
                report.has_headers_footers,
                report.has_numbering,
                report.section_count,
                int((time.monotonic() - start) * 1000),
            )
            return report

    except Exception as e:
        msg = f"L2 structural analysis failed: {e}"
        logger.warning("[QA][l2][BLOCK_L2_STRUCTURAL] %s", msg)
        return L2Report(available=False, error=msg)
# END_BLOCK_L2_STRUCTURAL


def run_qa(
    document_path: Path,
    rules_dir: Path | None = None,
) -> QAReport:
    l1 = run_l1(document_path, rules_dir=rules_dir)
    l2 = run_l2(document_path)

    l1_weight = 0.6
    l2_weight = 0.4
    l1_score = 1.0 if l1.passed else max(0.0, 1.0 - l1.violations * 0.1)
    l2_score = l2.confidence if l2.available else 0.0

    overall = l1_weight * l1_score + l2_weight * l2_score
    overall = min(1.0, max(0.0, overall))

    return QAReport(
        l1=l1,
        l2=l2,
        overall_confidence=round(overall, 2),
        passed=l1.passed,
    )
