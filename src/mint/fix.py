# FILE: src/mint/fix.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Apply safe/visual auto-fixes to OOXML docs with backup; reject destructive
#   SCOPE: Backup, iterate fixes, re-validate, detect cascade, produce diff report
#   DEPENDS: M-VALIDATE, M-RULES, M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-FIX, docs/verification-plan.xml#V-M-FIX
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FixReport - result dataclass with applied fixes, backup path, iterations, diff
#   FixError - base fix exception
#   CascadeDetectedError - raised when fixes don't converge
#   BackupFailedError - raised when backup creation fails
#   DestructiveRejectedError - raised when destructive fix attempted
#   DEFAULT_MAX_ITERATIONS - default max fix iterations (3)
#   apply_fixes - iterate fixes with cascade detection
#   create_backup - copy document before modification
#   fix - main fix entry point
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import io
import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from mint.config import SeverityMode
from mint.rules import FixCategory, Violation
from mint.validate import run_checks

logger = logging.getLogger(__name__)

_LOG_PREFIX = "Fix"
DEFAULT_MAX_ITERATIONS = 3


class FixError(Exception):
    """Base fix error."""


class CascadeDetectedError(FixError):
    """Fix loop not converging after max iterations."""


class BackupFailedError(FixError):
    """Backup creation failed."""


class DestructiveRejectedError(FixError):
    """Destructive fix rejected."""


@dataclass(frozen=True)
class FixReport:
    fixed_path: Path | None = None
    backup_path: Path | None = None
    iterations: int = 0
    applied_fixes: list[str] = field(default_factory=list)
    remaining_violations: list[Violation] = field(default_factory=list)
    diff: str = ""


# START_CONTRACT: create_backup
#   PURPOSE: Copy document to .bak before any modification
#   INPUTS: { document_path: Path }
#   OUTPUTS: { Path - backup file path }
#   SIDE_EFFECTS: creates backup file
# END_CONTRACT: create_backup
def create_backup(document_path: Path) -> Path:
    # START_BLOCK_CREATE_BACKUP
    backup_path = Path(str(document_path) + ".bak")
    try:
        shutil.copy2(document_path, backup_path)
        logger.info(
            f"[{_LOG_PREFIX}][backup][BLOCK_CREATE_BACKUP] "
            f"Created backup: {backup_path}"
        )
    except OSError as exc:
        raise BackupFailedError(f"Failed to create backup: {exc}") from exc
    # END_BLOCK_CREATE_BACKUP
    return backup_path


def _compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _apply_simple_fix(document_path: Path, violation: Violation) -> bool:
    if violation.fix_category not in (FixCategory.SAFE, FixCategory.VISUAL):
        return False

    try:
        with zipfile.ZipFile(document_path) as z:
            entries = {n: z.read(n) for n in z.namelist()}

        modified = False
        for name, data in entries.items():
            if not name.endswith(".xml"):
                continue
            xml_str = data.decode("utf-8")

            if violation.rule_id == "D-H09":
                fixed = xml_str.replace(">\n", "> ").replace(">\r\n", "> ")
                if fixed != xml_str:
                    entries[name] = fixed.encode("utf-8")
                    modified = True

        if modified:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zo:
                for name, data in entries.items():
                    zo.writestr(name, data)
            document_path.write_bytes(buf.getvalue())
            return True
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        logger.warning("[Fix][_apply_simple_fix] Failed: %s", exc)
        return False

    return False


# START_CONTRACT: apply_fixes
#   PURPOSE: Iterate safe+visual fixes, re-validate, detect cascade
#   INPUTS: { document_path, violations, max_iterations, rules_dir }
#   OUTPUTS: { FixReport }
#   SIDE_EFFECTS: modifies document file
#   LINKS: V-M-FIX scenario-1..5
# END_CONTRACT: apply_fixes
def apply_fixes(
    document_path: Path,
    violations: list[Violation],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    rules_dir: Path | None = None,
) -> FixReport:
    # START_BLOCK_APPLY_FIX
    destructive = [v for v in violations if v.fix_category == FixCategory.DESTRUCTIVE]
    if destructive:
        ids = ", ".join(v.rule_id for v in destructive)
        raise DestructiveRejectedError(
            f"Destructive fixes rejected: {ids}. "
            f"These require manual review: {destructive[0].hint}"
        )

    backup_path = create_backup(document_path)
    original_hash = _compute_file_hash(document_path)

    applied: list[str] = []
    fixable = [
        v for v in violations if v.fix_category in (FixCategory.SAFE, FixCategory.VISUAL)
    ]

    iteration = 0
    for iteration in range(max_iterations):
        iteration_fixes: list[str] = []
        for v in fixable:
            if _apply_simple_fix(document_path, v):
                iteration_fixes.append(v.rule_id)

        if not iteration_fixes:
            break
        applied.extend(iteration_fixes)

        report = run_checks(document_path, SeverityMode.AUDIT, rules_dir=rules_dir)
        remaining_fixable = [
            v
            for v in report.violations
            if v.fix_category in (FixCategory.SAFE, FixCategory.VISUAL)
        ]
        if not remaining_fixable:
            break

        new_hash = _compute_file_hash(document_path)
        if iteration > 0 and new_hash == original_hash:
            raise CascadeDetectedError(
                f"Fix cascade detected after {iteration + 1} iterations"
            )

    final_report = run_checks(document_path, SeverityMode.AUDIT, rules_dir=rules_dir)

    new_hash = _compute_file_hash(document_path)
    diff_desc = "file modified" if new_hash != original_hash else "no changes"

    logger.info(
        f"[{_LOG_PREFIX}][apply][BLOCK_APPLY_FIX] "
        f"iterations={iteration + 1}, applied={applied}, diff={diff_desc}"
    )
    # END_BLOCK_APPLY_FIX

    return FixReport(
        fixed_path=document_path,
        backup_path=backup_path,
        iterations=iteration + 1,
        applied_fixes=applied,
        remaining_violations=final_report.violations,
        diff=diff_desc,
    )


# START_CONTRACT: fix
#   PURPOSE: Main fix entry point
#   INPUTS: { document_path, rules_dir }
#   OUTPUTS: { FixReport }
#   SIDE_EFFECTS: modifies document, creates backup
# END_CONTRACT: fix
def fix(document_path: str | Path, rules_dir: Path | None = None) -> FixReport:
    path = Path(document_path)
    report = run_checks(path, SeverityMode.AUDIT, rules_dir=rules_dir)
    return apply_fixes(path, report.violations, rules_dir=rules_dir)
