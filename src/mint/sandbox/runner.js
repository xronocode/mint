import { writeFileSync, mkdirSync } from "fs";
import { join } from "path";

const codeFile = process.argv[2];
const outputDir = process.argv[3];

if (!codeFile || !outputDir) {
  process.stderr.write("Usage: runner.js <code-file> <output-dir>\n");
  process.exit(1);
}

mkdirSync(outputDir, { recursive: true });

const { readFileSync } = await import("fs");
let code = readFileSync(codeFile, "utf-8");

code = code.replace(
  /import\s*\{([^}]+)\}\s*from\s*["']sandbox:fs["']\s*;?/g,
  ""
);
code = code.replace(
  /import\s*\{([^}]+)\}\s*from\s*["']docx["']\s*;?/g,
  ""
);
code = code.replace(
  /import\s*\{[^}]*\}\s*from\s*["']pptxgenjs["']\s*;?/g,
  ""
);
code = code.replace(
  /import\s*(\w+)\s*from\s*["']pptxgenjs["']\s*;?/g,
  ""
);

const COLOR_TOKENS = {
  PRIMARY: "1B3A5C",
  ACCENT: "2E75B6",
  SUCCESS: "2E8B57",
  WARNING: "E8A838",
  ERROR: "C0392B",
  TEXT: "333333",
  MUTED: "666666",
  SURFACE: "F0F0F0",
  LIGHT_BLUE: "D5E8F0",
  WHITE: "FFFFFF",
  ALT_ROW: "F0F0F0",
};
for (const [token, hex] of Object.entries(COLOR_TOKENS)) {
  const re = new RegExp(`(['"\`])${token}\\1`, "g");
  code = code.replace(re, `'${hex}'`);
  const reFill = new RegExp(`fill:\\s*['"\`]${token}['"\`]`, "g");
  code = code.replace(reFill, `fill: '${hex}'`);
  const reColor = new RegExp(`color:\\s*['"\`]${token}['"\`]`, "g");
  code = code.replace(reColor, `color: '${hex}'`);
}

const __sandbox_fs__ = {
  writeFileSync: (name, data) => {
    const safeName = String(name).replace(/[^a-zA-Z0-9._-]/g, "_");
    writeFileSync(join(outputDir, safeName), data);
  },
};

const wrappedCode = `
(async () => {
  const __sandbox_fs__ = globalThis.__sandbox_fs__;
  const writeFileSync = __sandbox_fs__.writeFileSync;

  ${code}
})();
`;

globalThis.__sandbox_fs__ = __sandbox_fs__;

const { pathToFileURL } = await import("url");
const codeUrl = pathToFileURL(codeFile).href;

const { runInNewContext } = await import("vm");

const docx = await import("docx");
const pptxgen = (await import("pptxgenjs")).default;

const context = {
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  Promise,
  JSON,
  Math,
  Array,
  Object,
  String,
  Number,
  Boolean,
  Date,
  Error,
  Map,
  Set,
  Buffer,
  Uint8Array,
  ArrayBuffer,
  TextEncoder,
  TextDecoder,
  URL,
  globalThis: {},
  require: undefined,
  process: undefined,
  __dirname: undefined,
  __filename: undefined,
  __sandbox_fs__,
  pptxgen: pptxgen,
  docx: docx,
};

for (const [key, val] of Object.entries(docx)) {
  if (/^[A-Z]/.test(key) || key === "Media") {
    context[key] = val;
  }
}

const HALLUCINATED_STUBS = [
  "LineBreak",
  "Break",
  "PageNumberSeparator",
  "HorizontalRule",
  "HorizontalLine",
  "ListParagraph",
  "Hyperlink",
  "Normal",
  "Heading",
  "Spacing",
  "Indent",
  "Bold",
  "Italic",
  "Underline",
];
for (const stub of HALLUCINATED_STUBS) {
  if (!(stub in context)) {
    context[stub] = class {
      constructor(...args) {
        return new docx.TextRun({ text: "" });
      }
    };
  }
}

context.globalThis = context;

runInNewContext(wrappedCode, context, {
  timeout: 30000,
  filename: "sandbox.js",
});
