#!/usr/bin/env node
// Inventory of every database object the worker and web code touch.
//
// Prints JSON {usages, unresolved, dynamic_writes} consumed by
// apps/api/tests/schema_usage.py. Worker code runs with the service role; web
// code queries Supabase with the signed-in user's session (role
// "authenticated"). Static scanning covers supabase-js query chains, RPC calls
// with object-literal arguments, and the worker's direct REST fetch. The
// workflow RPC arguments are built at runtime, so they are captured by calling
// the exported step functions with a recording RPC function.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const usages = [];
const unresolved = [];
const dynamicWrites = [];

function walk(directory, extensions) {
  const found = [];
  for (const entry of readdirSync(directory)) {
    if (entry === "node_modules" || entry === ".next" || entry === "tests")
      continue;
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) found.push(...walk(full, extensions));
    else if (
      extensions.some((ext) => entry.endsWith(ext)) &&
      !/\.test\.[cm]?[jt]sx?$/.test(entry)
    )
      found.push(full);
  }
  return found;
}

function lineOf(source, index) {
  return source.slice(0, index).split("\n").length;
}

// Index just past a balanced (), [], or {} group that starts at `open`,
// skipping string literals, template literals, and comments.
function matchClose(source, open) {
  const pairs = { "(": ")", "[": "]", "{": "}" };
  const stack = [pairs[source[open]]];
  for (let i = open + 1; i < source.length; i += 1) {
    const char = source[i];
    if (char === '"' || char === "'" || char === "`") {
      i = skipString(source, i);
      continue;
    }
    if (char === "/" && source[i + 1] === "/") {
      i = source.indexOf("\n", i);
      if (i < 0) return source.length;
      continue;
    }
    if (char === "/" && source[i + 1] === "*") {
      i = source.indexOf("*/", i + 2) + 1;
      continue;
    }
    if (pairs[char]) stack.push(pairs[char]);
    else if (char === stack[stack.length - 1]) {
      stack.pop();
      if (stack.length === 0) return i + 1;
    }
  }
  return source.length;
}

function skipString(source, start) {
  const quote = source[start];
  for (let i = start + 1; i < source.length; i += 1) {
    if (source[i] === "\\") {
      i += 1;
      continue;
    }
    if (quote === "`" && source[i] === "$" && source[i + 1] === "{") {
      i = matchClose(source, i + 1) - 1;
      continue;
    }
    if (source[i] === quote) return i;
  }
  return source.length;
}

// Top-level keys of an object literal starting at `open` ("{").
function objectKeys(source, open) {
  const close = matchClose(source, open);
  const keys = [];
  let depth = 0;
  for (let i = open + 1; i < close - 1; i += 1) {
    const char = source[i];
    if (char === '"' || char === "'" || char === "`") {
      i = skipString(source, i);
      continue;
    }
    if ("([{".includes(char)) {
      i = matchClose(source, i) - 1;
      continue;
    }
    if (depth === 0) {
      const match = /^\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$]*))\s*:/.exec(
        source.slice(i),
      );
      const previous = source.slice(open + 1, i).trimEnd();
      if (match && (previous === "" || previous.endsWith(","))) {
        keys.push(match[1] || match[2] || match[3]);
        i += match[0].length - 1;
        continue;
      }
      const shorthand = /^\s*([A-Za-z_$][\w$]*)\s*(?=,|$)/.exec(
        source.slice(i, close - 1),
      );
      if (shorthand && (previous === "" || previous.endsWith(","))) {
        keys.push(shorthand[1]);
        i += shorthand[0].length - 1;
      }
    }
  }
  return keys;
}

// The method-call chain that follows `.from("table")`, up to the end of the
// expression (a `;` or a closing bracket that belongs to an enclosing call).
function chainText(source, start) {
  let i = start;
  while (i < source.length) {
    const char = source[i];
    if (char === '"' || char === "'" || char === "`") {
      i = skipString(source, i) + 1;
      continue;
    }
    if ("([{".includes(char)) {
      i = matchClose(source, i);
      continue;
    }
    if (
      char === ";" ||
      char === ")" ||
      char === "]" ||
      char === "}" ||
      char === ","
    )
      break;
    i += 1;
  }
  return { text: source.slice(start, i), end: i };
}

const FILTER_METHODS =
  /\.(eq|neq|gt|gte|lt|lte|like|ilike|is|in|contains|containedBy|order)\(\s*"([a-z_][a-z0-9_]*)"/g;

function scanFile(file, role) {
  const source = readFileSync(file, "utf8");
  const rel = relative(root, file);
  for (const match of source.matchAll(
    /(\.storage\s*)?\.from\(\s*"([a-z][a-z0-9_]*)"\s*\)/g,
  )) {
    if (match[1]) continue; // Storage bucket, not a table.
    const table = match[2];
    const where = `${rel}:${lineOf(source, match.index)}`;
    const chainStart = match.index + match[0].length;
    const { text } = chainText(source, chainStart);
    usages.push({ role, kind: "table", source: where, table });
    const select =
      /\.select\(\s*(?:"([^"]*)"|'([^']*)'|`([^`$]*)`)?\s*[,)]/.exec(text);
    if (select) {
      usages.push({
        role,
        kind: "select",
        source: where,
        table,
        select: select[1] ?? select[2] ?? select[3] ?? "*",
      });
    }
    const filters = [...text.matchAll(FILTER_METHODS)].map((item) => item[2]);
    if (filters.length) {
      usages.push({
        role,
        kind: "filter",
        source: where,
        table,
        columns: [...new Set(filters)],
      });
    }
    for (const write of text.matchAll(/\.(insert|update|upsert)\(\s*/g)) {
      const argStart = chainStart + write.index + write[0].length;
      if (source[argStart] === "{") {
        usages.push({
          role,
          kind: "write",
          source: where,
          table,
          columns: objectKeys(source, argStart),
          action: write[1],
        });
      } else {
        dynamicWrites.push(`${where}: ${write[1]} ${table}`);
      }
    }
    if (/\.delete\(\s*\)/.test(text))
      usages.push({ role, kind: "delete", source: where, table });
  }
  for (const match of source.matchAll(
    /\.rpc\(\s*"([a-z][a-z0-9_]*)"\s*(,\s*)?/g,
  )) {
    const where = `${rel}:${lineOf(source, match.index)}`;
    const argStart = match.index + match[0].length;
    let args = [];
    if (match[2]) {
      if (source[argStart] === "{") args = objectKeys(source, argStart);
      else {
        args = null;
        unresolved.push(
          `${where}: arguments for rpc ${match[1]} are not an object literal`,
        );
      }
    }
    usages.push({ role, kind: "rpc", source: where, rpc: match[1], args });
  }
  for (const match of source.matchAll(
    /new URL\(\s*"\/rest\/v1\/([a-z][a-z0-9_]*)"/g,
  )) {
    const where = `${rel}:${lineOf(source, match.index)}`;
    const table = match[1];
    usages.push({ role, kind: "table", source: where, table });
    const tail = source.slice(
      match.index,
      source.indexOf("fetchImpl(url", match.index),
    );
    const params = [
      ...tail.matchAll(/searchParams\.set\(\s*"([a-z_]+)"\s*,\s*([^)]*)\)/g),
    ];
    const select = params.find(([, key]) => key === "select");
    if (select) {
      const literal = /^"([^"]*)"$/.exec(select[2].trim());
      if (literal)
        usages.push({
          role,
          kind: "select",
          source: where,
          table,
          select: literal[1],
        });
      else
        unresolved.push(
          `${where}: select for ${table} is not a string literal`,
        );
    }
    const filters = params
      .map(([, key]) => key)
      .filter((key) => !["select", "limit", "offset", "order"].includes(key));
    if (filters.length)
      usages.push({
        role,
        kind: "filter",
        source: where,
        table,
        columns: filters,
      });
  }
}

const workerFiles = walk(join(root, "apps/worker/src"), [".mjs"]);
for (const file of workerFiles) scanFile(file, "service_role");
for (const directory of ["app", "components", "lib", "hooks"]) {
  for (const file of walk(join(root, "apps/web", directory), [
    ".ts",
    ".tsx",
    ".mjs",
  ])) {
    scanFile(file, "authenticated");
  }
}

// Workflow step RPCs: arguments are assembled at runtime from the job payload.
const worker = await import(
  pathToFileURL(join(root, "apps/worker/src/worker.mjs")).href
);
const job = {
  id: "queue-job-0001",
  queueName: "visual_analysis",
  timestamp: Date.now(),
  attemptsMade: 0,
  opts: { attempts: 1 },
  data: {
    id: "queue-job-0001",
    kind: "visual_analysis",
    queue_name: "visual_analysis",
    workspace_id: "33000000-0000-4000-8000-000000000001",
    project_id: "44000000-0000-4000-8000-000000000001",
    resource_id: "77000000-0000-4000-8000-000000000001",
    workflow_run_id: "55000000-0000-4000-8000-000000000001",
    workflow_step_id: "66000000-0000-4000-8000-000000000001",
    step_key: "analyze-source-v2",
    step_kind: "analyze_source",
    attempt_key: "dispatcher-attempt-0001",
    workflow_attempt_number: 1,
    workflow_max_attempts: 3,
    input_hash: "a".repeat(64),
    next_run_state: "proposal_ready",
  },
  async updateProgress() {},
};
const recorded = [];
const rpcCall = async (rpc, parameters) => {
  recorded.push({ rpc, args: Object.keys(parameters) });
  return [{}];
};
const claim = "88000000-0000-4000-8000-000000000001";
const attempts = [
  () => worker.beginWorkflowStep(job, { rpcCall }),
  () => worker.completeWorkflowStep(job, claim, { ok: true }, { rpcCall }),
  () => worker.failWorkflowStep(job, claim, new Error("probe"), { rpcCall }),
];
for (const attempt of attempts) {
  try {
    await attempt();
  } catch {
    // Result validation may reject the empty probe response; the call is recorded.
  }
}
for (const name of [
  "motionly_begin_workflow_step",
  "motionly_complete_workflow_step",
  "motionly_fail_workflow_step",
]) {
  const call = recorded.find((item) => item.rpc === name);
  if (!call)
    unresolved.push(
      `apps/worker/src/worker.mjs: ${name} was not called by its step function`,
    );
  else
    usages.push({
      role: "service_role",
      kind: "rpc",
      source: "apps/worker/src/worker.mjs (recorded)",
      rpc: name,
      args: call.args,
    });
}

process.stdout.write(
  `${JSON.stringify({ usages, unresolved, dynamic_writes: dynamicWrites }, null, 2)}\n`,
);
