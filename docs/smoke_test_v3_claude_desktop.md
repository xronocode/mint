# MINT MCP v0.4.0a7 — Claude Desktop Smoke Test v3
# Target: showcase-level document using MINT tools, testing all major surface areas

Вы —助手 Claude с доступом к MCP-серверу MINT (mint-memo). Ваша задача —
создать документ уровня showcase, последовательно вызвав несколько MINT-инструментов.

Пошагово:

---

## Шаг 1. Проверка версии и телеметрии

Вызовите `mint_version` чтобы убедиться что сервер запущен и версия ≥ 0.4.0.
Затем вызовите `mint_telemetry` чтобы получить текущий снапшот (raw=false).

## Шаг 2. Разведка шаблонов и пресетов

Вызовите `mint_list_templates` — запомните какие doc_type доступны.
Вызовите `mint_list_presets` — запомните какие пресеты доступны.
Вызовите `mint_get_template` с name="report" чтобы увидеть его структуру.

## Шаг 3. Создание отчёта (report)

Вызовите `mint_create_document` со следующими параметрами:

- **doc_type**: `report`
- **intent**: (текст ниже, скопируйте целиком)

```
title: MINT SDK Quarterly Report
author: Quality Engineering Team
date: May 13, 2026
summary: This report summarises MINT Pure Python SDK achievements in Q1 2026: template-driven document generation, 5 design-token presets, validation pipeline with auto-fix, GRACE metadata injection, and full MCP protocol integration. The SDK now covers memos, reports, letters, contracts, technical specs, and bilingual NDAs.
sections: Section 1 covers template architecture — YAML-driven layout declarations with {{ field }} substitution supporting 7 document types without Python changes. Section 2 details the style system: 5 presets (klawd, claret_serif, alga_corporate, minimal, compact) each defining color palette, typography scale, and spacing rules. Section 3 describes the validation pipeline: YAML rules engine, three severity modes (AUDIT, LENIENT, STRICT), and the auto-fix iteration loop with visual and safe tiers. Section 4 presents GRACE metadata: custom XML parts injected into docx carrying audit_id, model_identity, template_version, fingerprint, and fields_heuristic tracking. Section 5 benchmarks MCP integration: 19 registered tools, stdio transport, Claude Desktop smoke-tested with elicitation fallback, resource enumeration, and versioned preset management.
conclusions: MINT v0.4.0 achieves 100% test coverage across all modules, deterministic validation, and model-agnostic document generation via skill prompts. Next milestone: Phase 18 adds chart rendering (bar, line, pie, stacked bar, heatmap, waterfall, gantt) and landscape page layouts for data-heavy reports.
```

ВАЖНО: intent выше содержит все обязательные поля report (title, author, date, summary, sections, conclusions)
в формате `label: value`. MINT должен извлечь их эвристически без дополнительных вопросов.
Если MINT всё же запросит поля через elicit — ответьте значениями из списка выше.

## Шаг 4. Валидация сгенерированного документа

Когда `mint_create_document` вернёт результат, найдите в нём `file://` URI или
путь к .docx файлу. Скопируйте этот путь и вызовите:

`mint_validate_document` с document_path=<путь из шага 3> и severity_mode="lenient"

Ожидаемый результат: passed=true, violations=[].

## Шаг 5. Чтение GRACE-манифеста

Вызовите `mint_read_grace_manifest` с document_path=<путь из шага 3>.

Проверьте что манифест содержит:
- audit_id (UUID)
- template: "report"
- template_version: "1.0"
- fields_elicited: [] (все поля извлечены эвристически)
- fields_heuristic: список извлечённых полей
- model_identity: информация о модели
- fingerprint: SHA-256 хеш

## Шаг 6. Фингерпринт

Вызовите `mint_fingerprint_document` с document_path=<путь из шага 3>.

Запомните возвращённый SHA-256 хеш. Затем вызовите ещё раз с
document_path=<путь из шага 3> и baseline_hash=<хеш из первого вызова>.
Результат должен быть match=true.

## Шаг 7. Extract content

Вызовите `mint_extract_content` с document_path=<путь из шага 3>.

Проверьте что ответ содержит theme (colors, typography) и layouts.

## Шаг 8. Создание memo (тест elicitation fallback)

Вызовите `mint_create_document` с:

- **doc_type**: `memo`
- **intent**: `Memo to Engineering Team about MINT v0.4.0 release on May 13 2026. Body: We are pleased to announce MINT v0.4.0 with 100% test coverage, 19 MCP tools, and showcase-quality document generation.`

Если эвристика не извлечёт sender/recipient, MINT запросит их через elicit.
Ответьте: sender="QE Team", recipient="Engineering Team".

## Шаг 9. Предложение шаблона

Вызовите `mint_suggest_template` с intent="I need a non-disclosure agreement for a contractor in Russia and UK".

Ожидаемый результат: nda-bilingual-ru-en в топ-3.

## Шаг 10. Итоговый отчёт

В конце соберите результаты в таблицу:

| # | Шаг | Инструмент | Статус | Заметки |
|---|-----|-----------|--------|---------|
| 1 | Version check | mint_version | ✅/❌ | версия |
| 2 | Telemetry | mint_telemetry | ✅/❌ | calls count |
| 3 | List templates | mint_list_templates | ✅/❌ | кол-во шаблонов |
| 4 | List presets | mint_list_presets | ✅/❌ | кол-во пресетов |
| 5 | Get template | mint_get_template | ✅/❌ | report layout |
| 6 | Create report | mint_create_document | ✅/❌ | путь к файлу |
| 7 | Validate | mint_validate_document | ✅/❌ | passed? |
| 8 | GRACE manifest | mint_read_grace_manifest | ✅/❌ | audit_id |
| 9 | Fingerprint | mint_fingerprint_document | ✅/❌ | match? |
| 10 | Extract | mint_extract_content | ✅/❌ | theme? |
| 11 | Create memo | mint_create_document | ✅/❌ | путь к файлу |
| 12 | Suggest | mint_suggest_template | ✅/❌ | nda? |

Если любой шаг вернул ошибку — запишите текст ошибки в "Заметки" и продолжайте.
Откройте оба сгенерированных .docx файла и опишите что видите: структуру,
стили, таблицы, шрифты, цвета, отступы. Оцените визуальное качество по шкале 1-5.
