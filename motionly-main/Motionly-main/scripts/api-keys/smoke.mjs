#!/usr/bin/env node
// Task 24 — API-key smoke script.
//
// End-to-end proof that an Agency-plan `mtly_` API key can drive the documented
// automation flow and that revocation takes effect immediately:
//
//   1. A key is created (as an owner/admin, via BASE_URL + an owner bearer).
//   2. The KEY (not the owner session) creates a project.
//   3. The KEY uploads a small file through the resumable multipart endpoints,
//      pushing bytes through the CDN proxy / signed URLs the API returns.
//   4. The KEY is revoked.
//   5. The revoked key is rejected with 401 on its next request.
//
// Written for the phase checkpoint; NOT run here (CONTEXT: don't start the full
// CPU stack). Run it against a live stack with:
//
//   BASE_URL=https://localhost:8443 \
//   OWNER_BEARER=<supabase access token of an agency owner/admin> \
//   WORKSPACE_ID=<agency workspace id> \
//   node scripts/api-keys/smoke.mjs
//
// The CDN origin is the single public origin: `/api` is the FastAPI app.

import { Buffer } from "node:buffer";

const BASE_URL = (process.env.BASE_URL || "https://localhost:8443").replace(
  /\/$/,
  "",
);
const OWNER_BEARER = process.env.OWNER_BEARER;
const WORKSPACE_ID = process.env.WORKSPACE_ID;

function required(name, value) {
  if (!value) {
    console.error(`Missing required env ${name}. See the header of this file.`);
    process.exit(2);
  }
  return value;
}

async function json(response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    return { raw: text };
  }
}

function ownerHeaders() {
  return {
    authorization: `Bearer ${OWNER_BEARER}`,
    "content-type": "application/json",
    "x-motionly-workspace-id": WORKSPACE_ID,
  };
}

function keyHeaders(apiKey) {
  return {
    authorization: `Bearer ${apiKey}`,
    "content-type": "application/json",
    "x-motionly-workspace-id": WORKSPACE_ID,
  };
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(`Smoke assertion failed: ${message}`);
  }
}

async function main() {
  required("OWNER_BEARER", OWNER_BEARER);
  required("WORKSPACE_ID", WORKSPACE_ID);

  // 1. Create a key as the owner/admin.
  const createRes = await fetch(`${BASE_URL}/api/api-keys`, {
    method: "POST",
    headers: ownerHeaders(),
    body: JSON.stringify({ name: `smoke-${Date.now()}` }),
  });
  const created = await json(createRes);
  assert(createRes.status === 201, `create key -> ${createRes.status}`);
  const apiKey = created.secret;
  const keyId = created.key?.id;
  assert(apiKey?.startsWith("mtly_"), "secret is an mtly_ key");
  assert(keyId, "created key has an id");
  console.log(`✓ created key ${created.key.prefix}…${created.key.last4}`);

  // 2. The KEY creates a project.
  const projectRes = await fetch(`${BASE_URL}/api/projects`, {
    method: "POST",
    headers: keyHeaders(apiKey),
    body: JSON.stringify({ title: "API key smoke project" }),
  });
  const project = await json(projectRes);
  assert(projectRes.status === 201, `create project -> ${projectRes.status}`);
  const projectId = project.id;
  assert(projectId, "project has an id");
  console.log(`✓ key created project ${projectId}`);

  // 3. The KEY runs a resumable upload (init -> part -> complete) through the
  //    signed URLs the API returns (served via the CDN proxy / local storage).
  const payload = Buffer.from("motionly-api-key-smoke-video");
  const initRes = await fetch(
    `${BASE_URL}/api/projects/${projectId}/uploads/multipart/init`,
    {
      method: "POST",
      headers: keyHeaders(apiKey),
      body: JSON.stringify({
        file_name: "smoke.mp4",
        content_type: "video/mp4",
        total_size: payload.length,
        part_size: payload.length,
      }),
    },
  );
  const session = await json(initRes);
  assert(initRes.status === 201, `upload init -> ${initRes.status}`);
  const sessionId = session.session_id ?? session.id;
  const part = (session.parts ?? [])[0] ?? session.next_part ?? {};
  const signedUrl = part.url ?? part.signed_url;
  assert(sessionId, "upload session has an id");
  assert(signedUrl, "init returned a signed URL for part 1");

  // Push the bytes to the signed URL (the CDN proxy / storage origin).
  const putUrl = signedUrl.startsWith("http")
    ? signedUrl
    : `${BASE_URL}${signedUrl}`;
  const putRes = await fetch(putUrl, {
    method: "PUT",
    headers: { "content-type": "video/mp4" },
    body: payload,
  });
  assert(putRes.ok, `PUT part to CDN proxy -> ${putRes.status}`);
  const etag = putRes.headers.get("etag") ?? part.etag ?? "etag";

  const partNumber = part.part_number ?? 1;
  await fetch(
    `${BASE_URL}/api/projects/${projectId}/uploads/multipart/${sessionId}/parts/${partNumber}/complete`,
    {
      method: "POST",
      headers: keyHeaders(apiKey),
      body: JSON.stringify({ etag }),
    },
  );
  const completeRes = await fetch(
    `${BASE_URL}/api/projects/${projectId}/uploads/multipart/${sessionId}/complete`,
    {
      method: "POST",
      headers: keyHeaders(apiKey),
      body: JSON.stringify({ parts: [{ part_number: partNumber, etag }] }),
    },
  );
  assert(completeRes.ok, `upload complete -> ${completeRes.status}`);
  console.log("✓ key uploaded a file through the resumable/CDN path");

  // 4. Revoke the key (as owner/admin).
  const revokeRes = await fetch(`${BASE_URL}/api/api-keys/${keyId}/revoke`, {
    method: "POST",
    headers: ownerHeaders(),
  });
  assert(revokeRes.ok, `revoke -> ${revokeRes.status}`);
  console.log("✓ key revoked");

  // 5. The revoked key is rejected immediately.
  const afterRes = await fetch(`${BASE_URL}/api/projects`, {
    headers: keyHeaders(apiKey),
  });
  assert(
    afterRes.status === 401,
    `revoked key still works (expected 401, got ${afterRes.status})`,
  );
  const afterBody = await json(afterRes);
  assert(
    afterBody.error?.code === "api_key_invalid",
    `revoked key error code: ${afterBody.error?.code}`,
  );
  console.log("✓ revoked key is rejected with 401 api_key_invalid");

  console.log("\nAPI-key smoke flow passed.");
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
