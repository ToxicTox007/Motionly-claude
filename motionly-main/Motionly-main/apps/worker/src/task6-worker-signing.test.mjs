import assert from "node:assert/strict";
import test from "node:test";

import { assembleRenderProps, signReferenceAtExecution } from "./remotion.mjs";

test("Remotion signs durable source references immediately before use", async () => {
  process.env.WORKER_API_BASE_URL = "https://api.motionly.test/";
  process.env.WORKER_API_TOKEN = "worker-secret";
  const referenceId = "86000000-0000-4000-8000-000000000001";
  const observed = {};

  try {
    const signed = await signReferenceAtExecution(
      referenceId,
      { workspaceId: "workspace-a", projectId: "project-a" },
      {
        fetchImpl: async (url, options) => {
          observed.url = url;
          observed.options = options;
          return new Response(
            JSON.stringify({
              signed_urls: {
                [referenceId]:
                  "https://storage.example.test/source.mp4?token=short-lived",
              },
              ttl_seconds: 7200,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        },
      },
    );

    assert.equal(
      signed,
      "https://storage.example.test/source.mp4?token=short-lived",
    );
    assert.equal(
      observed.url,
      "https://api.motionly.test/internal/media/sign-urls",
    );
    assert.equal(
      observed.options.headers["x-motionly-worker-token"],
      "worker-secret",
    );
    assert.deepEqual(JSON.parse(observed.options.body), {
      workspace_id: "workspace-a",
      project_id: "project-a",
      reference_ids: [referenceId],
    });
  } finally {
    delete process.env.WORKER_API_BASE_URL;
    delete process.env.WORKER_API_TOKEN;
  }
});

test("Remotion refuses signing without tenant scope", async () => {
  await assert.rejects(
    signReferenceAtExecution("reference-id", null, {
      fetchImpl: async () => assert.fail("signer must not be called"),
    }),
    /reference and tenant scope/,
  );
});

test("Remotion rejects uploaded sources without a durable reference", async () => {
  await assert.rejects(
    assembleRenderProps(
      {
        sourceVideo: {
          src: "supabase://raw-uploads/workspaces/workspace-a/projects/project-a/raw/source.mp4",
        },
      },
      null,
      { workspaceId: "workspace-a", projectId: "project-a" },
    ),
    (error) => error.code === "durable_media_reference_missing",
  );
});
