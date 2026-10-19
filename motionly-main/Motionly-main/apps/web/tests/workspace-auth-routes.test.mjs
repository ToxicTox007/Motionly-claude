import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("workspace selection uses POST-safe redirects and surfaces recoverable errors", async () => {
  const [route, dashboard] = await Promise.all([
    source("app/app-api/workspaces/select/route.ts"),
    source("app/(app)/dashboard/page.tsx"),
  ]);

  assert.match(route, /NextResponse\.redirect\(redirectUrl, 303\)/);
  assert.doesNotMatch(route, /return NextResponse\.redirect\(redirectUrl\);/);
  assert.match(route, /workspace_unavailable/);

  const appShellImports = dashboard.match(
    /import \{ AppShell \} from "@\/components\/app-shell";/g,
  );
  assert.equal(appShellImports?.length, 1);
  assert.match(
    dashboard,
    /searchParams\?: Promise<Record<string, string \| string\[\] \| undefined>>/,
  );
  assert.match(dashboard, /error=\{workspaceError\}/);
});

test("auth callback redirects failed exchanges to login and only succeeds after exchange", async () => {
  const callback = await source("app/auth/callback/route.ts");

  assert.match(callback, /callback_missing/);
  assert.match(callback, /callback_failed/);
  assert.match(callback, /exchangeCodeForSession\(code\)/);
  assert.match(
    callback,
    /NextResponse\.redirect\(publicUrl\("\/dashboard"\), 303\)/,
  );
});
