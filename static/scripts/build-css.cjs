const fs = require("node:fs");
const path = require("node:path");
const postcss = require("postcss");
const loadPostcssConfig = require("../postcss.config.js");

const rootDir = path.resolve(__dirname, "..");
const inputFile = path.join(rootDir, "assets/css/style.css");
const outputFile = path.join(rootDir, "assets/css/style.compiled.css");
const watchMode = process.argv.includes("--watch");

async function compileCss() {
  const input = fs.readFileSync(inputFile, "utf8");
  const plugins = [];

  for (const [name, options] of Object.entries(loadPostcssConfig.plugins)) {
    plugins.push(require(name)(options));
  }

  const result = await postcss(plugins).process(input, {
    from: inputFile,
    to: outputFile
  });

  fs.mkdirSync(path.dirname(outputFile), { recursive: true });
  fs.writeFileSync(outputFile, result.css, "utf8");

  if (result.map) {
    fs.writeFileSync(`${outputFile}.map`, result.map.toString(), "utf8");
  }

  console.log(`[build:css] 已生成 ${path.relative(rootDir, outputFile)}`);
}

compileCss().catch((error) => {
  console.error("[build:css] 构建失败");
  console.error(error);
  process.exit(1);
});

if (watchMode) {
  console.log("[build:css] 进入监听模式");
  fs.watch(path.join(rootDir, "assets"), { recursive: true }, async (_eventType, fileName) => {
    if (!fileName || !fileName.endsWith(".css")) {
      return;
    }

    try {
      await compileCss();
    } catch (error) {
      console.error("[build:css] 监听构建失败");
      console.error(error);
    }
  });
}
