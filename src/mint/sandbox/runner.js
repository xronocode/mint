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
  Document: docx.Document,
  Packer: docx.Packer,
  Paragraph: docx.Paragraph,
  TextRun: docx.TextRun,
  HeadingLevel: docx.HeadingLevel,
  AlignmentType: docx.AlignmentType,
  TableRow: docx.TableRow,
  TableCell: docx.TableCell,
  WidthType: docx.WidthType,
  BorderStyle: docx.BorderStyle,
  Table: docx.Table,
  ImageRun: docx.ImageRun,
  ExternalHyperlink: docx.ExternalHyperlink,
  pptxgen: pptxgen,
  docx: docx,
};
context.globalThis = context;

runInNewContext(wrappedCode, context, {
  timeout: 30000,
  filename: "sandbox.js",
});
