# FILE: src/mint/sandbox/executor.py
# VERSION: 0.1.0
# START_CONTRACT: executor
#   PURPOSE: Core sandbox execution logic — validate code, run in Node.js VM, capture output
#   INPUTS: { code: str, timeout: int }
#   OUTPUTS: { SandboxResult }
#   SIDE_EFFECTS: spawns Node.js subprocess, writes temp files
#   LINKS: docs/verification-plan.xml#V-M-SANDBOX
# END_CONTRACT: executor

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PREFIX = "Sandbox"

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("fs", re.compile(r"""require\s*\(\s*['"]fs['"]\s*\)""")),
    ("net", re.compile(r"""require\s*\(\s*['"]net['"]\s*\)""")),
    ("child_process", re.compile(r"""require\s*\(\s*['"]child_process['"]\s*\)""")),
    ("http", re.compile(r"""require\s*\(\s*['"]http['"]\s*\)""")),
    ("https", re.compile(r"""require\s*\(\s*['"]https['"]\s*\)""")),
    ("process.exit", re.compile(r"""process\s*\.\s*exit""")),
    ("eval", re.compile(r"""\beval\s*\(""")),
    ("Function constructor", re.compile(r"""new\s+Function\s*\(""")),
]


class SandboxError(Exception):
    """Base sandbox error."""


class SandboxViolationError(SandboxError):
    """Raised when code contains forbidden patterns."""


class SandboxTimeoutError(SandboxError):
    """Raised when execution exceeds the timeout."""


class SandboxRuntimeError(SandboxError):
    """Raised when the sandboxed code throws a runtime error."""


# START_CONTRACT: SandboxResult
#   PURPOSE: Dataclass holding sandbox execution results
#   INPUTS: { field values }
#   OUTPUTS: { SandboxResult }
#   SIDE_EFFECTS: none
# END_CONTRACT: SandboxResult
@dataclass(frozen=True)
class SandboxResult:
    success: bool
    output_path: Path | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0


# START_CONTRACT: validate_code
#   PURPOSE: Static check — reject code with forbidden patterns (fs, net, etc.)
#   INPUTS: { code: str - JavaScript source code }
#   OUTPUTS: { list[str] - list of forbidden pattern names found }
#   SIDE_EFFECTS: none
#   LINKS: V-M-SANDBOX scenario-3, scenario-4
# END_CONTRACT: validate_code
def validate_code(code: str) -> list[str]:
    # START_BLOCK_VALIDATE_CODE
    violations: list[str] = []
    for name, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(code):
            violations.append(name)
            logger.info(
                f"[{_LOG_PREFIX}][validate][BLOCK_VALIDATE_CODE] "
                f"Forbidden pattern detected: {name}"
            )
    # END_BLOCK_VALIDATE_CODE
    return violations


# START_CONTRACT: execute
#   PURPOSE: Run JS code in Node.js sandbox with timeout enforcement
#   INPUTS: {
#     code: str - JavaScript source,
#     timeout: int - max seconds (default 30),
#     libraries: dict[str,str] - name->version (unused, for future),
#     node_path: Path | None - path to node binary
#   }
#   OUTPUTS: { SandboxResult }
#   SIDE_EFFECTS: writes temp files, spawns subprocess
#   LINKS: V-M-SANDBOX scenario-1..6
# END_CONTRACT: execute
def execute(
    code: str,
    timeout: int = 30,
    libraries: dict[str, str] | None = None,
    node_path: Path | None = None,
) -> SandboxResult:
    _ = libraries

    # START_BLOCK_VALIDATE_CODE
    violations = validate_code(code)
    if violations:
        violation_names = ", ".join(violations)
        raise SandboxViolationError(
            f"Code contains forbidden patterns: {violation_names}"
        )
    # END_BLOCK_VALIDATE_CODE

    runner_path = Path(__file__).parent / "runner.js"
    node_bin = str(node_path) if node_path else "node"

    with tempfile.TemporaryDirectory(prefix="mint_sandbox_") as tmpdir:
        code_file = Path(tmpdir) / "user_code.js"
        output_dir = Path(tmpdir) / "output"
        code_file.write_text(code)

        start = time.monotonic()
        # START_BLOCK_EXECUTE_CODE
        try:
            proc = subprocess.run(
                [
                    node_bin,
                    str(runner_path),
                    str(code_file),
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 2,
                cwd=str(Path(__file__).parent.parent.parent.parent),
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.monotonic() - start) * 1000
            logger.info(
                f"[{_LOG_PREFIX}][execute][BLOCK_EXECUTE_CODE] "
                f"Timeout after {duration_ms:.0f}ms"
            )
            raise SandboxTimeoutError(
                f"Execution exceeded {timeout}s timeout"
            ) from None
        # END_BLOCK_EXECUTE_CODE

        duration_ms = (time.monotonic() - start) * 1000

        if proc.returncode != 0:
            logger.info(
                f"[{_LOG_PREFIX}][execute][BLOCK_EXECUTE_CODE] "
                f"Runtime error: {proc.stderr.strip()}"
            )
            return SandboxResult(
                success=False,
                error=proc.stderr.strip(),
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
            )

        output_files = list(output_dir.glob("*")) if output_dir.exists() else []
        output_path = None
        if output_files:
            src_file = output_files[0]
            persistent_dir = Path(tempfile.gettempdir()) / "mint_output"
            persistent_dir.mkdir(exist_ok=True)
            dest = persistent_dir / src_file.name
            dest.write_bytes(src_file.read_bytes())
            output_path = dest

        return SandboxResult(
            success=True,
            output_path=output_path,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
        )


class _Sandbox:
    """Stateless sandbox facade for dependency injection."""

    def execute(
        self,
        code: str,
        timeout: int = 30,
        libraries: dict[str, str] | None = None,
    ) -> SandboxResult:
        return execute(code, timeout=timeout, libraries=libraries)

    @staticmethod
    def validate_code(code: str) -> list[str]:
        return validate_code(code)


sandbox = _Sandbox()
