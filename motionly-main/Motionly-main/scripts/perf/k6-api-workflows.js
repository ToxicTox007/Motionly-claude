import http from "k6/http";
import { check, group, sleep } from "k6";

const baseUrl = (__ENV.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const adminToken = __ENV.ADMIN_API_TOKEN || "";
const bearerToken = __ENV.API_BEARER_TOKEN || "";
const workspaceId = __ENV.WORKSPACE_ID || "";
const projectId = __ENV.PROJECT_ID || "";
const uploadId = __ENV.UPLOAD_ID || "";
const renderId = __ENV.RENDER_ID || "";

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
    http_req_failed: ["rate<0.05"],
    "http_req_duration{surface:projects}": ["p(95)<1000"],
    "http_req_duration{surface:uploads}": ["p(95)<1500"],
    "http_req_duration{surface:plans}": ["p(95)<2000"],
    "http_req_duration{surface:render_status}": ["p(95)<750"],
    "http_req_duration{surface:ops}": ["p(95)<1500"],
  },
};

export function projectReads() {
  group("projects list and detail", () => {
    const list = http.get(`${baseUrl}/projects?limit=20`, requestOptions("projects"));
    check(list, {
      "projects list responds": (response) => response.status < 500,
    });
    if (projectId) {
      const detail = http.get(`${baseUrl}/projects/${projectId}`, requestOptions("projects"));
      check(detail, {
        "project detail responds": (response) => response.status < 500,
      });
    }
  });
  sleep(1);
}

export function uploadFlow() {
  group("upload init and complete", () => {
    const init = http.post(
      `${baseUrl}/uploads/init`,
      JSON.stringify({
        project_id: projectId || "00000000-0000-4000-8000-000000000001",
        filename: `k6-${__VU}-${__ITER}.mp4`,
        content_type: "video/mp4",
        file_size_bytes: 1048576,
      }),
      requestOptions("uploads"),
    );
    check(init, {
      "upload init is accepted or guarded": (response) =>
        [200, 201, 202, 400, 401, 403, 409, 429].includes(response.status),
    });

    const completeTarget = uploadId || safeJson(init).upload_id;
    if (completeTarget) {
      const complete = http.post(
        `${baseUrl}/uploads/${completeTarget}/complete`,
        JSON.stringify({ object_version: `k6-${Date.now()}` }),
        requestOptions("uploads"),
      );
      check(complete, {
        "upload complete is accepted or guarded": (response) =>
          [200, 202, 400, 401, 403, 404, 409, 429].includes(response.status),
      });
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
      check(plan, {
        "plan create is accepted or guarded": (response) =>
          [200, 201, 202, 400, 401, 403, 409, 429].includes(response.status),
      });
    }
    if (renderId) {
      const render = http.get(`${baseUrl}/renders/${renderId}`, requestOptions("render_status"));
      check(render, {
        "render status responds": (response) => response.status < 500,
      });
    }
  });
  sleep(1);
}

export function metricsAndAdmin() {
  group("metrics scrape and admin queue overview", () => {
    const metrics = http.get(`${baseUrl}/metrics`, requestOptions("ops"));
    check(metrics, {
      "metrics scrape is reachable or protected": (response) =>
        [200, 401, 403].includes(response.status),
    });
    const admin = http.get(`${baseUrl}/admin/queues?limit=50`, requestOptions("ops"));
    check(admin, {
      "admin queue overview is reachable or protected": (response) =>
        [200, 401, 403].includes(response.status),
    });
  });
  sleep(1);
}

function requestOptions(surface) {
  const headers = {
    "content-type": "application/json",
  };
  if (bearerToken) headers.authorization = `Bearer ${bearerToken}`;
  if (workspaceId) headers["x-motionly-workspace-id"] = workspaceId;
  if (adminToken) headers["x-motionly-admin-token"] = adminToken;
  return {
    headers,
    tags: { surface },
  };
}

function safeJson(response) {
  try {
    return response.json();
  } catch {
    return {};
  }
}
