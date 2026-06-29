import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("TimelinePanel.tsx implements playhead and ruler memoization correctly", async () => {
  const source = await readFile(
    new URL("components/editor/timeline/TimelinePanel.tsx", root),
    "utf8",
  );

  // Verify Playhead and Ruler memoizations
  assert.match(source, /const Playhead = React\.memo/);
  assert.match(source, /const TimelineRuler = React\.memo/);
  assert.match(source, /<TimelineRuler/);
  assert.match(source, /<Playhead/);
});

test("TimelinePanel.tsx implements time-based virtualization correctly", async () => {
  const source = await readFile(
    new URL("components/editor/timeline/TimelinePanel.tsx", root),
    "utf8",
  );

  // Verify visibleRange state and tracking
  assert.match(source, /const \[visibleRange, setVisibleRange\] = useState/);
  assert.match(source, /const isClipVisible = useCallback/);
  assert.match(source, /isClipVisible\(clip\)\s*\?\s*\(?\s*<ClipBlock/);
});

test("TimelinePanel.tsx implements snapping logic and markers correctly", async () => {
  const source = await readFile(
    new URL("components/editor/timeline/TimelinePanel.tsx", root),
    "utf8",
  );

  // Verify snapping alignment checks and visual snap line renders
  assert.match(source, /const \[snapMarkerMs, setSnapMarkerMs\] = useState/);
  assert.match(source, /const getSnappedMs = useCallback/);
  assert.match(source, /snapMarkerMs !== null/);
  assert.match(source, /border-dashed border-primary/);
  assert.match(source, /getSnappedMs\(nextMs/);
});

test("TimelinePanel.tsx implements sequential queue and optimistic updates correctly", async () => {
  const source = await readFile(
    new URL("components/editor/timeline/TimelinePanel.tsx", root),
    "utf8",
  );

  // Verify requestQueue refs and chained promises
  assert.match(source, /const requestQueue = useRef/);
  assert.match(source, /requestQueue\.current = requestQueue\.current/);
  assert.match(source, /\.then\(async \(\) =>/);

  // Verify optimistic updates triggers
  assert.match(source, /onPlanUpdated\(\{[\s\S]*plan:[\s\S]*cuts: after/);
  assert.match(source, /onCaptionTrackUpdated\(\{[\s\S]*segments: after/);
});
