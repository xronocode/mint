# DOCX Generation Capabilities Showcase & Design System Guide

> **Версия 2.0** · Май 2026 · Сгенерировано Claude (Anthropic)
>
> Этот документ — полный справочник по возможностям генерации .docx через Claude с использованием библиотеки docx-js. Каждая секция построена по принципу **Результат → Как сделано → Антипаттерн**.

---

## Содержание

1. [Design System](#1-design-system)
   - [Color Palette](#11-color-palette)
   - [Typography Scale](#12-typography-scale)
   - [Spacing & Rhythm](#13-spacing--rhythm)
2. [Typography & Character Formatting](#2-typography--character-formatting)
   - [Character Styles](#21-character-styles)
   - [Font Mixing](#22-font-mixing)
   - [Paragraph Alignment & Indentation](#23-paragraph-alignment--indentation)
3. [Lists](#3-lists)
4. [Tables](#4-tables)
   - [Basic Table with Header](#41-basic-table-with-header)
   - [Table with Merged Cells](#42-table-with-merged-cells)
5. [Images & Charts](#5-images--charts)
6. [Callout Components](#6-callout-components)
7. [Hyperlinks, Bookmarks & Footnotes](#7-hyperlinks-bookmarks--footnotes)
8. [Page Layout & Sections](#8-page-layout--sections)
   - [Landscape Orientation](#81-landscape-orientation)
   - [Multi-Column Layout](#82-multi-column-layout)
9. [Headers & Footers](#9-headers--footers)
10. [Appendix: API Quick Reference](#10-appendix-api-quick-reference)

---

## 1. Design System

Все генерируемые документы используют единую систему дизайн-токенов. Это обеспечивает визуальную консистентность и упрощает поддержку.

### 1.1 Color Palette

Палитра построена вокруг профессионального синего primary-цвета с семантическими цветами для статусных индикаторов:

| Token | Hex | Назначение |
|-------|-----|-----------|
| Primary Blue | `#1B3A5C` | Заголовки H1, header таблиц, обложка |
| Accent Blue | `#2E75B6` | Заголовки H2, callout-рамки, ссылки |
| Light Blue | `#D5E8F0` | Разделители, декоративные элементы |
| Success Green | `#2E8B57` | Статус ✓, позитивные индикаторы |
| Warning Amber | `#E8A838` | Warning callouts, ∼ статус |
| Error Red | `#C0392B` | Статус ✗, ошибки |
| Dark Gray | `#333333` | Основной текст |
| Medium Gray | `#666666` | Вторичный текст, подписи |
| Light Gray | `#F0F0F0` | Чередование строк таблиц |
| White | `#FFFFFF` | Фон, текст на тёмном фоне |

**Дополнительные фоновые цвета:**

| Token | Hex | Назначение |
|-------|-----|-----------|
| Callout BG | `#EBF5FB` | Фон info-callout |
| Warning BG | `#FFF8E1` | Фон warning-callout |
| Code BG | `#F5F5F5` | Фон code-блоков |

> **🔧 Как реализовано:** Цвета определены как объект-константа `COLOR` в коде. Все компоненты ссылаются на токены — никогда на хардкод hex-значения. Изображение палитры сгенерировано через matplotlib и вставлено как `ImageRun`.

> **⚠️ Антипаттерн:** Не хардкодить цвета в каждом элементе. При смене палитры придётся менять сотни мест. Всегда используйте централизованные токены.

---

### 1.2 Typography Scale

Шрифтовая иерархия использует Arial как универсальную базу для кросс-платформенной совместимости (Word, LibreOffice, Google Docs). Код использует Courier New.

| Style | Font | Size | Weight | Использование |
|-------|------|------|--------|--------------|
| Heading 1 | Arial | 16pt (32 half-pt) | Bold | Секции документа |
| Heading 2 | Arial | 14pt (28 half-pt) | Bold | Подсекции |
| Heading 3 | Arial | 12pt (24 half-pt) | Bold | Подподсекции |
| Body | Arial | 11pt (22 half-pt) | Regular | Основной текст |
| Caption | Arial | 9pt (18 half-pt) | Italic | Подписи к рисункам и таблицам |
| Code | Courier New | 9pt (18 half-pt) | Regular | API reference, код |

> **🔧 Как реализовано:**
>
> ```javascript
> styles: {
>   default: { document: { run: { font: "Arial", size: 24 } } },
>   paragraphStyles: [
>     { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal",
>       run: { size: 32, bold: true, font: "Arial", color: "1B3A5C" },
>       paragraph: { spacing: { before: 360, after: 240 }, outlineLevel: 0 } },
>     // ... аналогично для Heading2, Heading3
>   ]
> }
> ```
>
> Критично: используйте точные built-in ID (`"Heading1"`, `"Heading2"`) и `outlineLevel` для поддержки TOC.

> **⚠️ Антипаттерн:** Никогда не используйте кастомные ID стилей (например `"MyHeading1"`) — TOC-генерация требует стандартных ID. Также не забывайте `outlineLevel` — без него заголовки не попадут в оглавление.

---

### 1.3 Spacing & Rhythm

Единицы измерения: **DXA** (twentieths of a point). 1440 DXA = 1 дюйм = 72pt. 20 DXA = 1pt.

| Элемент | Before (DXA) | After (DXA) | Эквивалент |
|---------|-------------|------------|------------|
| Heading 1 | 360 | 240 | 18pt / 12pt |
| Heading 2 | 240 | 180 | 12pt / 9pt |
| Heading 3 | 180 | 120 | 9pt / 6pt |
| Body text | — | 120 | — / 6pt |
| Callout box | 200 | 200 | 10pt / 10pt |
| Spacer (default) | — | 120 | — / 6pt |
| Section gap | — | 400 | — / 20pt |

> **🔧 Как реализовано:** Spacing задаётся через `paragraph.spacing: { before, after }`. Функция-хелпер `spacer(pts)` создаёт пустой параграф с настраиваемым `spacing.after`.

---

## 2. Typography & Character Formatting

### 2.1 Character Styles

Все доступные символьные форматирования через `TextRun`:

| Свойство | API-параметр | Пример |
|----------|-------------|--------|
| **Жирный** | `bold: true` | `new TextRun({ text: "Bold", bold: true })` |
| *Курсив* | `italics: true` | `new TextRun({ text: "Italic", italics: true })` |
| Подчёркнутый | `underline: {}` | `new TextRun({ text: "Under", underline: {} })` |
| ~~Зачёркнутый~~ | `strike: true` | `new TextRun({ text: "Strike", strike: true })` |
| Выделение | `highlight: "yellow"` | `new TextRun({ text: "Highlighted", highlight: "yellow" })` |
| Цвет | `color: "C0392B"` | `new TextRun({ text: "Red", color: "C0392B" })` |
| КАПС | `allCaps: true` | `new TextRun({ text: "caps", allCaps: true })` |
| Капитель | `smallCaps: true` | `new TextRun({ text: "small caps", smallCaps: true })` |
| Верхний индекс | `superScript: true` | E=mc² → `{ text: "2", superScript: true }` |
| Нижний индекс | `subScript: true` | H₂O → `{ text: "2", subScript: true }` |

Все свойства комбинируемы: `{ bold: true, italics: true, color: "2E75B6" }`.

### 2.2 Font Mixing

Каждый `TextRun` может задавать свой шрифт через `font: "FontName"`. Для кросс-платформенной безопасности рекомендуемые шрифты:

- **Arial** — основной sans-serif, установлен везде
- **Times New Roman** — основной serif
- **Georgia** — элегантный serif
- **Courier New** — monospace для кода
- **Verdana** — широкий sans-serif для экрана

### 2.3 Paragraph Alignment & Indentation

| Выравнивание | API | Использование |
|-------------|-----|--------------|
| По левому краю | `AlignmentType.LEFT` | Основной текст (по умолчанию) |
| По центру | `AlignmentType.CENTER` | Заголовки, подписи, изображения |
| По правому краю | `AlignmentType.RIGHT` | Даты, подписи |
| По ширине | `AlignmentType.JUSTIFIED` | Формальные документы |

**Отступы:**

```javascript
// Красная строка
indent: { firstLine: 720 }     // 0.5 дюйма

// Блочный отступ
indent: { left: 720 }          // Весь абзац сдвинут

// Hanging indent (для списков)
indent: { left: 720, hanging: 360 }

// Правый отступ
indent: { right: 720 }
```

---

## 3. Lists

### Правильная реализация списков

Списки **обязательно** используют `numbering.config` в конструкторе `Document`. Никогда не вставляйте символы буллетов вручную.

**Конфигурация:**

```javascript
const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "•",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
          { level: 1, format: LevelFormat.BULLET, text: "◦",
            style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
          { level: 2, format: LevelFormat.BULLET, text: "▪",
            style: { paragraph: { indent: { left: 2160, hanging: 360 } } } },
        ],
      },
      {
        reference: "numbers",
        levels: [
          { level: 0, format: LevelFormat.DECIMAL, text: "%1.", ... },
          { level: 1, format: LevelFormat.LOWER_LETTER, text: "%2)", ... },
          { level: 2, format: LevelFormat.LOWER_ROMAN, text: "%3.", ... },
        ],
      },
    ]
  }
});
```

**Использование:**

```javascript
new Paragraph({
  numbering: { reference: "bullets", level: 0 },
  children: [new TextRun("Item text")]
})
```

**Важные правила:**

- Одна `reference` = одна непрерывная нумерация (1, 2, 3 → 4, 5, 6)
- Разные `reference` = независимая нумерация (1, 2, 3 → 1, 2, 3)
- `level` определяет глубину вложенности (0, 1, 2)

> **⚠️ Антипаттерн:** `new TextRun("• Item")` или `new TextRun("\u2022 Item")` — ломает accessibility, нарушает структуру списка в Word, не позволяет Word распознать и продолжить нумерацию.

---

## 4. Tables

### 4.1 Basic Table with Header

Таблицы — самый сложный элемент docx. Требуют **двойного указания ширины** для кросс-платформенной совместимости.

```javascript
new Table({
  width: { size: 9360, type: WidthType.DXA },       // Ширина таблицы
  columnWidths: [4680, 4680],                         // Сумма = ширина таблицы
  rows: [
    new TableRow({
      children: [
        new TableCell({
          width: { size: 4680, type: WidthType.DXA }, // = columnWidths[0]
          borders: { top: border, bottom: border, left: border, right: border },
          shading: { fill: "D5E8F0", type: ShadingType.CLEAR },
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({ children: [new TextRun("Cell")] })]
        })
      ]
    })
  ]
})
```

**Критические правила:**

1. **Всегда `WidthType.DXA`** — `WidthType.PERCENTAGE` ломается в Google Docs
2. **Двойная ширина** — `columnWidths` НА таблице + `width` НА каждой ячейке
3. **Сумма `columnWidths`** должна точно равняться `width.size` таблицы
4. **`ShadingType.CLEAR`** — никогда не SOLID (создаёт чёрный фон)
5. **Cell margins** — обязательны для читаемости: `{ top: 80, bottom: 80, left: 120, right: 120 }`

**Расчёт ширины:**

```
Ширина контента = Ширина страницы - Левый margin - Правый margin
US Letter:  12240 - 1440 - 1440 = 9360 DXA
A4:         11906 - 1440 - 1440 = 9026 DXA
Landscape:  15840 - 1440 - 1440 = 12960 DXA
```

### 4.2 Table with Merged Cells

Объединение ячеек через `columnSpan`:

```javascript
new TableCell({
  columnSpan: 3,                              // Объединяет 3 колонки
  width: { size: 9360, type: WidthType.DXA }, // = сумма 3 колонок
  // ...
})
```

В строке с объединённой ячейкой присутствует **только одна** ячейка вместо трёх.

**Чередование строк (zebra striping):**

```javascript
rows.map((row, i) => new TableRow({
  children: cells.map(cell => 
    bodyCell(cell.text, cell.width, {
      shade: i % 2 ? "F0F0F0" : undefined  // Серый фон через строку
    })
  )
}))
```

---

## 5. Images & Charts

### Встраивание изображений

```javascript
new Paragraph({
  alignment: AlignmentType.CENTER,
  children: [new ImageRun({
    type: "png",                                    // ОБЯЗАТЕЛЬНО: png, jpg, gif, bmp, svg
    data: fs.readFileSync("chart.png"),              // Buffer
    transformation: { width: 450, height: 260 },    // Пиксели
    altText: {                                       // ОБЯЗАТЕЛЬНО все 3 поля
      title: "Chart title",
      description: "Chart description",
      name: "chart"
    }
  })]
})
```

### Pipeline генерации графиков

1. Python (matplotlib) → PNG-файл с нужным DPI и размером
2. Node.js (docx-js) → `fs.readFileSync()` → `ImageRun` → вставка в документ

**Типы графиков, продемонстрированные в документе:**

| Тип | Библиотека | Ключевые параметры |
|-----|-----------|-------------------|
| Horizontal bar | matplotlib | `ax.barh()`, custom colors per bar |
| Dual-axis line | matplotlib | `ax.twinx()`, two Y-axes |
| Pie chart | matplotlib | `ax.pie()`, custom color palette |
| Color swatches | matplotlib + patches | `FancyBboxPatch`, transparent background |

### Инлайн-иконки

Маленькие изображения (16×16 px) можно размещать в одном `Paragraph` рядом с `TextRun`:

```javascript
new Paragraph({
  children: [
    new ImageRun({ type: "png", data: iconBuffer,
      transformation: { width: 16, height: 16 }, ... }),
    new TextRun({ text: " Label text" }),
  ]
})
```

> **⚠️ Антипаттерн:** Не пропускайте `type` на `ImageRun` — генерирует невалидный XML. Не пропускайте `altText` — нарушает accessibility. Избегайте изображений >2000px — раздувают размер файла.

---

## 6. Callout Components

### Info Callout (синий)

Визуально: синяя полоса слева (12pt), светло-голубой фон, опциональная иконка.

```javascript
new Paragraph({
  border: { left: { style: BorderStyle.SINGLE, size: 12, color: "2E75B6", space: 8 } },
  shading: { type: ShadingType.CLEAR, fill: "EBF5FB" },
  children: [
    new ImageRun({ /* info icon 18x18 */ }),
    new TextRun({ text: "  Title", bold: true, color: "2E75B6" }),
  ],
})
```

### Warning Callout (янтарный)

Та же структура, но с `color: "E8A838"` и `fill: "FFF8E1"`.

### Code Block

Courier New на сером фоне с тонкой рамкой:

```javascript
new Paragraph({
  shading: { type: ShadingType.CLEAR, fill: "F5F5F5" },
  border: { top: border("DDDDDD"), bottom: border("DDDDDD"),
            left: border("DDDDDD"), right: border("DDDDDD") },
  children: [new TextRun({ text: code, font: "Courier New", size: 18 })],
})
```

> **⚠️ Антипаттерн:** Никогда не используйте `\n` для многострочного кода. Каждая строка — отдельный `Paragraph` с тем же стилем.

---

## 7. Hyperlinks, Bookmarks & Footnotes

### External Hyperlinks

```javascript
new ExternalHyperlink({
  children: [new TextRun({ text: "Link text", style: "Hyperlink" })],
  link: "https://example.com",
})
```

### Internal Bookmarks

Двухшаговый процесс:

```javascript
// 1. Создать закладку в месте назначения
new Bookmark({ id: "section_id", children: [new TextRun("Section Title")] })

// 2. Ссылка на неё
new InternalHyperlink({
  children: [new TextRun({ text: "Go to Section", style: "Hyperlink" })],
  anchor: "section_id",
})
```

### Footnotes

Определение в конструкторе `Document`, ссылка через `FootnoteReferenceRun`:

```javascript
const doc = new Document({
  footnotes: {
    1: { children: [new Paragraph("Footnote text")] },
  },
  sections: [{
    children: [new Paragraph({
      children: [
        new TextRun("Text with footnote"),
        new FootnoteReferenceRun(1),
      ],
    })]
  }]
});
```

### Tab Stops

Правое выравнивание на одной строке:

```javascript
new Paragraph({
  tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
  children: [
    new TextRun("Left text"),
    new TextRun("\tRight text"),
  ],
})
```

Dot leader (стиль оглавления):

```javascript
new PositionalTab({
  alignment: PositionalTabAlignment.RIGHT,
  relativeTo: PositionalTabRelativeTo.MARGIN,
  leader: PositionalTabLeader.DOT,
})
```

---

## 8. Page Layout & Sections

### Размеры страниц

| Бумага | Width (DXA) | Height (DXA) | Content Width (1" margins) |
|--------|-------------|-------------|---------------------------|
| US Letter | 12,240 | 15,840 | 9,360 |
| A4 (default) | 11,906 | 16,838 | 9,026 |
| US Letter Landscape | 15,840 | 12,240 | 12,960 |

**Единицы:** 1440 DXA = 1 inch = 72pt = 2.54cm

### 8.1 Landscape Orientation

```javascript
{
  properties: {
    page: {
      size: {
        width: 12240,    // Передавайте ПОРТРЕТНЫЕ размеры
        height: 15840,   // docx-js переворачивает их сам
        orientation: PageOrientation.LANDSCAPE,
      },
    },
    type: SectionType.NEXT_PAGE,  // Разрыв секции
  },
}
```

> **⚠️ Антипаттерн:** Не передавайте уже перевёрнутые размеры (15840 × 12240) — docx-js сделает двойную инверсию и вы получите портретную ориентацию.

### 8.2 Multi-Column Layout

```javascript
{
  properties: {
    column: {
      count: 2,           // Количество колонок
      space: 720,          // Зазор между колонками (0.5 inch)
      equalWidth: true,    // Равная ширина
      separate: true,      // Вертикальная разделительная линия
    },
  },
}
```

Неравные колонки (sidebar):

```javascript
column: {
  equalWidth: false,
  children: [
    new Column({ width: 5400, space: 720 }),  // Основная
    new Column({ width: 3240 }),               // Sidebar
  ],
}
```

Перенос в следующую колонку: `SectionType.NEXT_COLUMN`.

---

## 9. Headers & Footers

```javascript
headers: {
  default: new Header({
    children: [new Paragraph({
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "2E75B6", space: 4 } },
      children: [
        new TextRun({ text: "Document Title", bold: true }),
        // Tab stop для правого выравнивания
        new TextRun({ children: [
          new PositionalTab({
            alignment: PositionalTabAlignment.RIGHT,
            relativeTo: PositionalTabRelativeTo.MARGIN,
            leader: PositionalTabLeader.NONE,
          }),
          "Subtitle",
        ]}),
      ],
    })],
  }),
},
footers: {
  default: new Footer({
    children: [new Paragraph({
      children: [
        new TextRun("Company Name"),
        new TextRun({ children: [
          new PositionalTab({ ... }),  // Tab для правого выравнивания
        ]}),
        new TextRun("Page "),
        new TextRun({ children: [PageNumber.CURRENT] }),
        new TextRun(" of "),
        new TextRun({ children: [PageNumber.TOTAL_PAGES] }),
      ],
    })],
  }),
}
```

> **⚠️ Антипаттерн:** Никогда не используйте таблицы для двухколоночных headers/footers — ячейки имеют минимальную высоту и рендерятся как пустые боксы. Используйте tab stops.

---

## 10. Appendix: API Quick Reference

| Feature | API | Notes |
|---------|-----|-------|
| Bold | `TextRun({ bold: true })` | Character-level |
| Italic | `TextRun({ italics: true })` | Character-level |
| Color | `TextRun({ color: "2E75B6" })` | 6-char hex, без `#` |
| Font | `TextRun({ font: "Arial" })` | Должен быть на системе |
| Size | `TextRun({ size: 24 })` | Half-points: 24 = 12pt |
| Heading | `heading: HeadingLevel.HEADING_1` | Требует style ID |
| Alignment | `alignment: AlignmentType.CENTER` | Paragraph-level |
| Indent | `indent: { firstLine: 720 }` | DXA; also left, right |
| Spacing | `spacing: { before: 240, after: 120 }` | DXA (20 DXA = 1pt) |
| Bullet list | `numbering: { reference, level }` | Требует numbering config |
| Table | `new Table({ width, columnWidths })` | DXA; dual width required |
| Cell shade | `shading: { fill, type: CLEAR }` | Только CLEAR |
| Cell merge | `columnSpan: n` | Width = сумма колонок |
| Image | `ImageRun({ type, data, ... })` | type обязателен |
| Page break | `new PageBreak()` | Внутри Paragraph |
| Section | `SectionType.NEXT_PAGE` | Смена layout |
| Landscape | `PageOrientation.LANDSCAPE` | Передавать портрет |
| Columns | `column: { count: 2 }` | Section-level |
| Header | `headers: { default: Header }` | Per-section |
| Footer | `footers: { default: Footer }` | PageNumber.CURRENT |
| TOC | `new TableOfContents(...)` | HeadingLevel styles |
| Hyperlink | `ExternalHyperlink({ link })` | style: "Hyperlink" |
| Bookmark | `new Bookmark({ id, children })` | Internal navigation |
| Footnote | `FootnoteReferenceRun(id)` | Document constructor |
| Tab stop | `tabStops: [{ type, position }]` | RIGHT + MAX |
| Dot leader | `PositionalTabLeader.DOT` | TOC-style |
| Border | `border: { bottom: { style, size } }` | Paragraph or cell |
| Shading | `shading: { fill, type: CLEAR }` | Background fill |

---

## Структура проекта

```
generate_showcase.js          ← Основной скрипт генерации
assets/
├── logo.png                  ← Логотип-placeholder (400×100, PIL)
├── palette.png               ← Цветовая палитра (matplotlib)
├── bar_chart.png             ← Горизонтальная диаграмма
├── line_chart.png            ← Линейный график с двумя осями
├── pie_chart.png             ← Круговая диаграмма
├── info_icon.png             ← Иконка info (60×60, PIL)
└── warn_icon.png             ← Иконка warning (60×60, PIL)
```

## Pipeline

```
1. Python (matplotlib + PIL) → генерация PNG-ассетов
2. Node.js (docx-js)         → сборка документа из секций
3. validate.py               → валидация XML-структуры
4. Выход: .docx + .md companion
```

---

*Сгенерировано Claude · Anthropic · Май 2026*
