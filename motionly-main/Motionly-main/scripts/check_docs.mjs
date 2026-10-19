import { access, readdir, readFile } from "node:fs/promises";
import { dirname, extname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DOCS_DIR = resolve(ROOT, "docs");
const INDEX_FILE = resolve(DOCS_DIR, "README.md");
const REPOSITORY_ROOT_LINK_PREFIXES = [
  ".github/",
  "apps/",
  "infra/",
  "packages/",
  "scripts/",
  "supabase/",
];

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await walk(path)));
    } else if (
      [".md", ".markdown"].includes(extname(entry.name).toLowerCase())
    ) {
      files.push(path);
    }
  }
  return files;
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function markdownLinks(content) {
  return [...content.matchAll(/!?\[[^\]]*\]\(([^)]+)\)/g)].map((match) =>
    match[1].trim(),
  );
}

function linkPath(sourceFile, rawTarget) {
  const target = rawTarget.replace(/^<|>$/g, "").split("#", 1)[0].trim();
  if (!target || target.startsWith("#") || /^[a-z][a-z\d+.-]*:/i.test(target)) {
    return null;
  }

  const normalized = target.replaceAll("\\", "/");
  if (
    REPOSITORY_ROOT_LINK_PREFIXES.some((prefix) =>
      normalized.startsWith(prefix),
    )
  ) {
    return resolve(ROOT, normalized);
  }
  if (normalized.startsWith("docs/")) {
    return resolve(ROOT, normalized);
  }
  return resolve(dirname(sourceFile), normalized);
}

const markdownFiles = (await walk(DOCS_DIR)).sort();
const errors = [];
const indexedTargets = new Set();

if (!(await exists(INDEX_FILE))) {
  errors.push("docs/README.md is missing");
} else {
  const indexContent = await readFile(INDEX_FILE, "utf8");
  for (const rawTarget of markdownLinks(indexContent)) {
    const target = linkPath(INDEX_FILE, rawTarget);
    if (!target) continue;
    if (!(await exists(target))) {
      errors.push(`docs/README.md links to missing target: ${rawTarget}`);
      continue;
    }
    const relativeToDocs = relative(DOCS_DIR, target);
    if (
      relativeToDocs !== "" &&
      !relativeToDocs.startsWith("..") &&
      !relativeToDocs.includes(":")
    ) {
      indexedTargets.add(relativeToDocs.replaceAll("\\", "/"));
    }
  }
}

for (const file of markdownFiles) {
  const relativePath = relative(DOCS_DIR, file).replaceAll("\\", "/");
  const content = await readFile(file, "utf8");
  for (const rawTarget of markdownLinks(content)) {
    const target = linkPath(file, rawTarget);
    if (target && !(await exists(target))) {
      errors.push(`${relativePath} links to missing target: ${rawTarget}`);
    }
  }
  if (relativePath !== "README.md" && !indexedTargets.has(relativePath)) {
    errors.push(`${relativePath} is not linked from docs/README.md`);
  }
}

if (errors.length) {
  console.error(
    `Documentation check failed (${errors.length} issue${errors.length === 1 ? "" : "s"}):`,
  );
  for (const error of errors) console.error(`- ${error}`);
  process.exit(1);
}

console.log(
  `Documentation check passed: ${markdownFiles.length} Markdown files are linked and all local links resolve.`,
);
