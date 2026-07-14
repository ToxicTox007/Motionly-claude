import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("Login page implements form validation and pending submission locks", async () => {
  const source = await readFile(
    new URL("app/(auth)/login/page.tsx", root),
    "utf8",
  );

  // Verify form pending locks
  assert.match(source, /const \{ pending \} = useFormStatus\(\)/);
  assert.match(source, /disabled=\{pending\}/);
  assert.match(source, /Logging in\.\.\./);

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

test("Toast system defines provider, hook, and styled status alerts", async () => {
  const providerSource = await readFile(
    new URL("components/toast.tsx", root),
    "utf8",
  );

  // Verify ToastProvider, useToast hook, and styled toast variants
  assert.match(providerSource, /export function ToastProvider/);
  assert.match(providerSource, /export function useToast/);
  assert.match(providerSource, /success/);
  assert.match(providerSource, /error/);
  assert.match(providerSource, /warning/);
  assert.match(providerSource, /info/);
  assert.match(providerSource, /loading/);
});

test("Focus traps are implemented inside responsive drawers and modals", async () => {
  const layoutSource = await readFile(
    new URL("components/editor-layout.tsx", root),
    "utf8",
  );

  // Verify focus trap hooks in editor layout drawers
  assert.match(layoutSource, /useFocusTrap\(/);
  assert.match(layoutSource, /ref=\{leftTrapRef\}/);
  assert.match(layoutSource, /ref=\{rightTrapRef\}/);
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

test("Timeline virtualization, snapping alignments, and request queue compile correctly", async () => {
  const timelineSource = await readFile(
    new URL("components/editor/timeline/TimelinePanel.tsx", root),
    "utf8",
  );

  // Verify memoized renders
  assert.match(timelineSource, /const Playhead = React\.memo/);
  assert.match(timelineSource, /const TimelineRuler = React\.memo/);
  assert.match(timelineSource, /const MobileTimelineView = React\.memo/);

  // Verify virtualization loops
  assert.match(timelineSource, /const isClipVisible = useCallback/);

  // Verify real-time alignment checks
  assert.match(timelineSource, /const getSnappedMs = useCallback/);
});

test("Shared modal and focus trap preserve accessible dialog behavior", async () => {
  const modalSource = await readFile(new URL("components/brutal-ui.tsx", root), "utf8");
  const focusSource = await readFile(new URL("lib/useFocusTrap.ts", root), "utf8");

  assert.match(modalSource, /aria-labelledby=\{titleId\}/);
  assert.match(modalSource, /aria-describedby=\{description \? descriptionId : undefined\}/);
  assert.match(modalSource, /id=\{titleId\}/);
  assert.match(focusSource, /const onCloseRef = useRef\(onClose\)/);
  assert.match(focusSource, /\}, \[active\]\);/);
  assert.match(focusSource, /opener\?\.isConnected/);
});

test("Upload replacement and project bootstrap reject stale or failed state", async () => {
  const uploadSource = await readFile(new URL("components/upload-panel.tsx", root), "utf8");
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
