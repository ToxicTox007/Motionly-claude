import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

const workerDirectory = resolve(import.meta.dirname, "..");
const rendererDirectory = resolve(workerDirectory, "../renderer");

function runNode(arguments_, environment) {
  return spawnSync(process.execPath, arguments_, {
    cwd: workerDirectory,
    encoding: "utf8",
    env: environment,
  });
}

test("Compose-style FILE secrets start Remotion workers and authenticate Remotion signing", async (t) => {
  const secretDirectory = await mkdtemp(join(tmpdir(), "motionly-worker-secrets-"));
  t.after(() => rm(secretDirectory, { force: true, recursive: true }));

  const serviceRoleKeyFile = join(secretDirectory, "supabase_service_role_key");
  const workerTokenFile = join(secretDirectory, "worker_api_token");
  await writeFile(serviceRoleKeyFile, "file-backed-service-role-key\n", "utf8");
  await writeFile(workerTokenFile, "file-backed-worker-token\n", "utf8");

  const environment = {
    APP_ENV: "production",
    BROWSER_EXECUTABLE_PATH: process.execPath,
    PATH: process.env.PATH || "",
    REDIS_URL: "redis://127.0.0.1:6379/0",
    REMOTION_ENABLED: "true",
    RENDERER_BASE_PATH: rendererDirectory,
    SUPABASE_SERVICE_ROLE_KEY_FILE: serviceRoleKeyFile,
    SUPABASE_URL: "https://supabase.example.invalid",
    WORKER_API_BASE_URL: "https://api.example.invalid",
    WORKER_API_TOKEN_FILE: workerTokenFile,
    WORKER_QUEUES: "render_remotion",
  };

  const startup = runNode(["src/worker.mjs", "--check"], environment);
  assert.equal(startup.status, 0, startup.stderr || startup.stdout);
  assert.match(startup.stdout, /Remotion renderer prerequisites are present/);

  const signing = runNode(
    [
      "--input-type=module",
      "--eval",
      `
        import { signReferenceAtExecution } from "./src/remotion.mjs";
        const signed = await signReferenceAtExecution(
          "reference-id",
          { workspaceId: "workspace-id", projectId: "project-id" },
          {
            fetchImpl: async (_url, options) => {
              if (options.headers["x-motionly-worker-token"] !== "file-backed-worker-token") {
                throw new Error("Remotion did not read WORKER_API_TOKEN_FILE");
              }
              return new Response(
                JSON.stringify({
                  signed_urls: { "reference-id": "https://storage.example.invalid/source.mp4" },
                  ttl_seconds: 300,
                }),
                { status: 200, headers: { "content-type": "application/json" } },
              );
            },
          },
        );
        if (signed !== "https://storage.example.invalid/source.mp4") {
          throw new Error("unexpected signed URL");
        }
      `,
    ],
    environment,
  );
  assert.equal(signing.status, 0, signing.stderr || signing.stdout);
});
