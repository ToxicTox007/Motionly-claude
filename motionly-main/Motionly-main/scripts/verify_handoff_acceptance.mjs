#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ledgerPath = resolve(root, "scripts/handoff-acceptance.json");
const ledger = JSON.parse(await readFile(ledgerPath, "utf8"));
const allowedStatuses = new Set(["pending", "verified", "failed", "blocked"]);
const requiredCategories = new Set(["task", "product", "architecture", "risk", "slo"]);
const failures = [];
const ids = new Set();

if (ledger.schema_version !== 1) failures.push("schema_version must be 1");
if (!ledger.evidence_policy) failures.push("evidence_policy is required");
if (!Array.isArray(ledger.items)) failures.push("items must be an array");

for (const [index, item] of (ledger.items ?? []).entries()) {
  const label = item?.id || `item[${index}]`;
  if (!item?.id || typeof item.id !== "string") failures.push(`${label}: id is required`);
  if (ids.has(item?.id)) failures.push(`${label}: duplicate id`);
  ids.add(item?.id);
  if (!requiredCategories.has(item?.category)) failures.push(`${label}: invalid category`);
  if (!item?.criterion || typeof item.criterion !== "string") {
    failures.push(`${label}: criterion is required`);
  }
  if (!allowedStatuses.has(item?.status)) failures.push(`${label}: invalid status`);
  for (const field of ["implementation_evidence", "verification_commands", "runtime_evidence"]) {
    if (!Array.isArray(item?.[field])) failures.push(`${label}: ${field} must be an array`);
  }
  if (item?.status === "verified") {
    if (!item.implementation_evidence.length) {
      failures.push(`${label}: verified item lacks implementation evidence`);
    }
    if (!item.verification_commands.length || !item.runtime_evidence.length) {
      failures.push(`${label}: verified item lacks command/runtime evidence`);
    }
  }
}

for (let task = 1; task <= 18; task += 1) {
  const id = `task-${String(task).padStart(2, "0")}`;
  if (!ids.has(id)) failures.push(`missing required ${id}`);
}
for (const category of requiredCategories) {
  if (!(ledger.items ?? []).some((item) => item.category === category)) {
    failures.push(`missing category ${category}`);
  }
}

if (process.argv.includes("--require-verified")) {
  for (const item of ledger.items ?? []) {
    if (item.status !== "verified") failures.push(`${item.id}: ${item.status}`);
  }
}

const counts = Object.fromEntries(
  [...allowedStatuses].map((status) => [
    status,
    (ledger.items ?? []).filter((item) => item.status === status).length,
  ]),
);
console.log(JSON.stringify({ ledger: ledgerPath, total: ledger.items?.length ?? 0, counts }, null, 2));
if (failures.length) {
  console.error(`Acceptance ledger validation failed (${failures.length}):`);
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}
console.log("Acceptance ledger schema and handoff task coverage are valid.");
