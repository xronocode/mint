"""Visual quality scorer for MINT-generated DOCX via local VLM (qwen2.5vl:7b).

Pipeline:
1. soffice headless → PDF
2. pdftoppm → first N pages PNG @ 110dpi
3. For each page: send to qwen2.5vl:7b with rubric
4. Aggregate per-axis + overall + collected issues

Output: JSON to stdout. Returns 0 if overall_score >= --threshold else 1.

Usage:
    python e2e_qa_visual.py path/to/doc.docx [--pages 5] [--threshold 70]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from statistics import mean
from typing import Any

OLLAMA_URL = "http://10.128.26.10:11434/api/chat"
VLM_MODEL = "qwen2.5vl:7b"
SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

PROMPT_TEMPLATE = """You are a graphic-design reviewer. Analyze this single page from a generated DOCX.
Score 0-100 on each axis. Return STRICT JSON ONLY (no markdown fences, no commentary):

{{
  "typography": <int>,
  "layout": <int>,
  "visual_richness": <int>,
  "professional": <int>,
  "overall": <int>,
  "issues": ["<short concrete problem 1>", "<short concrete problem 2>", "..."],
  "highlights": ["<what works 1>", "<what works 2>"]
}}

Axes (rate by appropriateness for the page TYPE — recognize cover/TOC/section-break pages):
- typography: font consistency, weight/size hierarchy, no jumping styles
- layout: balance, alignment; whitespace is GOOD on cover/TOC pages, BAD only when content was expected
- visual_richness: tables/callouts/images/decorative elements; weight LESS for cover pages where richness=plenty of empty white space and a centered title is normal
- professional: passes for a corporate/technical report, not amateur
- overall: weighted. **A clean cover page (centered title, subtitle, decorative line, footer) deserves 75-85 even if mostly empty — empty space is the design.** A TOC page with the heading and a populated entry list is 80+. Penalize empty-after-heading ONLY for body content sections (page 3+ that are clearly mid-document).

Issues: 3-7 short concrete problems (<100 chars each). Don't say "title is too large" unless title clearly takes >40% of page height. Don't say "no images" if a clean text-and-tables design is appropriate.
Highlights: 1-3 things this page does well.

Page context: page {page_no} of {total_pages}. Doc title: "{doc_title}".
Treat page 1 as the COVER (sparse + decorative is correct), page 2 as TOC if title says so, pages 3+ as body content.

Return ONLY the JSON object.
"""


def render_pdf(docx_path: Path, out_dir: Path) -> Path:
    """Render DOCX to PDF in `out_dir`. Returns the PDF path."""
    subprocess.run(
        [SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir),
         str(docx_path)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    pdf = out_dir / (docx_path.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError(f"soffice did not produce {pdf}")
    return pdf


def render_pngs(pdf_path: Path, out_dir: Path, max_pages: int) -> list[Path]:
    """Render PDF pages to PNG. Returns list of paths."""
    base = out_dir / "page"
    subprocess.run(
        ["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", str(max_pages),
         str(pdf_path), str(base)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return sorted(out_dir.glob("page-*.png"))


def doc_title(docx_path: Path) -> str:
    """Extract the title from docx core.xml or first paragraph; fallback to filename."""
    import zipfile
    from lxml import etree
    try:
        with zipfile.ZipFile(docx_path) as zf:
            if "docProps/core.xml" in zf.namelist():
                root = etree.fromstring(zf.read("docProps/core.xml"))
                ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                title = root.findtext("dc:title", default="", namespaces=ns)
                if title:
                    return title
            doc = etree.fromstring(zf.read("word/document.xml"))
            W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            for p in doc.iter(f"{{{W}}}p")[:5]:  # type: ignore[index]
                txt = "".join(t.text or "" for t in p.iter(f"{{{W}}}t"))
                if txt.strip():
                    return txt.strip()[:80]
    except Exception:
        pass
    return docx_path.stem


def call_vlm(image_path: Path, page_no: int, total_pages: int, doc_title_str: str) -> dict[str, Any]:
    img_b64 = base64.b64encode(image_path.read_bytes()).decode()
    prompt = PROMPT_TEMPLATE.format(
        page_no=page_no, total_pages=total_pages,
        doc_title=doc_title_str.replace('"', "'"),
    )
    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 2000},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    raw = data["message"]["content"]
    # strip code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # try to find {...} in the raw text
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        return {
            "typography": 0, "layout": 0, "visual_richness": 0,
            "professional": 0, "overall": 0,
            "issues": [f"VLM returned unparseable: {raw[:200]}"],
            "highlights": [],
        }


def score_doc(docx_path: Path, max_pages: int) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="qa_visual_"))
    try:
        pdf = render_pdf(docx_path, workdir)
        pngs = render_pngs(pdf, workdir, max_pages)
        if not pngs:
            return {"ok": False, "error": "no PNG pages produced"}
        total = len(pngs)
        title = doc_title(docx_path)
        per_page: list[dict[str, Any]] = []
        for i, png in enumerate(pngs, 1):
            score = call_vlm(png, i, total, title)
            score["page"] = i
            per_page.append(score)

        # Aggregate
        axes = ("typography", "layout", "visual_richness", "professional", "overall")
        agg: dict[str, float] = {}
        for axis in axes:
            vals = [int(p.get(axis, 0) or 0) for p in per_page]
            agg[axis] = round(mean(vals), 1) if vals else 0.0

        all_issues: list[str] = []
        for p in per_page:
            for it in (p.get("issues") or []):
                all_issues.append(f"page {p['page']}: {it}")

        return {
            "ok": True,
            "path": str(docx_path),
            "title": title,
            "pages_scored": total,
            "scores": agg,
            "min_overall": min(int(p.get("overall", 0) or 0) for p in per_page),
            "max_overall": max(int(p.get("overall", 0) or 0) for p in per_page),
            "issues": all_issues,
            "per_page": per_page,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("docx", type=Path)
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=70.0,
                    help="exit 0 if scores.overall >= threshold")
    args = ap.parse_args()
    result = score_doc(args.docx, args.pages)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("ok"):
        return 2
    return 0 if result["scores"]["overall"] >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
