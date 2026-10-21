import http from "k6/http";
import { check, group, sleep } from "k6";

const baseUrl = (__ENV.API_BASE_URL || "http://localhost:8000").replace(
  /\/$/,
  "",
);
const adminToken = __ENV.ADMIN_API_TOKEN || "";
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

export const options = {
  scenarios: {
    project_reads: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_PROJECT_VUS || 5),
      duration: __ENV.K6_PROJECT_DURATION || "1m",
      exec: "projectReads",
    },
    upload_flow: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.K6_UPLOAD_RATE || 2),
      timeUnit: "1s",
      duration: __ENV.K6_UPLOAD_DURATION || "1m",
      preAllocatedVUs: Number(__ENV.K6_UPLOAD_VUS || 10),
      exec: "uploadFlow",
    },
    plan_and_render_status: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_PLAN_VUS || 3),
      duration: __ENV.K6_PLAN_DURATION || "1m",
      exec: "planAndRenderStatus",
    },
    metrics_and_admin: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_OPS_VUS || 2),
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
  group("resumable upload init, part, and complete", () => {
    // Task 17: every media type uploads through the resumable multipart path.
    // The legacy one-shot upload init and complete endpoints are gone.
    const target = projectId || "00000000-0000-4000-8000-000000000001";
    const init = http.post(
      `${baseUrl}/projects/${target}/uploads/multipart/init`,
      JSON.stringify({
        filename: `k6-${__VU}-${__ITER}.mp4`,
        mime_type: "video/mp4",
        file_size_bytes: 1048576,
      }),
      requestOptions("uploads"),
    );
    assertResponse(init, "upload multipart init", [200, 201]);
    const session = safeJson(init);
    const sessionId = uploadId || session.session_id;
    const firstPart = (session.part_urls || [])[0];
    if (sessionId && firstPart) {
      const confirm = http.post(
        `${baseUrl}/projects/${target}/uploads/multipart/${sessionId}/parts/${firstPart.part_number}/complete`,
        JSON.stringify({}),
        requestOptions("uploads"),
      );
      assertResponse(confirm, "upload part confirm", [200]);
      const complete = http.post(
        `${baseUrl}/projects/${target}/uploads/multipart/${sessionId}/complete`,
        JSON.stringify({}),
        requestOptions("uploads"),
      );
      assertResponse(complete, "upload multipart complete", [200]);
    }
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
        `${baseUrl}/renders/${renderId}`,
        requestOptions("render_status"),
      );
      assertResponse(render, "render status", [200]);
    }
  });
  sleep(1);
}

export function metricsAndAdmin() {
  group("metrics scrape and admin queue overview", () => {
    const metrics = http.get(`${baseUrl}/metrics`, requestOptions("ops"));
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
