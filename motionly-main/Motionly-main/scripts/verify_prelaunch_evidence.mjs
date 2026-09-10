import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const readText = (relativePath) => readFileSync(resolve(ROOT, relativePath), "utf8");

const requiredFiles = [
  "docs/data-retention.md",
  "docs/operations.md",
  "docs/renderer-feature-matrix.md",
  "docs/backups-restore.md",
  "docs/prelaunch-review.md",
  "docs/security/secrets.md",
];

const requiredReviewTerms = [
  "Accessibility",
  "Keyboard editing",
  "Screen-reader semantics",
  "Contrast",
  "Reduced motion",
  "Browser and device compatibility",
  "Privacy and legal",
  "retention",
  "legal approval",
];

for (const file of requiredFiles) {
  const content = readText(file);
  if (!content.trim()) {
    throw new Error(`${file} is empty`);
  }
}

const review = readText("docs/prelaunch-review.md");
for (const term of requiredReviewTerms) {
  if (!review.includes(term)) {
    throw new Error(
      `docs/prelaunch-review.md is missing required evidence term: ${term}`,
    );
  }
}

const operations = readText("docs/operations.md");
for (const term of [
  "Provider latency/error SLO",
  "/health/ready",
  "worker heartbeat",
]) {
  if (!operations.includes(term)) {
    throw new Error(
      `docs/operations.md is missing required operations term: ${term}`,
    );
  }
}

const retention = readText("docs/data-retention.md");
for (const term of [
  "Source video",
  "Transcripts",
  "Encoded exports",
  "Local processing cache",
]) {
  if (!retention.includes(term)) {
    throw new Error(
      `docs/data-retention.md is missing retention category: ${term}`,
    );
  }
}
