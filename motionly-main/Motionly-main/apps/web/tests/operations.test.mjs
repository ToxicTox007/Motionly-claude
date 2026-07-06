import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("admin ops fetches bounded queue jobs and surfaces slice metadata", async () => {
  const page = await readFile(new URL("app/(app)/admin/ops/page.tsx", root), "utf8");
  const list = await readFile(new URL("app/(app)/admin/ops/AdminJobsList.tsx", root), "utf8");

  assert.match(page, /\/admin\/queues\?limit=50/);
  assert.match(page, /total_jobs\?: number/);
  assert.match(page, /limit\?: number/);
  assert.match(page, /totalJobs=\{queues\?\.total_jobs \?\? 0\}/);
  assert.match(list, /Showing \{Math\.min\(jobs\.length, limit\)\} of \{totalJobs\} latest jobs/);
  assert.doesNotMatch(list, /filteredJobs\.slice\(0, 50\)/);
});

test("admin ops surfaces provider SLO and cost panels", async () => {
  const page = await readFile(new URL("app/(app)/admin/ops/page.tsx", root), "utf8");

  assert.match(page, /type ProviderSloSummary/);
  assert.match(page, /type ProviderCostSummary/);
  assert.match(page, /provider_slos\?: ProviderSloSummary\[\]/);
  assert.match(page, /Provider SLOs/);
  assert.match(page, /Provider costs/);
  assert.match(page, /formatPercent\(slo\.success_rate\)/);
  assert.match(page, /formatLatency\(slo\.p95_latency_ms\)/);
  assert.match(page, /provider_costs\?\.by_provider/);
  assert.match(page, /cost_per_approved_export_usd/);
});

test("admin ops surfaces runtime operations dashboard signals", async () => {
  const page = await readFile(new URL("app/(app)/admin/ops/page.tsx", root), "utf8");

  assert.match(page, /type OpsDashboard/);
  assert.match(page, /operations_dashboard\?: OpsDashboard/);
  assert.match(page, /Runtime signals/);
  assert.match(page, /API p95/);
  assert.match(page, /API p99/);
  assert.match(page, /Route errors/);
  assert.match(page, /Slow Supabase/);
  assert.match(page, /Max queue age/);
  assert.match(page, /GPU backlog/);
  assert.match(page, /Worker freshness/);
  assert.match(page, /Render p95/);
  assert.match(page, /Export p95/);
});

test("web runtime config sends cache headers for static assets", async () => {
  const config = await readFile(new URL("next.config.ts", root), "utf8");

  assert.match(config, /async headers\(\)/);
  assert.match(config, /source: "\/_next\/static\/:path\*"/);
  assert.match(config, /public, max-age=31536000, immutable/);
  assert.match(config, /source: "\/icon\.svg"/);
  assert.match(config, /stale-while-revalidate=604800/);
});
