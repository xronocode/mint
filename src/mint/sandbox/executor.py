# FILE: src/mint/sandbox/executor.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Core sandbox execution logic — validate code, repair syntax, run in Node.js VM
#   SCOPE: Pre-check code, repair brackets, run in isolated Node.js subprocess
#   DEPENDS: none
#   LINKS: docs/knowledge-graph.xml#M-SANDBOX, docs/verification-plan.xml#V-M-SANDBOX
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   SandboxError - base sandbox exception
#   SandboxViolationError - forbidden code pattern detected
#   SandboxTimeoutError - execution exceeded timeout
#   SandboxRuntimeError - JS runtime error
#   SandboxResult - execution result dataclass
#   validate_code - static check for forbidden patterns
#   execute - run JS code in sandbox, return result
#   sandbox - module-level sandbox instance
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - Updated module contract markup
# END_CHANGE_SUMMARY

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
    ("fs", re.compile(r"""require\s*\(\s*['"`]fs['"`]\s*\)""")),
    ("net", re.compile(r"""require\s*\(\s*['"`]net['"`]\s*\)""")),
    ("child_process", re.compile(r"""require\s*\(\s*['"`]child_process['"`]\s*\)""")),
    ("http", re.compile(r"""require\s*\(\s*['"`]http['"`]\s*\)""")),
    ("https", re.compile(r"""require\s*\(\s*['"`]https['"`]\s*\)""")),
    ("process.exit", re.compile(r"""\bprocess\s*\[\s*['"`]exit['"`]\s*\]|\bprocess\s*\.\s*exit""")),
    ("eval", re.compile(r"""\beval\s*\(""")),
    ("Function constructor", re.compile(r"""new\s+Function\s*\(""")),
    ("dynamic import", re.compile(r"""import\s*\(""")),
    ("bracket require", re.compile(r"""require\s*\[""")),
]


class SandboxError(Exception):
    pass


class SandboxViolationError(SandboxError):
    pass


class SandboxTimeoutError(SandboxError):
    pass


class SandboxRuntimeError(SandboxError):
    pass


@dataclass(frozen=True)
class SandboxResult:
    success: bool
    output_path: Path | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0


def validate_code(code: str) -> list[str]:
    violations: list[str] = []
    for name, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(code):
            violations.append(name)
            logger.info(
                f"[{_LOG_PREFIX}][validate] Forbidden pattern detected: {name}"
            )
    return violations


def _repair_brackets(code: str) -> str:
    open_brackets = {"(": ")", "[": "]", "{": "}"}
    close_set = {")", "]", "}"}
    stack: list[str] = []

    in_string: str | None = None
    escape_next = False
    in_line_comment = False
    in_block_comment = False

    i = 0
    while i < len(code):
        ch = code[i]

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and i + 1 < len(code) and code[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if escape_next:
            escape_next = False
            i += 1
            continue

        if ch == "\\" and in_string:
            escape_next = True
            i += 1
            continue

        if ch == "/" and i + 1 < len(code):
            if code[i + 1] == "/":
                in_line_comment = True
                i += 2
                continue
            if code[i + 1] == "*":
                in_block_comment = True
                i += 2
                continue

        if in_string:
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch in ('"', "'", "`"):
            in_string = ch
            i += 1
            continue

        if ch in open_brackets:
            stack.append(open_brackets[ch])
        elif ch in close_set and stack and stack[-1] == ch:
            stack.pop()

        i += 1

    if stack:
        code = code.rstrip()
        for bracket in reversed(stack):
            code += "\n" + bracket

    return code


def _check_syntax(code: str, node_bin: str) -> str | None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False
    ) as f:
        f.write(code)
        tmp = f.name
    try:
        result = subprocess.run(
            [node_bin, "--check", tmp],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return None
        return result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "syntax check timed out"
    finally:
        Path(tmp).unlink(missing_ok=True)


def execute(
    code: str,
    timeout: int = 30,
    libraries: dict[str, str] | None = None,
    node_path: Path | None = None,
) -> SandboxResult:
    _ = libraries

    violations = validate_code(code)
    if violations:
        violation_names = ", ".join(violations)
        raise SandboxViolationError(
            f"Code contains forbidden patterns: {violation_names}"
        )

    runner_path = Path(__file__).parent / "runner.js"
    node_bin = str(node_path) if node_path else "node"

    syntax_error = _check_syntax(code, node_bin)
    if syntax_error:
        repaired = _repair_brackets(code)
        if repaired != code:
            new_error = _check_syntax(repaired, node_bin)
            if new_error is None:
                logger.info(
                    "[Sandbox][execute] Repaired bracket mismatch in generated code"
                )
                code = repaired
            else:
                logger.info(
                    "[Sandbox][execute] Bracket repair did not fix syntax error: %s",
                    new_error[:200],
                )

    with tempfile.TemporaryDirectory(prefix="mint_sandbox_") as tmpdir:
        code_file = Path(tmpdir) / "user_code.js"
        output_dir = Path(tmpdir) / "output"
        code_file.write_text(code)

        # Optional debug dump of the assembled JS for postmortem inspection.
        # Activated by MINT_DEBUG_DUMP_JS=<path>; the file is written even on
        # syntax-error paths so failures can be reproduced.
        import os as _os
        _dump_target = _os.environ.get("MINT_DEBUG_DUMP_JS")
        if _dump_target:
            from mint._security import resolve_safe_path

            try:
                safe_dump = resolve_safe_path(
                    _dump_target, Path(_os.environ.get("MINT_ROOT", ".")).resolve()
                )
                safe_dump.write_text(code)
            except (OSError, ValueError) as _exc:
                logger.warning(
                    "[Sandbox][execute] failed to dump JS to %s: %s",
                    _dump_target,
                    _exc,
                )

        start = time.monotonic()
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
                f"[{_LOG_PREFIX}][execute] Timeout after {duration_ms:.0f}ms"
            )
            raise SandboxTimeoutError(
                f"Execution exceeded {timeout}s timeout"
            ) from None

        duration_ms = (time.monotonic() - start) * 1000

        if proc.returncode != 0:
            logger.info(
                f"[{_LOG_PREFIX}][execute] Runtime error: {proc.stderr.strip()}"
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
