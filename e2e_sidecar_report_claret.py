"""Run e2e_sidecar_report.py with the claret_serif theme instead of default."""
from pathlib import Path

from mint.create import CreateRequest, create

# Reuse the same prompt as the showcase script
exec(open("e2e_sidecar_report.py").read().split("result = CreateRequest")[0])

result = CreateRequest(
    format="docx",
    tier="frontier",
    prompt=PROMPT,  # type: ignore[name-defined]  # noqa: F821
    modular=True,
    llm_base_url="http://10.128.26.10:11434/v1",
    llm_model="gpt-oss:latest",
    llm_fallback_model="gemma4:31b",
    theme_name="claret_serif",
)

output = create(result, rules_dir=Path("rules"))

print(f"Success: {output.success}")
print(f"Mode: {output.execution_mode}")
print(f"Output: {output.output_path}")
print(f"Duration: {output.duration_ms / 1000:.1f}s")
print(f"Sections: {output.sections_succeeded}/{output.sections_total}")
if output.error:
    print(f"Error: {output.error[:300]}")
if output.validation_report:
    hard = [
        v for v in output.validation_report.violations
        if v.severity.value == "hard"
    ]
    soft = [
        v for v in output.validation_report.violations
        if v.severity.value == "soft"
    ]
    print(f"Validation: hard={len(hard)}, soft={len(soft)}")
