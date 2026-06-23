import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const supabaseUrl = process.env.SUPABASE_URL || "http://127.0.0.1:54321";
const anonKey = process.env.SUPABASE_ANON_KEY || "";
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const apiUrl = "http://127.0.0.1:8000";
const password = "Motionly-E2E-Strong-Password-1!";
let email;
let userId;
let workspaceId;
let accessToken;
let mediaDirectory;
let mediaPath;

test.beforeAll(async () => {
  assertEnvironment();
  email = `creator-${Date.now()}@motionly.test`;
  const created = await providerFetch("/auth/v1/admin/users", {
    method: "POST",
    body: { email, password, email_confirm: true, user_metadata: { full_name: "E2E Creator" } },
  });
  userId = created.id;
  const session = await fetchJson(`${supabaseUrl}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: anonKey, "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  accessToken = session.access_token;
  const memberships = await rest("/workspace_members", {
    user_id: `eq.${userId}`,
    status: "eq.active",
    select: "workspace_id",
    limit: "1",
  });
  workspaceId = memberships[0].workspace_id;
  mediaDirectory = await mkdtemp(join(tmpdir(), "motionly-browser-e2e-"));
  mediaPath = join(mediaDirectory, "creator-source.mp4");
  createVideo(mediaPath);
});

test.afterAll(async () => {
  if (userId) await providerFetch(`/auth/v1/admin/users/${userId}`, { method: "DELETE" });
  if (mediaDirectory) await rm(mediaDirectory, { recursive: true, force: true });
});

test("auth, upload/reload, edit, render, billing, and publish creator journey", async ({ page }) => {
  await page.goto("/projects/new");
  await expect(page).toHaveURL(/\/login\?next=/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/projects\/new$/);

  await page.getByLabel("Project Name").fill("Phase 9 creator journey");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+$/);
  const projectId = page.url().split("/").pop();
  await expect(page.getByRole("heading", { name: "Upload your footage" })).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles(mediaPath);
  await expect(page.getByRole("button", { name: /^Upload$/ })).toBeEnabled();
  await page.getByRole("button", { name: /^Upload$/ }).click();
  await expect(page.getByText(/processing|transcribing/i).first()).toBeVisible();
  await page.reload();
  await expect(page.getByText(/processing|transcribing/i).first()).toBeVisible();

  const uploads = await rest("/uploaded_videos", {
    project_id: `eq.${projectId}`,
    select: "id",
    order: "created_at.desc",
    limit: "1",
  });
  const videoId = uploads[0].id;
  const preprocess = await internalJob(`preprocess--${videoId}`);
  await internalJob(`transcribe--${projectId}--${videoId}--${workspaceId}`);
  if (preprocess.visual_job_id) await internalJob(preprocess.visual_job_id);
  await api(`/projects/${projectId}/captions/generate`, {
    method: "POST",
    body: { transcript_id: preprocess.transcript_id, preset: "founder_clean" },
  });
  await api(`/projects/${projectId}/plans`, {
    method: "POST",
    body: { style: "founder", target_duration_s: 10, broll_density: "none" },
  });

  await page.reload();
  await expect(page.getByRole("button", { name: "Looks" })).toBeVisible();
  await page.getByRole("button", { name: "Looks" }).click();
  await page.getByLabel("Enable portrait backdrop blur").check();
  await expect(page.getByLabel("Enable portrait backdrop blur")).toBeChecked();

  const render = await api(`/projects/${projectId}/renders`, { method: "POST", body: {} });
  await internalJob(render.queue_job_id);
  await page.reload();
  await page.getByRole("button", { name: "Export" }).click();
  await expect(page.getByText("Ready to post")).toBeVisible();

  await page.goto("/billing");
  await expect(page.getByRole("heading", { name: /Billing|Plan/i }).first()).toBeVisible();
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("button", { name: "Export" }).click();
  await page.getByLabel("Account name").fill("Sandbox Creator");
  await page.getByLabel("Platform account ID").fill("sandbox-account");
  await page.getByLabel("Platform access token").fill("sandbox-token-secret");
  await page.getByRole("button", { name: "Connect account" }).click();
  await expect(page.getByText(/TikTok · Sandbox Creator/)).toBeVisible();
  await page.getByLabel("Post caption").fill("Published from the Phase 9 browser journey.");
  await page.getByRole("button", { name: "Publish" }).click();
  await expect(page.getByText(/publish queued/i)).toBeVisible();
});

async function internalJob(jobId) {
  const payload = await fetchJson(`${apiUrl}/internal/jobs/${encodeURIComponent(jobId)}/run`, {
    method: "POST",
    headers: { "x-motionly-worker-token": "e2e-worker-token" },
  });
  return payload.result;
}

async function api(path, { method = "GET", body } = {}) {
  return fetchJson(`${apiUrl}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${accessToken}`,
      "content-type": "application/json",
      "x-motionly-user-id": userId,
      "x-motionly-workspace-id": workspaceId,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function rest(path, query) {
  const url = new URL(`${supabaseUrl}/rest/v1${path}`);
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
  return fetchJson(url, { headers: serviceHeaders() });
}

async function providerFetch(path, { method, body } = {}) {
  return fetchJson(`${supabaseUrl}${path}`, {
    method,
    headers: { ...serviceHeaders(), "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  if (!response.ok) throw new Error(`${options?.method || "GET"} ${url}: ${response.status} ${text}`);
  return text ? JSON.parse(text) : {};
}

function serviceHeaders() {
  return { apikey: serviceKey, authorization: `Bearer ${serviceKey}` };
}

function assertEnvironment() {
  if (!anonKey || !serviceKey) throw new Error("Supabase E2E credentials are required.");
}

function createVideo(path) {
  const result = spawnSync("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
    "testsrc2=size=360x640:rate=24:duration=3", "-f", "lavfi", "-i",
    "sine=frequency=440:sample_rate=44100:duration=3", "-shortest", "-c:v",
    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", path,
  ], { encoding: "utf8" });
  if (result.status !== 0) throw new Error(result.stderr);
}
