import { act, render } from "@testing-library/react";
import { Profiler, type ReactNode } from "react";
import type { UploadedVideoSummary } from "@motionly/shared";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

// Render counts for the editor while the playhead moves (FE-15). The heavy
// children are replaced by counting stubs; the editor shell, history,
// revision tracking, layout and inspector are real.

const counts = vi.hoisted(() => ({
  toolbar: 0,
  tabs: 0,
  timeline: 0,
  preview: 0,
  timelinePlayhead: [] as number[],
  previewPlayhead: [] as number[],
  setPlayheadMs: null as null | ((ms: number) => void),
  onClipSelect: null as null | ((id: string | null) => void),
}));
const recordToolbarRender = vi.hoisted(() => () => {
  counts.toolbar += 1;
});

vi.mock("next/navigation", () => navigationMock);
vi.mock("next/dynamic", () => ({
  default: () =>
    function DynamicStub() {
      return null;
    },
}));
vi.mock("@/app/(auth)/actions", () => ({ logout: vi.fn() }));
vi.mock("@/lib/env", () => ({
  env: { apiBaseUrl: "http://api.test", supabaseUrl: "http://sb.test" },
}));
vi.mock("@/hooks/useWorkflowStatus", () => {
  const value = {
    workflow: null,
    error: null,
    pendingCommand: null,
    transport: "test",
    refresh: async () => null,
    runCommand: async () => null,
  };
  return { useWorkflowStatus: () => value };
});
vi.mock("@/hooks/useTranscriptPolling", () => ({
  useTranscriptPolling: () => {},
}));
// Memoized like the real toolbar, so this counts whether the editor hands it
// stable props.
vi.mock("@/components/editor/layout/EditorToolbar", async () => {
  const { memo } = await import("react");
  return {
    EditorToolbar: memo(function ToolbarStub() {
      recordToolbarRender();
      return <div data-testid="toolbar" />;
    }),
  };
});
vi.mock("@/components/editor/layout/InspectorTabs", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("@/components/editor/layout/InspectorTabs")
    >();
  return {
    ...actual,
    InspectorTabList: (
      props: Parameters<typeof actual.InspectorTabList>[0],
    ) => {
      counts.tabs += 1;
      return <actual.InspectorTabList {...props} />;
    },
  };
});
vi.mock("@/components/editor/timeline/TimelinePanel", () => ({
  TimelinePanel: (props: {
    playheadMs: number;
    setPlayheadMs: (ms: number) => void;
    onClipSelect: (id: string | null) => void;
  }) => {
    counts.timeline += 1;
    counts.timelinePlayhead.push(props.playheadMs);
    counts.setPlayheadMs = props.setPlayheadMs;
    counts.onClipSelect = props.onClipSelect;
    return <div data-testid="timeline">{props.playheadMs}</div>;
  },
}));
vi.mock("@/components/editor/preview/VideoPreview", () => ({
  VideoPreview: (props: { playheadMs: number }) => {
    counts.preview += 1;
    counts.previewPlayhead.push(props.playheadMs);
    return <div data-testid="preview" />;
  },
}));

const { EditorClient } = await import("@/components/editor-client");
const { ToastProvider } = await import("@/components/toast");

const upload: UploadedVideoSummary = {
  id: "up-1",
  projectId: "proj-1",
  workspaceId: "ws-1",
  originalFilename: "clip.mp4",
  status: "ready",
  durationMs: 30_000,
  width: 1920,
  height: 1080,
  fps: 30,
  mediaRevision: 1,
  sourceObjectReferenceId: null,
  verifiedSha256: null,
  thumbnailPath: null,
  warnings: [],
  updatedAt: "2026-01-01T00:00:00Z",
};

function editor(): ReactNode {
  return (
    <EditorClient
      projectId="proj-1"
      workspaceId="ws-1"
      userId="user-1"
      title="Launch reel"
      upload={upload}
      uploadAssetUrls={{ source_video_url: null, thumbnail_url: null }}
      transcript={null}
      captionTracks={[]}
      editPlans={[]}
      activePlanId={null}
      brollSlots={[]}
      musicSelection={null}
      effectsConfig={null}
      renderJobs={[]}
      exports={[]}
      maxExportResolution="720p"
      visualStatus={{ status: "unavailable", message: "n/a" }}
      initialWorkflow={null}
    />
  );
}

function useDesktop(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query === "(min-width: 1024px)" ? matches : false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

function reset() {
  counts.toolbar = 0;
  counts.tabs = 0;
  counts.timeline = 0;
  counts.preview = 0;
  counts.timelinePlayhead = [];
  counts.previewPlayhead = [];
}

beforeEach(() => {
  resetNextMocks();
  reset();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 200 })),
  );
  useDesktop(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("editor render counts during playback", () => {
  it("20 playhead updates re-render only the timeline and the preview", () => {
    let editorCommits = 0;
    render(
      <ToastProvider>
        <Profiler id="editor" onRender={() => (editorCommits += 1)}>
          {editor()}
        </Profiler>
      </ToastProvider>,
    );
    expect(counts.setPlayheadMs).not.toBeNull();
    const before = { ...counts };
    const commitsBefore = editorCommits;

    for (let step = 1; step <= 20; step += 1) {
      act(() => counts.setPlayheadMs?.(step * 250));
    }

    const delta = {
      commits: editorCommits - commitsBefore,
      toolbar: counts.toolbar - before.toolbar,
      tabs: counts.tabs - before.tabs,
      timeline: counts.timeline - before.timeline,
      preview: counts.preview - before.preview,
    };
    // Recorded for the PR's before/after table.
    console.info("editor render counts for 20 playhead updates", delta);

    expect(delta.toolbar).toBe(0);
    expect(delta.tabs).toBe(0);
    expect(delta.timeline).toBe(20);
    expect(delta.preview).toBe(20);
    expect(counts.timelinePlayhead.at(-1)).toBe(5000);
    expect(counts.previewPlayhead.at(-1)).toBe(5000);
  });

  it("selecting a clip does not re-render the toolbar", () => {
    render(<ToastProvider>{editor()}</ToastProvider>);
    const before = counts.toolbar;
    act(() => counts.onClipSelect?.("clip-1"));
    act(() => counts.onClipSelect?.("clip-2"));
    expect(counts.toolbar - before).toBe(0);
  });
});
