// Parse every JS/JSX/TS/TSX file under a directory with esbuild and print one JSON report.
// Usage: node validate.mjs <dir>   (the frontend-validator image's entrypoint)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { extname, join, relative } from "node:path";

const require = createRequire("/opt/validator/");
const esbuild = require("esbuild");

const LOADERS = { ".js": "jsx", ".jsx": "jsx", ".mjs": "js", ".cjs": "js", ".ts": "ts", ".tsx": "tsx" };
const SKIP = new Set(["node_modules", ".git", "dist", "build"]);
const MAX_ERRORS = 200;

function* walk(dir) {
  for (const name of readdirSync(dir).sort()) {
    if (SKIP.has(name)) continue;
    const path = join(dir, name);
    const info = statSync(path);
    if (info.isDirectory()) yield* walk(path);
    else if (info.isFile() && LOADERS[extname(name)]) yield path;
  }
}

const root = process.argv[2] || "/work";
const report = { validator: `esbuild@${esbuild.version}`, files: 0, errors: [] };
for (const path of walk(root)) {
  report.files += 1;
  try {
    esbuild.transformSync(readFileSync(path, "utf8"), { loader: LOADERS[extname(path)], logLevel: "silent" });
  } catch (error) {
    for (const item of error.errors || [{ text: String(error) }]) {
      if (report.errors.length >= MAX_ERRORS) break;
      report.errors.push({
        path: relative(root, path),
        line: item.location ? item.location.line : null,
        column: item.location ? item.location.column : null,
        message: item.text,
      });
    }
  }
}
process.stdout.write(JSON.stringify(report) + "\n");
