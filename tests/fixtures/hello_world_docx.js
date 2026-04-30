const doc = new Document({
  sections: [{ children: [new Paragraph({ children: [new TextRun("Hello World")] })] }],
});

const buffer = await Packer.toBuffer(doc);
writeFileSync("output.docx", buffer);
