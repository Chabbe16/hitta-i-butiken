const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = process.cwd();
const PORT = Number(process.env.PORT || 5173);
const DEFAULT_PLANOGRAM_ROOT = process.env.USERPROFILE
  ? path.join(process.env.USERPROFILE, "Documents", "HITTA_I_BUTIKEN", "PDF-EXCEL", "PLANOGRAM")
  : "";
const PLANOGRAM_ROOT = process.env.PLANOGRAM_ROOT || (DEFAULT_PLANOGRAM_ROOT && fs.existsSync(DEFAULT_PLANOGRAM_ROOT) ? DEFAULT_PLANOGRAM_ROOT : "");

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".css": "text/css; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".pdf": "application/pdf",
};

function normalizePlanogramKey(filename) {
  const raw = String(filename || "").trim();
  if (!raw) return "";
  const base = path.basename(raw);
  const withoutExt = base.toLowerCase().endsWith(".pdf") ? base.slice(0, -4) : base;
  return withoutExt
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function safePathFromUrl(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0] || "/");
  const rel = decoded === "/" ? "/index.html" : decoded;
  const joined = path.join(ROOT, rel);
  const normalized = path.normalize(joined);
  if (!normalized.startsWith(path.normalize(ROOT + path.sep))) return null;
  return normalized;
}

function listFilesRecursive(rootDir, out) {
  let entries;
  try {
    entries = fs.readdirSync(rootDir, { withFileTypes: true });
  } catch (error) {
    return;
  }
  for (const entry of entries) {
    const full = path.join(rootDir, entry.name);
    if (entry.isDirectory()) {
      listFilesRecursive(full, out);
    } else {
      out.push(full);
    }
  }
}

function buildPlanogramIndex(rootDir) {
  if (!rootDir) return { index: new Map(), duplicates: new Map() };
  let stat;
  try {
    stat = fs.statSync(rootDir);
  } catch (error) {
    return { index: new Map(), duplicates: new Map() };
  }
  if (!stat.isDirectory()) return { index: new Map(), duplicates: new Map() };

  const files = [];
  listFilesRecursive(rootDir, files);

  const index = new Map();
  const duplicates = new Map();
  for (const filePath of files) {
    if (path.extname(filePath).toLowerCase() !== ".pdf") continue;
    const key = normalizePlanogramKey(path.basename(filePath));
    if (!key) continue;
    if (!index.has(key)) {
      index.set(key, filePath);
      continue;
    }
    if (!duplicates.has(key)) duplicates.set(key, [index.get(key)]);
    duplicates.get(key).push(filePath);
  }

  return { index, duplicates };
}

const planogram = buildPlanogramIndex(PLANOGRAM_ROOT);

function sendText(res, statusCode, text) {
  res.writeHead(statusCode, { "Content-Type": "text/plain; charset=utf-8" });
  res.end(text);
}

function servePdfByFilename(req, res) {
  const decodedPath = decodeURIComponent((req.url || "").split("?")[0] || "");
  const prefix = "/planogram/";
  const filePart = decodedPath.slice(prefix.length);
  let safeBase = path.basename(filePart || "");
  if (!safeBase || safeBase.includes("/") || safeBase.includes("\\")) {
    sendText(res, 400, "Bad request");
    return true;
  }

  if (!safeBase.toLowerCase().endsWith(".pdf")) safeBase += ".pdf";
  const key = normalizePlanogramKey(safeBase);
  if (!key) {
    sendText(res, 400, "Bad request");
    return true;
  }
  if (planogram.duplicates.has(key)) {
    sendText(res, 409, "Ambiguous PDF filename; multiple matches exist");
    return true;
  }

  const filePath = planogram.index.get(key);
  if (!filePath) {
    sendText(res, 404, "PDF not found");
    return true;
  }

  if ((req.method || "GET").toUpperCase() === "HEAD") {
    res.writeHead(200, { "Content-Type": CONTENT_TYPES[".pdf"] });
    res.end();
    return true;
  }

  fs.readFile(filePath, (err, buf) => {
    if (err) {
      sendText(res, 404, "Not found");
      return;
    }
    res.writeHead(200, { "Content-Type": CONTENT_TYPES[".pdf"] });
    res.end(buf);
  });
  return true;
}

const server = http.createServer((req, res) => {
  if ((req.url || "").startsWith("/planogram/")) {
    const handled = servePdfByFilename(req, res);
    if (handled) return;
  }

  const filePath = safePathFromUrl(req.url || "/");
  if (!filePath) {
    sendText(res, 400, "Bad request");
    return;
  }

  fs.readFile(filePath, (err, buf) => {
    if (err) {
      sendText(res, 404, "Not found");
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { "Content-Type": CONTENT_TYPES[ext] || "application/octet-stream" });
    res.end(buf);
  });
});

server.listen(PORT, "127.0.0.1", () => {
  // eslint-disable-next-line no-console
  console.log(`Serving ${ROOT} on http://127.0.0.1:${PORT}`);
  if (PLANOGRAM_ROOT) {
    console.log(`Planogram PDFs: ${PLANOGRAM_ROOT}`);
    if (planogram.duplicates.size) {
      console.log(`Warning: ${planogram.duplicates.size} duplicate PDF filenames detected (ambiguous)`);
    }
  } else {
    console.log("Planogram PDFs: (not configured) set PLANOGRAM_ROOT to enable /planogram/<file>.pdf");
  }
});
