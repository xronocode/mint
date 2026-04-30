# MINT – Model-Independent Normalization Toolkit
## Concept & Roadmap v5.0

**Author:** Mike Yevdokimov
**Date:** 2026-04-25
**Status:** Pre-pilot
**Previous:** v4.0 (GOQL→MINT rename), v3.0, v2.0, v1.0
**Key change v5:** self-contained runtime (no dvejsada dependency), Claude removed from stack

---

## 1. One Line

> Claude-quality documents. Without Claude.

MINT is a self-contained, open-source toolkit that brings Claude-grade document generation to any LLM – cloud or local, frontier or 7B.

---

## 2. Two Entities

**MINT Runtime** – open-source software (MIT). Contains everything needed to generate, validate, and QA office documents. Includes execution runtime (docx-js, pptxgenjs), skill prompts, templates, style extraction, validation engine, QA pipeline. Versioned as software (0.x.x).

**GRACE Spec** – open protocol for self-describing documents. Embedded metadata via Custom XML Parts makes documents agent-navigable across models. Versioned as standard (1.x). Separate repository.

---

## 3. Problem

Claude has the best document generation tooling in the industry. SKILL.md prompts, Linux container with docx-js/pptxgenjs, visual QA, deep OOXML knowledge. The model writes JavaScript code, the container executes it, out comes a polished document.

This creates **capability lock-in**:
- Quality documents = Claude only ($20-200/mo)
- Data leaves your perimeter (board decks, financial models, credit reports → Anthropic servers)
- No customization of skills, templates, validation
- Every other model + harness = noticeably worse documents

Not because other models are dumb. Because they lack the tooling.

---

## 4. Solution

MINT replicates the full Claude document pipeline as a standalone toolkit for any OpenAI-compatible model.

```
Claude's approach (locked in):
  Model writes JS code → Claude's container executes → file

MINT's approach (open, any model):
  Model writes JS code → MINT runtime executes → validates → QA → file
```

Same architecture. Open. Model-agnostic. Local-first.

---

## 5. Architectural Principles

### #1: Model = swappable part
Single OpenAI-compatible API endpoint. Cloud or local. One line in config.

### #2: Model WILL violate rules
Enforcement in code, not prompts. Prompt = ~85%. Code = 100%.

### #3: GRACE degrades gracefully
No GRACE = normal document. Broken GRACE = warning, not blockage.

### #4: Template-first for weak models
7B models can't write 100 lines of correct JS. They fill templates. MINT compensates.

---

## 6. Architecture

### 6.1 How Claude does it (for reference)

```
Claude model → writes JS/Python code using docx-js/pptxgenjs
  → SKILL.md tells model HOW to write the code
  → Linux container executes code
  → pack.py validates and zips OOXML
  → soffice converts for preview
  → file delivered to user
```

### 6.2 How MINT does it (same approach, open)

```
┌──────────────────────────────────────────────────┐
│                   LibreChat (UI)                  │
│            Multi-model, MCP, Artifacts            │
├──────────────────────────────────────────────────┤
│                                                   │
│  ┌────────────┐  ┌────────────┐                  │
│  │Cloud Models │  │Local Models│                  │
│  │GLM-5.1     │  │Ollama      │                  │
│  │DeepSeek V4 │  │vLLM        │                  │
│  │Qwen 3.6+   │  │llama.cpp   │                  │
│  │Gemini Flash │  │            │                  │
│  └─────┬──────┘  └─────┬──────┘                  │
│        │ OpenAI-compat  │ OpenAI-compat           │
│        └───────┬────────┘                         │
│                ▼                                  │
│  ┌────────────────────────────────────────────┐  │
│  │            MINT Runtime (single service)    │  │
│  │                                             │  │
│  │  1. SKILL LAYER                             │  │
│  │     Skill prompts (per tier)                │  │
│  │     Design tokens (from templates/extract)  │  │
│  │     → Model receives instructions           │  │
│  │                                             │  │
│  │  2. EXECUTION LAYER                         │  │
│  │     Frontier/Medium: model writes code      │  │
│  │       → Node.js runtime executes            │  │
│  │       → docx-js, pptxgenjs, exceljs         │  │
│  │     Small: model fills template             │  │
│  │       → Template engine applies             │  │
│  │                                             │  │
│  │  3. VALIDATION LAYER                        │  │
│  │     Hard rules → reject                     │  │
│  │     Soft rules → auto-fix                   │  │
│  │     StyleFingerprint → drift check          │  │
│  │                                             │  │
│  │  4. QA LAYER                                │  │
│  │     L1: programmatic (instant)              │  │
│  │     L2: render via Gotenberg (async)        │  │
│  │                                             │  │
│  │  5. GRACE LAYER                             │  │
│  │     Metadata injection (Custom XML Parts)   │  │
│  │                                             │  │
│  │  → Output: file + QA report + thumbnails    │  │
│  └────────────────────────────────────────────┘  │
│                       │                           │
│  ┌────────────────────▼───────────────────────┐  │
│  │          Gotenberg (rendering)              │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

**Key difference from v4:** MINT is the entire pipeline – no upstream dvejsada MCP server. One service, not two. Simpler deployment, full control.

### 6.3 Two Execution Modes

| Mode | When | How |
|------|------|-----|
| **Code Generation** | Frontier/Medium models (GLM-5.1, DeepSeek, 70B+) | Model writes JS/Python → MINT Node.js runtime executes → docx-js/pptxgenjs produce file |
| **Template Fill** | Small models (7-14B) | Model provides structured JSON content → MINT template engine fills master template → file |

Both paths converge at validation → QA → GRACE → output.

```
Frontier/Medium:
  Model → writes code → MINT executes → validates → QA → file

Small:
  Model → fills JSON → MINT applies template → validates → QA → file
```

### 6.4 Severity Modes

| Mode | Hard rules | Soft rules | QA |
|------|-----------|------------|-----|
| `audit` | Log | Log | Log |
| `lenient` | Reject | Auto-fix + log | Warn |
| `strict` | Reject | Fix + confirm | Reject on warnings |

### 6.5 Model Tiers

| Tier | Models | Execution mode | Skill style |
|------|--------|---------------|-------------|
| **small** | Qwen2.5-7B, Llama3-8B, Mistral-7B | Template fill | Step-by-step, no code |
| **medium** | Qwen2.5-72B, Llama3-70B, DeepSeek V4 | Code generation | Detailed + examples |
| **frontier** | GLM-5.1, Qwen 3.6+, GPT-5.4 | Code generation | Full design latitude |

---

## 7. Component 1: Skill Prompts

Original prompts (not Anthropic copy) based on public OOXML knowledge.

**For code-generation mode (medium/frontier):**

Tells model how to use docx-js/pptxgenjs correctly:
- Which API calls to use
- DXA units (not percentages)
- Explicit cell widths and margins
- Font and color constraints from design tokens
- Layout patterns and anti-patterns
- Error-prone areas to avoid

**For template mode (small):**

Tells model how to fill structured JSON:
```json
{
  "title": "Q1 2026 Board Report",
  "subtitle": "Key Marketing Activities & Metrics",
  "sections": [
    {
      "heading": "Coverage Overview",
      "content": "The marketing campaign was strategically focused...",
      "bullets": ["Transfers via Astrasend", "Visa Direct", "BNPL"]
    }
  ],
  "tables": [
    {
      "headers": ["Campaign", "January", "February", "March", "Total"],
      "rows": [["App installs", "78 949", "71 806", "82 220", "232 975"]]
    }
  ]
}
```

No code required. Model provides content, MINT handles rendering.

**Files:**
```
skills/
├── docx/
│   ├── create_small.md       # JSON content → template
│   ├── create_medium.md      # code generation with examples
│   └── create_frontier.md    # full creative latitude
├── pptx/
│   ├── create_small.md
│   ├── create_medium.md
│   └── create_frontier.md
└── common/
    ├── design_rules.md
    └── anti_patterns.md
```

---

## 8. Component 2: Execution Runtime

**This is what makes MINT a Claude-replacement, not just a validator.**

### 8.1 Code Execution (Medium/Frontier)

MINT includes Node.js runtime with:
- `docx` (docx-js) – Word document generation
- `pptxgenjs` – PowerPoint generation
- `exceljs` – Excel generation

Model writes code → MINT executes in sandboxed environment → captures output file.

Security: code runs in restricted sandbox (no filesystem access outside output dir, no network, timeout 30 sec).

### 8.2 Template Engine (Small models)

MINT includes template engine:
- Master templates (.pptx, .docx) with named placeholders
- Model provides JSON content structure
- Engine: unpack template → replace content → apply design tokens → repack

No code generation required from model. Works with any model that can produce structured JSON.

### 8.3 Both paths share

- Validation layer (same rules)
- QA pipeline (same checks)
- GRACE injection (same metadata)
- Design tokens compliance (same fingerprint)

---

## 9. Component 3: Templates & Style Extraction

### 9.1 Template Library

```
templates/
├── builtin/                    # ships with MINT
│   ├── board-deck/
│   │   ├── template.pptx
│   │   └── design-tokens.json
│   ├── startup-pitch/
│   ├── business-memo/
│   ├── formal-report/
│   └── prd/
├── extracted/                  # from user documents
│   ├── obank-deck/
│   │   ├── design-tokens.json
│   │   └── user-overrides.json
│   └── obank-memo/
└── custom/                     # user-created
```

### 9.2 Style Extraction

```
mint_extract_style(existing_file.pptx) → design-tokens.json
```

Layer 1 (theme/styles.xml): 100% accurate → fonts, colors, dimensions
Layer 2 (statistical analysis): ~80% accurate → layouts, patterns
User can review and edit extracted tokens.

### 9.3 design-tokens.json

```json
{
  "name": "O!Bank Board Deck",
  "source": "q1_2026_board.pptx",
  "colors": {
    "primary": "#E6007E",
    "secondary": "#222222",
    "accent": "#4CAF50",
    "background": "#FFFFFF",
    "positive": "#4CAF50",
    "negative": "#FF0000"
  },
  "typography": {
    "heading": {"font": "Arial", "weight": "Bold", "size_pt": 36},
    "body": {"font": "Arial", "size_pt": 14, "color": "#555555"}
  },
  "layout": {
    "aspect": "16:9",
    "logo_position": "top-right"
  },
  "detected_layouts": [
    {"name": "title-only", "frequency": 2},
    {"name": "two-column-with-icons", "frequency": 3},
    {"name": "metrics-grid", "frequency": 1}
  ]
}
```

Tokens = input for model (design constraints) + rules for validation (compliance check).

---

## 10. Component 4: Validation Engine

### 10.1 Auto-fix Categories

| Category | Render impact | Action |
|----------|--------------|--------|
| **Safe** | None | Silent fix |
| **Visual** | Minor | Fix + diff in report |
| **Destructive** | May break | REJECT + educational hint |

### 10.2 Fix Loop

Max 3 iterations. Backup before any fix. Reject if cascade detected.

### 10.3 Rules

**DOCX Hard:**

| ID | Rule |
|----|------|
| D-H01 | sum(columnWidths) == table width |
| D-H02 | Every cell: explicit DXA width |
| D-H03 | WidthType = DXA (strict=reject, lenient=warn) |
| D-H04 | Lists: LevelFormat.BULLET, never unicode |
| D-H05 | XML well-formed |
| D-H06 | Page size explicit |
| D-H07 | PageBreak inside Paragraph |
| D-H08 | ImageRun: explicit type |
| D-H09 | No raw `\n` in w:t |
| D-H10 | TOC: HeadingLevel required |

**DOCX Soft:** D-S01..D-S05 (margins, shading, xml:space, outlineLevel)

**PPTX Hard:** P-H01..P-H05 (slide order, fonts, animations, charts, SmartArt)

### 10.4 Educational Reject Hints

```json
{
  "rule": "D-H03",
  "reason": "PERCENTAGE breaks Google Docs rendering",
  "fix_instruction": "Use DXA. Content width = 9360 for US Letter with 1in margins.",
  "learn_more": "https://mint-toolkit.github.io/rules/D-H03"
}
```

### 10.5 StyleFingerprint Hash

```python
hash = sha256(styles.xml + numbering.xml)  # DOCX
hash = sha256(theme1.xml)                   # PPTX
```

Structural drift detection. Does not catch inline formatting (that's QA L2).

---

## 11. Component 5: QA Pipeline

**L1 – Programmatic (<1 sec, sync):** all rules + XML checks + fingerprint comparison.

**L2 – Render-based (3-5 sec, async):** Gotenberg → PDF → PNG → overlap detection (relative: >0.5% = warn, >2% = violation), margin check, font substitution. Confidence scores (0-1).

**L3 – Multimodal (optional):** advisory only, never blocking.

Async delivery: file after L1 → QA report follows after L2.

---

## 12. Component 6: GRACE Protocol

Custom XML Parts (OOXML standard). Namespace: `urn:mint:grace:2026:manifest`. Preserves existing Custom XML from other vendors.

**MVP (G4):** manifest (structure + fingerprint) + instructions (5-10 rules). No graph, contracts, verification.

**Cross-model handoff:** GLM creates → Qwen-7B edits → MINT validates. Rules in file, not in model memory.

**Limitations v0.x:** no macros, no signatures, no protection. Structural drift only.

---

## 13. Tech Stack

| Component | Technology |
|-----------|-----------|
| UI | LibreChat |
| Model API | Any OpenAI-compatible endpoint |
| **MINT Runtime** | **Python (FastMCP) + Node.js (execution)** |
| Document generation | docx-js, pptxgenjs, exceljs (Node.js) |
| Template engine | Python (lxml + zipfile) |
| Validation | Python (lxml, YAML rules) |
| Style extraction | Python (lxml + zipfile) |
| GRACE injection | Python (Custom XML Parts) |
| QA render | Gotenberg (Docker) |
| Local models | Ollama / vLLM / llama.cpp |

### Deployment

```yaml
# docker-compose.yml
services:
  librechat:
    image: ghcr.io/danny-avila/librechat:latest
    ports: ["3080:3080"]

  mint:
    build: ./mint
    depends_on: [gotenberg]
    env_file: .env
    volumes:
      - ./rules:/app/rules
      - ./skills:/app/skills
      - ./templates:/app/templates
      - ./tokens:/app/tokens

  gotenberg:
    image: gotenberg/gotenberg:8

  # Optional: local model
  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
```

**Three containers** (or four with Ollama). No dvejsada. No Claude. No external dependencies beyond model API.

```yaml
# .env – one line to switch model
LLM_BASE_URL=http://localhost:11434/v1   # Ollama local
# LLM_BASE_URL=https://api.z.ai/v1      # GLM cloud
LLM_MODEL=qwen2.5:7b
MINT_MODEL_TIER=small                    # small | medium | frontier
MINT_DEFAULT_MODE=lenient                # audit | lenient | strict
```

RAM: ~4GB without local model. ~8GB with 7B. ~48GB with 70B quantized.

### MCP Tools

| Tool | What | Gate |
|------|------|------|
| `mint_validate` | Check against rules | G1 |
| `mint_fix` | Auto-fix + diff + backup | G1 |
| `mint_fingerprint` | Style drift check | G1 |
| `mint_extract_style` | Design tokens from doc | G2 |
| `mint_list_templates` | Available templates | G2 |
| `mint_create` | Generate (code or template mode) | G2 |
| `mint_qa` | Visual QA | G3 |
| `mint_bootstrap` | GRACE injection | G4 |
| `mint_describe` | Structure summary | G4 |

---

## 14. Development Roadmap: Gates

```
G0 ──→ G1 ──→ G2 ──→ G3 ──→ G4 ──→ G5
Hypo    Valid   Generate  QA    GRACE  Launch
```

**Minimum viable launch = G0 + G1 + G2 + G5.**

---

### G0: Hypothesis Validation
**«Can non-Claude models generate docs through MINT's approach?»**

| Duration | 1 week (10-12 hrs) |
|----------|-------------------|
| Cost | $10 (GLM) or $0 (Ollama only) |

Tasks:
- Deploy MINT skeleton + LibreChat + Ollama (Qwen-7B) + GLM-5.1
- Test: can GLM write valid pptxgenjs/docx-js code? (10 tasks)
- Test: can Qwen-7B fill structured JSON templates? (10 tasks)
- Generate 10 documents, manually inspect for OOXML errors
- Generate same 10 in claude.ai as baseline comparison
- lxml script: check DXA, fonts, XML validity
- Bug catalog CSV with P0/P1/P2 severity

**Kill criteria:**
- GLM-5.1 can't produce valid docx-js/pptxgenjs code in >50% of tasks
- <30% of non-Claude docs have catchable P0/P1 errors
- Ollama 7B can't fill JSON templates reliably

**Pass → G1**

---

### G1: Validation Engine
**«Deterministic quality gate»**

| Duration | 2-3 weeks |
|----------|-----------|

- Rule engine (audit/lenient/strict)
- 10 hard + 5 soft rules from G0 catalog
- Auto-fix: safe/visual/destructive
- Fix loop max 3 + backup
- Educational hints
- StyleFingerprint hash
- Code execution sandbox (Node.js, timeout 30s)
- MCP tools: mint_validate, mint_fix, mint_fingerprint
- Test: 0 false negatives, <5% false positives

**Pass → G2**

---

### G2: Claude-Quality Generation
**«MINT + any model ≈ Claude quality»**

| Duration | 2-3 weeks |
|----------|-----------|

Week 1: Skill prompts
- pptx + docx, 3 tiers each
- Code generation skills (medium/frontier): how to use docx-js/pptxgenjs
- Template fill skills (small): how to produce structured JSON
- Auto-selection by MINT_MODEL_TIER

Week 2: Templates + style extraction
- 3 builtin pptx + 3 docx templates
- mint_extract_style → design-tokens.json
- mint_create: orchestrates skill → model → execute/template → validate → output

Week 3: Quality testing
- Blind A/B: 10 tasks × Claude (baseline) vs GLM+MINT vs 7B+MINT
- Target: GLM ≥85%, 7B ≥70% of Claude quality
- Document remaining gaps

**Core promise fulfilled at this gate.**

**Pass → G3, G4, or straight to G5**

---

### G3: Visual QA (optional for launch)
**1-2 weeks.** Gotenberg render, overlap/margin/font checks, confidence scores, async delivery.

### G4: GRACE (optional for launch)
**2 weeks.** manifest + instructions via Custom XML Parts, cross-model editing test.

### G5: Public Launch
**1 week.** GitHub (mint-toolkit/mint-runtime, mint-toolkit/grace-spec), README, demo video, Habr article, Smithery.ai.

---

## 15. Timeline

| Scenario | G0 | G1 | G2 | G5 | Total to launch |
|----------|----|----|----|----|-----------------|
| Full-time | 1w | 3w | 3w | 1w | **8 weeks** |
| 1-2 hrs/day | 3w | 7w | 7w | 2w | **5 months** |
| AI-assisted | 1w | 2w | 3w | 1w | **7 weeks** |

---

## 16. Cost

### Development
| Item | Monthly |
|------|---------|
| GLM Coding Plan | $10 |
| Ollama | $0 |
| Gemini (free tier for testing) | $0 |
| **Total** | **$10** |

### Production (per user)
| Setup | Monthly | Quality target |
|-------|---------|---------------|
| MINT + Ollama 7B | **$0** | ≥70% Claude |
| MINT + GLM Coding | **$10** | ≥85% Claude |
| Claude Pro (benchmark only) | $20-200 | 100%, locked in |

---

## 17. Business Model

**Open-source (MIT):** MINT runtime, skill prompts, templates, validation, QA, GRACE.

**Premium:** industry skill packs, Brand Book JSON, enterprise audit trail, custom templates, hosted SaaS.

**Moat:** GRACE adoption + accumulated rules + community.

---

## 18. Risks

| Risk | Prob | Mitigation |
|------|------|-----------|
| GLM can't write valid docx-js code | Med | Test in G0. Template fallback for all models |
| 7B models can't fill JSON templates | Low | Test in G0. Simpler template format |
| Code execution sandbox escapes | Low | Restricted: no fs, no network, timeout 30s |
| Validation false positives | Med | Severity modes, start with audit |
| Skills don't close quality gap | Med | Template-first for weak models |
| Anthropic open-sources full pipeline | Low | Different market: local-first, model-agnostic |
| Timeline overrun | High | AI-assisted dev, minimum = G0+G1+G2+G5 |

---

## 19. Pitch

### One line
> Claude-quality documents. Without Claude.

### For developers
MINT replicates Claude's document pipeline as open-source: skill prompts, docx-js/pptxgenjs execution runtime, deterministic validation, visual QA. Any OpenAI-compatible model – cloud or local. docker-compose up, MIT license.

### For enterprise
Board decks, financial models, credit reports – generated on your internal LLM, inside your network. No data leaves your perimeter. MINT validates every document to production standard. $0 with local model.

### For AI community
GRACE makes documents self-describing. Created by GLM, edited by Qwen, updated by local Llama – rules live in the file. First open protocol for agent-navigable office documents.

---

## 20. Key Decisions (v5 changes)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Remove dvejsada dependency** | MINT needs same approach as Claude (code execution), not placeholder replacement |
| 2 | **MINT includes Node.js execution runtime** | docx-js + pptxgenjs + exceljs = Claude's actual toolchain |
| 3 | **Two execution modes** (code gen + template fill) | Frontier models write code, small models fill JSON |
| 4 | **Remove Claude from stack** | Only OpenAI-compatible endpoints. Claude = external benchmark only |
| 5 | **Three containers** (LibreChat + MINT + Gotenberg) | Simpler than four. Full control |
| 6 | **Code sandbox** (no fs, no net, 30s timeout) | Security for executing model-generated code |
| 7 | **G0 tests code generation capability** | New kill criterion: can models write valid docx-js/pptxgenjs? |

---

*MINT v5.0 – Model-Independent Normalization Toolkit*
*Claude-quality documents. Without Claude.*
