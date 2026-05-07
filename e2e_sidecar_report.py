"""Generate Vision Sidecar showcase report via MINT single-pass code mode."""
from pathlib import Path

from mint.create import CreateRequest, create

PROMPT = (
    "Create a professional DOCX document titled 'Vision Sidecar MCP - "
    "Operations Report & Technical Overview'.\n\n"

    "This is a technical report about a Vision Language Model (VLM) sidecar "
    "service that provides image analysis tools to AI coding assistants.\n\n"

    "SECTION 1 - COVER PAGE:\n"
    "Title: 'Vision Sidecar MCP' (size:72, bold, color:1B3A5C, centered).\n"
    "Subtitle: 'Operations Report & Technical Overview' (size:28, centered).\n"
    "Version: 'v0.4.0 - May 2026' (size:20, color:666666, centered).\n"
    "Paragraph: 'A comprehensive report on the Vision Language Model sidecar "
    "service providing image analysis capabilities to AI coding assistants "
    "via the Model Context Protocol (MCP).' (size:22, centered).\n"
    "Horizontal line: Paragraph with bottom border (BorderStyle.SINGLE, "
    "size:6, color:2E75B6, space:4).\n\n"

    "SECTION 2 - EXECUTIVE SUMMARY (Heading1):\n"
    "Paragraph: Vision Sidecar is an MCP server that exposes 8 image analysis "
    "tools powered by a local Vision Language Model. It runs as a companion "
    "service alongside AI coding assistants, enabling screenshot analysis, "
    "text extraction, error diagnosis, and structured UI parsing.\n\n"

    "Key metrics callout box: Paragraph with left border (color:2E75B6, "
    "size:12, space:8), shading fill EBF5FB. Content:\n"
    "Bold 'Key Metrics (Apr 30 - May 7, 2026):' (color:2E75B6)\n"
    "Bullet list with items:\n"
    "  150 total API calls over 8 days of operation\n"
    "  0 errors, 0 empty responses - 100% reliability\n"
    "  Median response time: 7.6 seconds\n"
    "  16.1 MB of images processed\n"
    "  8 automatic image upscales for small images\n"
    "  5 active projects using the service\n\n"

    "SECTION 3 - ARCHITECTURE (Heading1):\n"
    "Paragraph: The sidecar runs as a FastMCP server connecting to a local "
    "Ollama instance. All inference happens on-premises with no external API calls.\n\n"

    "Heading2: '3.1 System Configuration'.\n"
    "TABLE (2 columns: Parameter, Value) with rows:\n"
    "  Version | 0.4.0\n"
    "  VLM Model | qwen3-vl:8b\n"
    "  Ollama URL | 10.128.26.10:11434\n"
    "  Context Window | 32,768 tokens\n"
    "  Max Predict | 8,192 tokens\n"
    "  Temperature | 0.1\n"
    "  Timeout | 300 seconds\n"
    "  Upscale Threshold | <400px width\n"
    "  Upscale Factor | 2x\n"
    "Header row: shading fill 1B3A5C, white bold text. Alt rows fill F0F0F0.\n\n"

    "Heading2: '3.2 Available Tools'.\n"
    "TABLE (3 columns: Tool, Purpose, Calls) with rows:\n"
    "  extract_text_from_image | OCR and text extraction from screenshots | 69\n"
    "  analyze_ui_screenshot | UI component hierarchy and layout analysis | 29\n"
    "  analyze_structured | Structured JSON analysis of screenshots | 19\n"
    "  diagnose_error | Error diagnosis from error screenshots | 19\n"
    "  analyze_image | General-purpose image analysis | 10\n"
    "  extract_table | Table extraction as JSON | 4\n"
    "  check_vlm_status | Health check and configuration | (meta)\n"
    "  get_telemetry | Usage statistics retrieval | (meta)\n"
    "Same table styling.\n\n"

    "Info callout: border color 2E75B6, shading EBF5FB. Text: 'All 6 analysis "
    "tools use the same underlying VLM model (qwen3-vl:8b) with tool-specific "
    "system prompts. The analyze tools accept an optional viewport_hint parameter "
    "(desktop, tablet, mobile, small_mobile) for responsive-specific analysis.'\n\n"

    "SECTION 4 - PERFORMANCE ANALYSIS (Heading1):\n"
    "Paragraph: Performance analysis of 114 successful inference calls over "
    "the reporting period.\n\n"

    "Heading2: '4.1 Response Time Distribution'.\n"
    "TABLE (2 columns: Percentile, Latency) with rows:\n"
    "  Minimum | 1.3 seconds\n"
    "  p50 (Median) | 7.6 seconds\n"
    "  p90 | ~25 seconds\n"
    "  p95 | 35.8 seconds\n"
    "  p99 | 60.2 seconds\n"
    "  Maximum | 81.0 seconds\n"
    "  Total inference time | 1,428 seconds (23.8 minutes)\n"
    "Same table styling.\n\n"

    "Heading2: '4.2 Response Size Analysis'.\n"
    "TABLE (2 columns: Metric, Value) with rows:\n"
    "  p50 response | 1,182 characters\n"
    "  p95 response | 11,045 characters\n"
    "  p99 response | 23,140 characters\n"
    "  Maximum response | 24,399 characters\n"
    "  Total output | 325,486 characters (~325K chars)\n"
    "Same table styling.\n\n"

    "Heading2: '4.3 Image Processing'.\n"
    "TABLE (2 columns: Metric, Value) with rows:\n"
    "  p50 image size | 130 KB\n"
    "  p95 image size | 257 KB\n"
    "  Maximum image | 625 KB\n"
    "  Total processed | 16.1 MB\n"
    "  Upscale rate | 5.3% (8 of 150 calls)\n"
    "  Upscale factor | 2x (images <400px width)\n"
    "Same table styling.\n\n"

    "SECTION 5 - RELIABILITY (Heading1):\n"
    "Paragraph: The service demonstrated perfect reliability during the "
    "reporting period.\n\n"

    "TABLE (2 columns: Metric, Value) with rows:\n"
    "  Total calls | 150\n"
    "  Successful (ok) | 114 (76%)\n"
    "  Status N/A | 36 (24%, meta/status calls)\n"
    "  Errors | 0 (0%)\n"
    "  Empty responses | 0 (0%)\n"
    "  JSON parse success | 15 of 15 (100%)\n"
    "  Uptime | Continuous (8 days)\n"
    "Header: 1B3A5C bg, white bold. Alt rows.\n\n"

    "Success callout: border color 2E8B57, shading fill E8F5E9. "
    "Bold green title 'Reliability Highlights' (color:2E8B57). "
    "Text: 'Zero errors across 150 calls. 100% JSON parse success on "
    "structured output tools. No empty responses recorded. The 36 N/A "
    "status calls are health checks and telemetry queries that do not "
    "invoke the VLM model.'\n\n"

    "SECTION 6 - PROJECT USAGE (Heading1):\n"
    "Paragraph: The sidecar serves multiple development projects simultaneously.\n\n"

    "TABLE (2 columns: Project, Role) with rows:\n"
    "  kvorum | Primary user - screenshot analysis and OCR\n"
    "  kombo/kombo | UI evaluation - structured analysis and comparison\n"
    "  mint | Document generation showcase - minimal usage (1 call)\n"
    "Header: 1B3A5C bg, white bold. Alt rows.\n\n"

    "SECTION 7 - AVAILABLE MODELS (Heading1):\n"
    "Paragraph: The Ollama instance hosts 16 models across multiple families. "
    "The sidecar uses qwen3-vl:8b for all vision tasks.\n\n"

    "TABLE (2 columns: Model, Size) with rows:\n"
    "  qwen3.6:35b | 35B params (MINT document generation)\n"
    "  qwen3.5:35b | 35B params\n"
    "  qwen3.5:latest | Variable\n"
    "  qwen3:latest | Variable\n"
    "  gemma4:31b | 31B params\n"
    "  gemma4:e2b | Variable\n"
    "  gemma3:12b | 12B params\n"
    "  gemma3:4b | 4B params\n"
    "  gemma3:latest | Variable\n"
    "  qwen3-vl:8b | 8B params (VLM - used by sidecar)\n"
    "  qwen2.5vl:7b | 7B params (VLM alternative)\n"
    "  glm-4.7-flash:latest | Flash model\n"
    "  gpt-oss:latest | Open-source model\n"
    "  minimax-m2.7:cloud | Cloud model\n"
    "  s44t12/coder:latest | Code-specialized\n"
    "  bge-m3:latest | Embedding model\n"
    "Same table styling.\n\n"

    "Info callout about VLM model choice.\n\n"

    "SECTION 8 - APPENDIX (Heading1):\n"
    "Heading2: 'Sample Telemetry Event'.\n"
    "Code block (Courier New size:18, shading fill F5F5F5, border DDDDDDD): "
    "show JSON example:\n"
    "{ ts: '2026-05-07T20:06:06Z', tool: 'analyze_structured', "
    "viewport_hint: 'desktop', project_root: '/Users/.../kombo', "
    "image_bytes: 619845, model: 'qwen3-vl:8b', response_chars: 2734, "
    "ollama_wall_s: 14.78, ollama_status: 'ok' }\n\n"

    "Final paragraph: bold 'End of Report', then "
    "'Vision Sidecar MCP v0.4.0 - Generated May 2026'.\n\n"

    "DESIGN RULES:\n"
    "- Font sizes in HALF-POINTS: 11pt=22, 14pt=28, 16pt=32, 36pt=72. NEVER above 100.\n"
    "- Use heading: HeadingLevel.HEADING_1/2 for headings.\n"
    "- Table width: {size:9360, type:WidthType.DXA}. columnWidths must sum to 9360.\n"
    "- Cell width: {size:N, type:WidthType.DXA}. Cell margins: "
    "{top:80,bottom:80,left:120,right:120}.\n"
    "- Shading: always {fill:'HEX', type:ShadingType.CLEAR}. NEVER type:'solid'.\n"
    "- Separate ALL elements with commas.\n"
    "- No import/require/fs. All docx-js classes are pre-loaded.\n"
    "- Use style: 'Heading1'/'Heading2' for headings, no style for body.\n"
    "- Add header with 'Vision Sidecar MCP' and footer with page numbers.\n"
)

result = CreateRequest(
    format="docx",
    tier="frontier",
    prompt=PROMPT,
    modular=True,
    llm_base_url="http://10.128.26.10:11434/v1",
    llm_model="qwen3.6:35b",
    llm_fallback_model="glm-4.7-flash:latest",
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
    hard = [v for v in output.validation_report.violations if v.severity.value == "hard"]
    soft = [v for v in output.validation_report.violations if v.severity.value == "soft"]
    print(f"Validation: hard={len(hard)}, soft={len(soft)}")
if hasattr(output, 'section_details') and output.section_details:
    for d in output.section_details:
        model_short = d["model"].split(":")[0]
        ok = "OK" if d["success"] else "FAIL"
        print(f"  [{ok}] {d['section_id'][:30]:<32} "
              f"{model_short:<15} att={d['attempt']} "
              f"{d['duration_ms']:>5}ms {d['code_chars']:>5}chars")
