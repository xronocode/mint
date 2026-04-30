from pathlib import Path

import pytest

from mint.sandbox import (
    SandboxTimeoutError,
    SandboxViolationError,
    execute,
    sandbox,
    validate_code,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestValidateCode:
    def test_clean_code_passes(self) -> None:
        code = "const x = 1;"
        assert validate_code(code) == []

    def test_fs_require_detected(self) -> None:
        code = 'const fs = require("fs");'
        violations = validate_code(code)
        assert "fs" in violations

    def test_net_require_detected(self) -> None:
        code = 'const net = require("net");'
        violations = validate_code(code)
        assert "net" in violations

    def test_child_process_detected(self) -> None:
        code = 'const cp = require("child_process");'
        violations = validate_code(code)
        assert "child_process" in violations

    def test_http_require_detected(self) -> None:
        code = 'const http = require("http");'
        violations = validate_code(code)
        assert "http" in violations

    def test_https_require_detected(self) -> None:
        code = 'const https = require("https");'
        violations = validate_code(code)
        assert "https" in violations

    def test_process_exit_detected(self) -> None:
        code = "process.exit(1);"
        violations = validate_code(code)
        assert "process.exit" in violations

    def test_eval_detected(self) -> None:
        code = 'eval("malicious")'
        violations = validate_code(code)
        assert "eval" in violations

    def test_multiple_violations(self) -> None:
        code = 'const fs = require("fs"); const net = require("net");'
        violations = validate_code(code)
        assert "fs" in violations
        assert "net" in violations


class TestExecuteValidation:
    def test_malicious_fs_raises(self) -> None:
        code = (FIXTURES / "malicious_fs.js").read_text()
        with pytest.raises(SandboxViolationError, match="fs"):
            execute(code)

    def test_malicious_net_raises(self) -> None:
        code = (FIXTURES / "malicious_net.js").read_text()
        with pytest.raises(SandboxViolationError, match="net"):
            execute(code)


class TestExecuteDocx:
    def test_hello_world_docx(self) -> None:
        code = (FIXTURES / "hello_world_docx.js").read_text()
        result = execute(code, timeout=30)
        assert result.success
        assert result.output_path is not None
        assert result.output_path.exists()
        assert result.duration_ms > 0
        assert result.output_path.suffix == ".docx"


class TestExecutePptx:
    def test_hello_world_pptx(self) -> None:
        code = (FIXTURES / "hello_world_pptx.js").read_text()
        result = execute(code, timeout=30)
        assert result.success
        assert result.output_path is not None
        assert result.output_path.exists()
        assert result.output_path.suffix == ".pptx"


class TestExecuteErrors:
    def test_syntax_error_returns_failure(self) -> None:
        code = (FIXTURES / "syntax_error.js").read_text()
        result = execute(code, timeout=10)
        assert not result.success
        assert result.error is not None

    def test_timeout_raises(self) -> None:
        code = (FIXTURES / "infinite_loop.js").read_text()
        with pytest.raises(SandboxTimeoutError):
            execute(code, timeout=3)


class TestSandboxFacade:
    def test_facade_validate_code(self) -> None:
        assert sandbox.validate_code('require("fs")') == ["fs"]

    def test_facade_execute(self) -> None:
        code = (FIXTURES / "hello_world_docx.js").read_text()
        result = sandbox.execute(code)
        assert result.success
