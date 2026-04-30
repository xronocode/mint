# FILE: src/mint/sandbox/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Sandboxed Node.js execution for model-generated JS code
#   SCOPE: Pre-check code, run in isolated VM, capture output
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

from mint.sandbox.executor import (
    SandboxError,
    SandboxResult,
    SandboxRuntimeError,
    SandboxTimeoutError,
    SandboxViolationError,
    execute,
    sandbox,
    validate_code,
)

__all__ = [
    "SandboxError",
    "SandboxResult",
    "SandboxRuntimeError",
    "SandboxTimeoutError",
    "SandboxViolationError",
    "execute",
    "sandbox",
    "validate_code",
]
