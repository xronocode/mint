"""E2E showcase: dual-model modular pipeline."""
from pathlib import Path
from mint.create import CreateRequest, create

PROMPT = (
    "Create a comprehensive quarterly business review document for Q1 2026. "
    "Include: 1) Cover page with company name 'Nexus Technologies Inc.' and date. "
    "2) Executive summary with 3-4 paragraphs about company performance. "
    "3) Revenue breakdown table with columns: Product Line, Q1 Revenue, Q4 2025 Revenue, "
    "Growth %. Add 5 product lines with realistic data. Use colored header row "
    "and alternating row shading.\n"
    "4) Key achievements section with 5-6 detailed bullet points. "
    "5) Challenges and risks section with subsections and mitigation strategies. "
    "6) Strategic initiatives for Q2 with timeline table.\n"
    "Use professional blue color scheme (#1E40AF primary, #3B82F6 accent). "
    "Proper heading hierarchy (Heading1/2/3), page numbers in footer. "
    "Body text 11pt, headings 16-24pt. "
    "Every section must have at least 5 paragraphs or a full table."
)

req = CreateRequest(
    format="docx",
    tier="frontier",
    prompt=PROMPT,
    modular=True,
    llm_base_url="http://10.128.26.10:11434/v1",
    llm_model="qwen3.6:35b",
    llm_fallback_model="glm-4.7-flash:latest",
)

result = create(req, rules_dir=Path("rules"))

print(f"Success: {result.success}")
print(f"Mode: {result.execution_mode}")
print(f"Output: {result.output_path}")
print(f"Duration: {result.duration_ms / 1000:.1f}s")
print(f"Plan sections: {result.sections_total}")
print(f"Sections succeeded: {result.sections_succeeded}")
if result.error:
    print(f"Error: {result.error}")
if result.validation_report:
    print(f"Validation: passed={result.validation_report.passed}, "
          f"violations={result.validation_report.total}")
    hard = [v for v in result.validation_report.violations
            if v.severity.value == "hard"]
    soft = [v for v in result.validation_report.violations
            if v.severity.value == "soft"]
    print(f"  Hard: {len(hard)}, Soft: {len(soft)}")
if result.plan:
    print(f"\nPlan sections:")
    for s in result.plan.sections:
        print(f"  [{s.type}] {s.title} (level={s.level})")
