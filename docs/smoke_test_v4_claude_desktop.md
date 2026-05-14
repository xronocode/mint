# MINT MCP v0.4.0a7 — Claude Desktop Smoke Test v4
# Validated locally: 10/10 passed. Testing report parser fix + full pipeline.

Вы — Claude с доступом к MCP-серверу MINT (mint-memo). Ваша задача —
последовательно вызвать MINT-инструменты, проверить все major surface areas,
и собрать итоговую таблицу.

Пошагово:

---

## Шаг 1. Проверка версии

Вызовите `mint_version`. Проверьте что версия ≥ 0.4.0.

## Шаг 2. Разведка

Вызовите `mint_list_templates`. Запомните сколько шаблонов.
Вызовите `mint_list_presets`. Запомните сколько пресетов.
Вызовите `mint_get_template` с name="report". Посмотрите required_fields.

## Шаг 3. Создание report (КРИТИЧЕСКИЙ ТЕСТ)

Вызовите `mint_create_document` с параметрами:

- **doc_type**: `report`
- **intent**: (скопируйте целиком блок ниже)

```
title: MINT SDK Quarterly Report
author: Quality Engineering Team
date: May 15, 2026
summary: This report summarises MINT Pure Python SDK achievements in Q1 2026: template-driven document generation, 5 design-token presets, validation pipeline with auto-fix, GRACE metadata injection, and full MCP protocol integration.
sections: Section 1 covers template architecture — YAML-driven layout declarations supporting 7 document types without Python changes. Section 2 details the style system: 5 presets each defining color palette, typography scale, and spacing rules. Section 3 describes the validation pipeline: YAML rules engine, three severity modes, and the auto-fix iteration loop. Section 4 presents GRACE metadata: custom XML parts injected into docx carrying audit_id, template_version, fingerprint, and fields_heuristic tracking. Section 5 benchmarks MCP integration: 19 registered tools, stdio transport, and versioned preset management.
conclusions: MINT v0.4.0 achieves 100% test coverage across all modules, deterministic validation, and model-agnostic document generation via skill prompts.
```

Ожидание: MINT извлечёт все 6 полей (title, author, date, summary, sections, conclusions)
эвристически без elicitation. Статус ответа: complete. Если MINT запросит поля —
ответьте значениями из блока выше.

## Шаг 4. Валидация

Найдите path к .docx в результате шага 3. Вызовите:
`mint_validate_document(document_path=<path>, severity_mode="lenient")`

Ожидание: passed=true. Допускается 1 soft violation (D-S05 — page setup).

## Шаг 5. GRACE manifest

Вызовите `mint_read_grace_manifest(document_path=<path>)`.

Проверьте: audit_id (UUID), template="report.yaml", template_version="1.0".
fields_heuristic должен содержать извлечённые поля.
model_identity может быть null (by design — MCP сервер не знает какая модель его вызвала).

## Шаг 6. Fingerprint

Вызовите `mint_fingerprint_document(document_path=<path>)`.
Запомните hash. Вызовите ещё раз с baseline_hash=<hash>.
Ожидание: drift_status="match" или "drift" (drift возможен если GRACE re-injects).

## Шаг 7. Extract content

Вызовите `mint_extract_content(document_path=<path>)`.
Ожидание: ответ содержит theme и layouts.

## Шаг 8. Создание memo (тест эвристики + elicitation fallback)

Вызовите `mint_create_document` с:

- **doc_type**: `memo`
- **intent**: `sender: QE Team, recipient: Engineering Team, date: May 15, 2026, subject: MINT v0.4.0 Release, body: We are pleased to announce MINT v0.4.0 with 100% test coverage, 19 MCP tools, and showcase-quality document generation.`

Ожидание: все 5 полей извлечены эвристически, статус complete.
Если MINT запросит недостающие поля через elicit — ответьте из списка выше.

## Шаг 9. Suggest template

Вызовите `mint_suggest_template` с intent="I need a non-disclosure agreement for a contractor in Russia and UK".

Ожидание: nda-bilingual-ru-en в результатах (может быть не на первом месте).

## Шаг 10. Итоговая таблица

Соберите результаты:

| # | Шаг | Инструмент | Статус | Заметки |
|---|-----|-----------|--------|---------|
| 1 | Version | mint_version | ✅/❌ | версия |
| 2 | List templates | mint_list_templates | ✅/❌ | кол-во |
| 3 | List presets | mint_list_presets | ✅/❌ | кол-во |
| 4 | Get template | mint_get_template | ✅/❌ | required_fields |
| 5 | Create report | mint_create_document | ✅/❌ | path, heuristic fields |
| 6 | Validate | mint_validate_document | ✅/❌ | passed?, violations |
| 7 | GRACE manifest | mint_read_grace_manifest | ✅/❌ | audit_id, template |
| 8 | Fingerprint | mint_fingerprint_document | ✅/❌ | drift_status |
| 9 | Extract | mint_extract_content | ✅/❌ | theme?, layouts? |
| 10 | Create memo | mint_create_document | ✅/❌ | path, heuristic fields |
| 11 | Suggest | mint_suggest_template | ✅/❌ | nda в результатах? |

Если любой шаг вернул ошибку — запишите текст ошибки в "Заметки" и продолжайте.

Откройте оба .docx файла (report и memo). Опишите визуально:
структуру, стили, шрифты, цвета, отступы, таблицы.
Оцените визуальное качество каждого по шкале 1-5.
