const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const rootDir = path.resolve(__dirname, "..");
const host = process.env.HOST || process.env.PSOP_WEB_HOST || "0.0.0.0";
const port = Number(process.env.PORT || process.env.PSOP_WEB_PORT || 4173);
const apiBaseUrl = process.env.PSOP_WEB_API_BASE_URL || "/api/v1";

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg"
};

function resolveFilePath(requestPath) {
  const normalized = decodeURIComponent((requestPath || "/").split("?")[0]);
  const candidate = normalized === "/" ? "/index.html" : normalized;
  const absolute = path.resolve(rootDir, `.${candidate}`);

  if (!absolute.startsWith(rootDir)) {
    return null;
  }

  if (fs.existsSync(absolute) && fs.statSync(absolute).isFile()) {
    return absolute;
  }

  const hasExtension = path.extname(normalized) !== "";
  if (!hasExtension) {
    return path.join(rootDir, "index.html");
  }

  return null;
}

const server = http.createServer((req, res) => {
  const requestPath = decodeURIComponent((req.url || "/").split("?")[0]);

  if (requestPath === "/assets/js/runtime-config.js") {
    res.setHeader("Content-Type", "text/javascript; charset=utf-8");
    res.end(`window.__PSOP_API_BASE_URL = ${JSON.stringify(apiBaseUrl)};\n`);
    return;
  }

  const filePath = resolveFilePath(req.url || "/");

  if (!filePath) {
    res.statusCode = 404;
    res.end("Not Found");
    return;
  }

  const ext = path.extname(filePath).toLowerCase();
  res.setHeader("Content-Type", mimeTypes[ext] || "application/octet-stream");
  fs.createReadStream(filePath).pipe(res);
});

server.listen(port, host, () => {
  console.log(`[dev] static scaffold available at http://${host}:${port}`);
  console.log(`[dev] API base URL injected as ${apiBaseUrl}`);
});
