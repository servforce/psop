const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const rootDir = path.resolve(__dirname, "..");
const host = "127.0.0.1";
const port = Number(process.env.PORT || 4173);

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
});
