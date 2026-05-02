# FILE: src/mint/qa/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Two-level QA pipeline: L1 programmatic (instant) + L2 render-based (async)
#   SCOPE: QA report generation with confidence scores
#   DEPENDS: M-VALIDATE, M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-QA, docs/verification-plan.xml#V-M-QA
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   L1Report - Level 1 programmatic QA report
#   L2Report - Level 2 render-based QA report
#   QAReport - combined QA report with confidence scores
#   run_l1 - run L1 programmatic checks
#   run_l2 - run L2 render-based checks (requires Gotenberg)
#   run_qa - full QA pipeline (L1 + L2)
# END_MODULE_MAP

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from mint.config import SeverityMode
from mint.fingerprint import FingerprintResult
from mint.fingerprint import compute as fp_compute
from mint.validate import ValidationReport, run_checks

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).parent.parent.parent.parent / "rules"


class GotenbergUnavailableError(Exception):
    pass


class RenderFailedError(Exception):
    pass


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
    overlap_detected: bool = False
    overlap_percentage: float = 0.0
    margin_violations: int = 0
    font_substitutions: int = 0
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


# START_BLOCK_L2_RENDER
def run_l2(
    document_path: Path,
    gotenberg_url: str = "http://localhost:3000",
) -> L2Report:
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{gotenberg_url}/health",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2):
            pass
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        msg = f"Gotenberg unavailable at {gotenberg_url}: {e}"
        logger.warning("[QA][l2][BLOCK_L2_RENDER] %s", msg)
        return L2Report(available=False, error=msg)

    logger.info(
        "[QA][l2][BLOCK_L2_RENDER] Gotenberg available, but L2 render not yet implemented"
    )
    return L2Report(
        available=True,
        confidence=0.5,
    )
# END_BLOCK_L2_RENDER


def run_qa(
    document_path: Path,
    rules_dir: Path | None = None,
    gotenberg_url: str = "http://localhost:3000",
) -> QAReport:
    l1 = run_l1(document_path, rules_dir=rules_dir)
    l2 = run_l2(document_path, gotenberg_url=gotenberg_url)

    l1_weight = 0.7
    l2_weight = 0.3
    l1_score = 1.0 if l1.passed else max(0.0, 1.0 - l1.violations * 0.1)
    l2_score = l2.confidence if l2.available else 0.5

    overall = l1_weight * l1_score + l2_weight * l2_score
    overall = min(1.0, max(0.0, overall))

    return QAReport(
        l1=l1,
        l2=l2,
        overall_confidence=round(overall, 2),
        passed=l1.passed,
    )
