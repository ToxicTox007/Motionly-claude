import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const webDir = resolve(__dirname, "..");

test("production mode + SKIP_AUTH_MIDDLEWARE=true fails validation", () => {
  const result = spawnSync(
    "node",
    ["--experimental-strip-types", "-e", "import('./lib/env.ts')"],
    {
      cwd: webDir,
      env: {
        ...process.env,
        NODE_ENV: "production",
        NEXT_PUBLIC_APP_URL: "https://motionly.video",
        SKIP_AUTH_MIDDLEWARE: "true",
        NEXT_PUBLIC_API_URL: "https://api.motionly.video",
        NEXT_PUBLIC_SUPABASE_URL: "https://supabase.co",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon_key",
      },
    }
  );
  
  assert.equal(result.status, 1);
  const output = result.stderr.toString() + result.stdout.toString();
  assert.match(output, /Security violation: SKIP_AUTH_MIDDLEWARE and NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE are not allowed in production/);
});

test("production mode + NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE=true fails validation", () => {
  const result = spawnSync(
    "node",
    ["--experimental-strip-types", "-e", "import('./lib/env.ts')"],
    {
      cwd: webDir,
      env: {
        ...process.env,
        NODE_ENV: "production",
        NEXT_PUBLIC_APP_URL: "https://motionly.video",
        NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE: "true",
        NEXT_PUBLIC_API_URL: "https://api.motionly.video",
        NEXT_PUBLIC_SUPABASE_URL: "https://supabase.co",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon_key",
      },
    }
  );
  
  assert.equal(result.status, 1);
  const output = result.stderr.toString() + result.stdout.toString();
  assert.match(output, /Security violation: SKIP_AUTH_MIDDLEWARE and NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE are not allowed in production/);
});

test("production mode + normal auth configuration passes validation", () => {
  const result = spawnSync(
    "node",
    ["--experimental-strip-types", "-e", "import('./lib/env.ts')"],
    {
      cwd: webDir,
      env: {
        ...process.env,
        NODE_ENV: "production",
        NEXT_PUBLIC_APP_URL: "https://motionly.video",
        NEXT_PUBLIC_API_URL: "https://api.motionly.video",
        NEXT_PUBLIC_SUPABASE_URL: "https://supabase.co",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon-key-here",
        SKIP_AUTH_MIDDLEWARE: "false",
        NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE: "false",
      },
    }
  );
  
  const output = result.stderr.toString() + result.stdout.toString();
  assert.equal(result.status, 0, `Expected success but failed with: ${output}`);
});

test("local/development mode with bypass enabled passes validation", () => {
  const result = spawnSync(
    "node",
    ["--experimental-strip-types", "-e", "import('./lib/env.ts')"],
    {
      cwd: webDir,
      env: {
        ...process.env,
        NODE_ENV: "development",
        NEXT_PUBLIC_APP_URL: "http://localhost:3000",
        NEXT_PUBLIC_API_URL: "http://localhost:8000",
        NEXT_PUBLIC_SUPABASE_URL: "http://localhost:54321",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon-key-here",
        SKIP_AUTH_MIDDLEWARE: "true",
      },
    }
  );
  
  const output = result.stderr.toString() + result.stdout.toString();
  assert.equal(result.status, 0, `Expected success but failed with: ${output}`);
});
