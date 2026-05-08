# MINT Pure Python Edition — Handover Document для агента

**Версия:** v1.0 (9 мая 2026)  
**Автор:** Grok (на основе детального обсуждения с владельцем проекта)  
**Цель:** Перевести MINT на полностью pure Python execution layer без потери качества текущих документов (включая showcase.docx).

## 1. Executive Summary

Мы переходим от текущей архитектуры (Python + Node.js sandbox + docx-js/pptxgenjs + Gotenberg) на **чистый Python**.

**Ключевые требования:**
- Качество документов ≥ текущего уровня (особенно showcase.docx).
- Полная поддержка всех трёх tier (Small / Medium / Frontier).
- 100% сохранение и улучшение GRACE.
- Упрощение установки до `pip install mint`.
- JS-сandbox остаётся как optional fallback.
- MCP Tools переименовываются и документируются.

## 2. Архитектура (High-Level)

```mermaid
graph TD
    A[LLM Response] --> B[Execution Engine]
    B --> C[Small Tier: Jinja2 + PostProcessor]
    B --> D[Medium Tier: mint.sdk (Guided)]
    B --> E[Frontier Tier: RestrictedPython Sandbox]
    C & D & E --> F[mint.document Core]
    F --> G[Validation + Fix Engine]
    G --> H[Style + Branding Engine]
    H --> I[GRACE Injector v1.1]
    I --> J[Output .docx/.pptx + PDF]
```

## 3. mint.sdk — High-Level API (v2.1)

### Основные классы

```python
from mint import Document, Section, Table, Chart, Style, Image, TOC
from pathlib import Path

doc = Document(
    format="docx",
    title="Alga Group × Yandex Partnership",
    style_preset="alga_corporate"
)

doc.add_cover(title=..., subtitle=...)

# Sections
doc.add_section(Section("Executive Summary", level=1)
                .add_paragraph(text)
                .add_table(table)
                .add_chart(chart))

doc.add_toc(max_level=3)
doc.inject_grace(level=GraceLevel.FULL)
doc.validate(level="strict")
doc.fix(strategy="visual_first")
doc.save("outputs/memo.docx")
```

**Полный список ключевых классов и методов** (см. раздел 3.1–3.6 ниже).

#### 3.1 Document
- `with_style_preset(name)`, `with_style_from(path)`
- `add_cover()`, `add_section()`, `add_toc()`
- `set_header()`, `set_footer()`
- `inject_grace()`, `validate()`, `fix()`, `save()`, `to_pdf()`

#### 3.2 Section
- Fluent: `.add_paragraph()`, `.add_table()`, `.add_chart()`, `.add_image()`

#### 3.3 Table
- `Table.from_list()`, `Table.from_markdown()`, `Table.from_list_of_dicts()`
- `Table.financial()`, `Table.comparison()`
- `apply_style()`, `set_column_widths()`, `autofit`

#### 3.4 Chart (особо важно — showcase)
- `Chart.bar()`, `Chart.line()`, `Chart.stacked_bar()`, `Chart.pie()`, `Chart.heatmap()`, `Chart.waterfall()`, `Chart.gantt()`
- `Chart.from_matplotlib(fig, caption=..., width=..., vector=True)`
- `Chart.from_seaborn()`, `Chart.from_plotly()`
- Автоматическое применение корпоративных цветов, шрифтов, удаление chartjunk.

#### 3.5 Style
- `Style.heading1()`, `Style.body()`, `Style.table_header()` и т.д.
- Поддержка `Pt()`, цветов Alga/Weiser, spacing, keep_with_next.

#### 3.6 Дополнительно
- `Image`, `Callout`, `FinancialTable`, `GraceLevel`

## 4. Execution Layer по Tier’ам

- **Small**: Jinja2 templates + powerful PostProcessor
- **Medium**: LLM пишет код с использованием mint.sdk (рекомендуемый)
- **Frontier**: Полный Python-код в `RestrictedPython` sandbox (whitelist: python-docx, lxml, matplotlib, seaborn, pandas, openpyxl, pillow и т.д.)

## 5. Структура проекта (предлагаемая)

```
src/mint/
├── core/                  # Document, Section, Table, Chart и т.д.
├── sdk/                   # Высокоуровневый API
├── execution/
│   ├── engine.py
│   ├── small.py
│   ├── medium.py
│   └── frontier.py        # RestrictedExecutor
├── rules/                 # YAML rules
├── grace/                 # GRACE v1.1
├── mcp/
│   ├── core.py
│   └── generation.py
├── cli.py
└── ...
```

`src/mint_python/` — параллельная реализация на первое время (с feature flag).

## 6. Поэтапный план реализации

**Phase 0** — Подготовка (структура, pyproject.toml)  
**Phase 1** — Core Document + SDK (Document, Section, Table, Style)  
**Phase 2** — Chart + matplotlib интеграция (критично)  
**Phase 3** — Validation + Fix + PostProcessor  
**Phase 4** — Execution tiers + sandbox  
**Phase 5** — MCP, CLI, GRACE, тесты, демо

## 7. Acceptance Criteria

- На 20+ тестовых кейсах (включая showcase.docx) качество ≥ текущей JS-версии.
- `mint create ... --engine=python` работает.
- Все существующие тесты зелёные.
- Установка: `pip install -e .` + одна команда.
- GRACE полностью сохраняется и читается.

## 8. Риски и Mitigation

- Sandbox безопасность → RestrictedPython + whitelist.
- Качество сложных чартов → тщательное тестирование from_matplotlib.
- Backward compatibility → optional JS engine.
