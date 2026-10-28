import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("Login page implements form validation and pending submission locks", async () => {
  const source = await readFile(
    new URL("app/(auth)/login/page.tsx", root),
    "utf8",
  );

  // The shared SubmitButton locks the form while it runs (behaviour:
  // tests/components/auth-forms.test.tsx "login submit").
  assert.match(source, /<SubmitButton/);
  assert.match(source, /pendingLabel="Logging in\.\.\."/);

  // Verify inline validation logic
  assert.match(source, /const validateEmail =/);
  assert.match(source, /const validatePassword =/);
  assert.match(source, /emailError/);
  assert.match(source, /passwordError/);
  assert.match(source, /onSubmit=\{handleSubmit\}/);
  assert.match(
    source,
    /aria-describedby=\{emailError \? "email-error" : undefined\}/,
  );
});

test("Keyboard controls and visible focus indicators exist on UI elements", async () => {
  const uiSource = await readFile(
    new URL("components/editor/ui.tsx", root),
    "utf8",
  );

  // Verify accessibility properties like aria-label, focus rings, and sliders keyboard controls
  assert.match(uiSource, /focus-visible:ring-2/);
  assert.match(uiSource, /type="range"/);
  assert.match(uiSource, /aria-valuemin/);
});

// Toasts, the editor's mobile sheets and timeline (snapping, virtualisation)
// and the shared Modal and focus trap are tested as behaviour in Vitest:
// tests/components/{toast,editor-workspaces,modal}.test.tsx and
// tests/unit/timeline-geometry.test.ts.

test("Upload replacement and project bootstrap reject stale or failed state", async () => {
  const uploadSource = await readFile(
    new URL("components/upload-panel.tsx", root),
    "utf8",
  );
  const projectSource = await readFile(
    new URL("app/(app)/projects/[projectId]/page.tsx", root),
    "utf8",
  );

  assert.match(uploadSource, /const operationIdRef = useRef\(0\)/);
  assert.match(uploadSource, /if \(isWorking\) return/);
  assert.match(uploadSource, /if \(!isCurrentOperation\(\)\) return/);
  assert.match(uploadSource, /abortRef\.current\?\.abort\(\)/);
  assert.match(projectSource, /if \(response\.status === 404\) return null/);
  assert.match(projectSource, /throw new Error\(/);
  assert.doesNotMatch(projectSource, /if \(!response\.ok\) return null/);
});
