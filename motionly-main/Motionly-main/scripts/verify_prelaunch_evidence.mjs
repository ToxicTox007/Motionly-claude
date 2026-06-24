import { readFileSync } from "node:fs";

const requiredFiles = [
  "docs/data-retention.md",
  "docs/operations.md",
  "docs/renderer-feature-matrix.md",
  "docs/backups-restore.md",
  "docs/prelaunch-review.md",
  "infra/secrets/README.md",
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
  const content = readFileSync(file, "utf8");
  if (!content.trim()) {
    throw new Error(`${file} is empty`);
  }
}

const review = readFileSync("docs/prelaunch-review.md", "utf8");
for (const term of requiredReviewTerms) {
  if (!review.includes(term)) {
    throw new Error(
      `docs/prelaunch-review.md is missing required evidence term: ${term}`,
    );
  }
}

const operations = readFileSync("docs/operations.md", "utf8");
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

const retention = readFileSync("docs/data-retention.md", "utf8");
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
