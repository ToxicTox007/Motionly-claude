import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../../", import.meta.url);

test("k6 performance harness covers required API workflows", async () => {
  const source = await readFile(
    new URL("scripts/perf/k6-api-workflows.js", root),
    "utf8",
  );
  const docs = await readFile(
    new URL("docs/ops/performance-load-tests.md", root),
    "utf8",
  );
  const pkg = await readFile(new URL("package.json", root), "utf8");

  for (const scenario of [
    "projectReads",
    "uploadFlow",
    "planAndRenderStatus",
    "metricsAndAdmin",
  ]) {
    assert.match(source, new RegExp(`exec: "${scenario}"`));
  }
  for (const route of [
    "/projects?limit=20",
    "/uploads/init",
    "/complete",
    "/plans",
    "/renders/",
    "/metrics",
    "/admin/queues?limit=50",
  ]) {
    assert.match(source, new RegExp(route.replace(/[/?]/g, "\\$&")));
  }
  assert.match(source, /K6_STRICT_SLO/);
  assert.match(source, /K6_SUMMARY_OUTPUT/);
  assert.match(source, /handleSummary/);
  assert.match(source, /http_req_duration\{surface:projects\}/);
  assert.match(source, /http_req_failed/);
  assert.match(pkg, /"perf:k6": "k6 run scripts\/perf\/k6-api-workflows\.js"/);
  assert.match(docs, /before\/after evidence/);
});

test("BullMQ load harness seeds above 500 jobs per queue", async () => {
  const source = await readFile(
    new URL("scripts/perf/bullmq-queue-load.mjs", root),
    "utf8",
  );
  const pkg = await readFile(new URL("package.json", root), "utf8");

  assert.match(source, /LOAD_TEST_JOBS_PER_QUEUE \|\| "750"/);
  assert.match(source, /jobsPerQueue > 500/);
  assert.match(
    source,
    /getJobsPaged\(queue,\s*\[\s*"waiting",\s*"prioritized",\s*"delayed",?\s*\]\)/,
  );
  assert.match(source, /RUN_BULLMQ_LOAD_TEST=1/);
  assert.match(source, /BULLMQ_LOAD_OUTPUT/);
  assert.match(source, /cleanup_verified/);
  assert.match(source, /job\.remove\(\)/);
  assert.match(
    pkg,
    /"perf:bullmq": "node scripts\/perf\/bullmq-queue-load\.mjs"/,
  );
});

test("browser performance harness covers required app surfaces", async () => {
  const source = await readFile(
    new URL("apps/web/tests/e2e/browser-performance.spec.mjs", root),
    "utf8",
  );
  const docs = await readFile(
    new URL("docs/ops/performance-load-tests.md", root),
    "utf8",
  );
  const rootPkg = await readFile(new URL("package.json", root), "utf8");
  const webPkg = await readFile(new URL("apps/web/package.json", root), "utf8");

  for (const surface of [
    "landing",
    "login",
    "dashboard",
    "projects",
    "project_editor",
    "billing",
    "account",
    "admin_ops",
  ]) {
    assert.match(source, new RegExp(`name: "${surface}"`));
  }
  assert.match(source, /RUN_BROWSER_PERF/);
  assert.match(source, /PERF_ENFORCE/);
  assert.match(source, /schema_version/);
  assert.match(source, /domContentLoadedMs/);
  assert.match(source, /firstContentfulPaintMs/);
  assert.match(source, /browser-performance\.json/);
  assert.match(
    rootPkg,
    /"perf:browser": "pnpm --filter @motionly\/web perf:browser"/,
  );
  assert.match(
    webPkg,
    /"perf:browser": "playwright test tests\/e2e\/browser-performance\.spec\.mjs"/,
  );
  assert.match(docs, /Browser performance/);
});

test("golden media benchmark harness covers required media surfaces", async () => {
  const source = await readFile(
    new URL("scripts/perf/media-golden-benchmarks.mjs", root),
    "utf8",
  );
  const docs = await readFile(
    new URL("docs/ops/performance-load-tests.md", root),
    "utf8",
  );
  const pkg = await readFile(new URL("package.json", root), "utf8");

  for (const surface of [
    "visual",
    "matte",
    "ffmpeg_render_export",
    "remotion_render",
    "remotion_export",
  ]) {
    assert.match(source, new RegExp(`surface: "${surface}"`));
  }
  assert.match(source, /RUN_MEDIA_BENCHMARKS/);
  assert.match(source, /media-benchmarks\.json/);
  assert.match(source, /duration_ms/);
  assert.match(source, /disk_free_bytes/);
  assert.match(source, /memory_rss_bytes/);
  assert.match(source, /gpu/);
  assert.match(source, /cache_dir/);
  assert.match(
    pkg,
    /"perf:media": "node scripts\/perf\/media-golden-benchmarks\.mjs"/,
  );
  assert.match(docs, /Golden media benchmarks/);
});

test("SLO contract assertion has a fail-closed release mode", async () => {
  const source = await readFile(
    new URL("scripts/perf/assert-slo.mjs", root),
    "utf8",
  );
  const contract = await readFile(
    new URL("scripts/perf/slo-contract.json", root),
    "utf8",
  );
  assert.match(source, /insufficient_samples/);
  assert.match(source, /canary_revision_mismatch/);
  assert.match(source, /mode === "enforce"/);
  assert.match(contract, /"minimum_samples"/);
  assert.match(contract, /"queue_backpressure_count"/);
});
