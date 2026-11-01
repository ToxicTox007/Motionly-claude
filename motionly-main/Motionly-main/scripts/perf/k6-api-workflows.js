import http from "k6/http";
import { check, group, sleep } from "k6";

const baseUrl = (__ENV.API_BASE_URL || "http://localhost:8000").replace(
  /\/$/,
  "",
);
const adminToken = __ENV.ADMIN_API_TOKEN || "";
const metricsToken = __ENV.METRICS_BEARER_TOKEN || "";
// Signed storage URLs may be origin-relative; the API is served under /api.
const origin = baseUrl.replace(/\/api$/, "");
// One part of this size per upload-flow iteration.
const uploadPartBytes = Number(__ENV.K6_UPLOAD_PART_BYTES || 65536);
const bearerToken = __ENV.API_BEARER_TOKEN || "";
const workspaceId = __ENV.WORKSPACE_ID || "";
const projectId = __ENV.PROJECT_ID || "";
const uploadId = __ENV.UPLOAD_ID || "";
const renderId = __ENV.RENDER_ID || "";
const strictSlo = __ENV.K6_STRICT_SLO === "1";
const summaryOutput = __ENV.K6_SUMMARY_OUTPUT || "test-results/k6-summary.json";

if (
  strictSlo &&
  (!bearerToken || !workspaceId || !projectId || !uploadId || !renderId)
) {
  throw new Error(
    "K6_STRICT_SLO=1 requires API_BEARER_TOKEN, WORKSPACE_ID, PROJECT_ID, UPLOAD_ID, and RENDER_ID.",
  );
}

// The API rate-limits each user per path (RATE_LIMIT_PER_MINUTE, 120 by
// default) and this smoke signs in one user, so each scenario stays near one
// request per second per path: a 429 is the product's limit working, not a
// latency result. The SLO contract's minimum samples are still met.
export const options = {
  scenarios: {
    project_reads: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_PROJECT_VUS || 1),
      duration: __ENV.K6_PROJECT_DURATION || "1m",
      exec: "projectReads",
    },
    upload_flow: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.K6_UPLOAD_RATE || 1),
      timeUnit: "1s",
      duration: __ENV.K6_UPLOAD_DURATION || "1m",
      preAllocatedVUs: Number(__ENV.K6_UPLOAD_VUS || 10),
      exec: "uploadFlow",
    },
    plan_and_render_status: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_PLAN_VUS || 1),
      duration: __ENV.K6_PLAN_DURATION || "1m",
      exec: "planAndRenderStatus",
    },
    metrics_and_admin: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_OPS_VUS || 1),
      duration: __ENV.K6_OPS_DURATION || "1m",
      exec: "metricsAndAdmin",
    },
  },
  thresholds: {
    http_req_failed: [strictSlo ? "rate<0.01" : "rate<0.05"],
    "http_req_duration{surface:projects}": ["p(95)<1000"],
    "http_req_duration{surface:uploads}": ["p(95)<1500"],
    "http_req_duration{surface:plans}": ["p(95)<2000"],
    "http_req_duration{surface:render_status}": ["p(95)<750"],
    "http_req_duration{surface:ops}": ["p(95)<1500"],
    ...(strictSlo ? { "checks{mode:strict}": ["rate>0.99"] } : {}),
  },
};

export function projectReads() {
  group("projects list and detail", () => {
    const list = http.get(
      `${baseUrl}/projects?limit=20`,
      requestOptions("projects"),
    );
    assertResponse(list, "projects list", [200]);
    if (projectId) {
      const detail = http.get(
        `${baseUrl}/projects/${projectId}`,
        requestOptions("projects"),
      );
      assertResponse(detail, "project detail", [200]);
    }
  });
  sleep(1);
}

export function uploadFlow() {
  group("resumable upload init, part, and abort", () => {
    // Task 17: every media type uploads through the resumable multipart path.
    // Each iteration opens its own session, sends one part to signed storage
    // and confirms it, then aborts the session, so the load never starts the
    // first-draft pipeline for hundreds of placeholder uploads.
    const target = projectId || "00000000-0000-4000-8000-000000000001";
    const uploads = `${baseUrl}/projects/${target}/uploads/multipart`;
    const init = http.post(
      `${uploads}/init`,
      JSON.stringify({
        filename: `k6-${__VU}-${__ITER}.mp4`,
        mime_type: "video/mp4",
        file_size_bytes: uploadPartBytes,
      }),
      requestOptions("uploads"),
    );
    assertResponse(init, "upload multipart init", [200, 201]);
    const session = safeJson(init);
    const part = (session.part_urls || [])[0];
    if (!session.session_id || !part) return;
    const url = part.upload_url.startsWith("/")
      ? `${origin}${part.upload_url}`
      : part.upload_url;
    const stored = http.put(url, new ArrayBuffer(uploadPartBytes), {
      headers: part.upload_headers || {},
      tags: { surface: "uploads", phase: strictSlo ? "strict" : "guarded" },
    });
    assertResponse(stored, "upload part to storage", [200, 201]);
    const etag = stored.headers.Etag || stored.headers.ETag || null;
    const confirm = http.post(
      `${uploads}/${session.session_id}/parts/${part.part_number}/complete`,
      JSON.stringify(etag ? { etag } : {}),
      requestOptions("uploads"),
    );
    assertResponse(confirm, "upload part confirm", [200]);
    const abort = http.post(
      `${uploads}/${session.session_id}/abort`,
      JSON.stringify({}),
      requestOptions("uploads"),
    );
    assertResponse(abort, "upload multipart abort", [200]);
  });
  sleep(1);
}

export function planAndRenderStatus() {
  group("plan create and render status", () => {
    if (projectId) {
      const plan = http.post(
        `${baseUrl}/projects/${projectId}/plans`,
        JSON.stringify({ prompt: "k6 performance scenario" }),
        requestOptions("plans"),
      );
      assertResponse(plan, "plan create", [200, 201, 202]);
    }
    if (renderId) {
      const render = http.get(
        `${baseUrl}/projects/${projectId}/renders/${renderId}`,
        requestOptions("render_status"),
      );
      assertResponse(render, "render status", [200]);
    }
  });
  sleep(1);
}

export function metricsAndAdmin() {
  group("metrics scrape and admin queue overview", () => {
    // /metrics takes the scrape token, not a user session.
    const metrics = http.get(`${baseUrl}/metrics`, {
      headers: metricsToken ? { authorization: `Bearer ${metricsToken}` } : {},
      tags: { surface: "ops", phase: strictSlo ? "strict" : "guarded" },
    });
    assertResponse(metrics, "metrics scrape", [200]);
    const admin = http.get(
      `${baseUrl}/admin/queues?limit=50`,
      requestOptions("ops"),
    );
    assertResponse(admin, "admin queue overview", [200]);
  });
  sleep(1);
}

function assertResponse(response, label, acceptedStatuses) {
  const guarded = response.status < 500;
  return check(
    response,
    {
      [`${label} succeeds`]: (value) =>
        strictSlo ? acceptedStatuses.includes(value.status) : guarded,
    },
    { mode: strictSlo ? "strict" : "guarded" },
  );
}

function requestOptions(surface) {
  const headers = { "content-type": "application/json" };
  if (bearerToken) headers.authorization = `Bearer ${bearerToken}`;
  if (workspaceId) headers["x-motionly-workspace-id"] = workspaceId;
  if (adminToken) headers["x-motionly-admin-token"] = adminToken;
  return {
    headers,
    tags: { surface, phase: strictSlo ? "strict" : "guarded" },
  };
}
function safeJson(response) {
  try {
    return response.json();
  } catch {
    return {};
  }
}

export function handleSummary(data) {
  const metrics = data.metrics || {};
  const p95 = (surface) =>
    metrics[`http_req_duration{surface:${surface}}`]?.values?.["p(95)"] ?? null;
  const count = (surface) =>
    metrics[`http_reqs{surface:${surface}}`]?.values?.count ?? 0;
  const payload = {
    schema_version: 1,
    mode: strictSlo ? "strict" : "guarded",
    revision: __ENV.DEPLOYMENT_REVISION || "unversioned",
    generated_at: new Date().toISOString(),
    http_requests: metrics.http_reqs?.values?.count ?? 0,
    http_error_rate: metrics.http_req_failed?.values?.rate ?? null,
    status_5xx_rate:
      metrics["http_req_failed{expected_response:false}"]?.values?.rate ?? null,
    checks_rate: metrics.checks?.values?.rate ?? null,
    samples_by_surface: {
      projects: count("projects"),
      uploads: count("uploads"),
      plans: count("plans"),
      render_status: count("render_status"),
      ops: count("ops"),
    },
    p95_ms_by_surface: {
      projects: p95("projects"),
      uploads: p95("uploads"),
      plans: p95("plans"),
      render_status: p95("render_status"),
      ops: p95("ops"),
    },
  };
  return { [summaryOutput]: JSON.stringify(payload, null, 2) };
}
