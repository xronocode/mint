const pres = new pptxgen();
pres.addSlide().addText("Hello World", { x: 1, y: 1, fontSize: 24 });
const buffer = await pres.write({ outputType: "arraybuffer" });
writeFileSync("output.pptx", Buffer.from(buffer));
