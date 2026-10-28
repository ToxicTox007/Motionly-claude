import { act, renderHook } from "@testing-library/react";
import type { TranscriptStatus, TranscriptSummary } from "@motionly/shared";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn(
  async (_url: string, _init?: RequestInit, _auth?: unknown) =>
    new Response(JSON.stringify({ status: "processing" }), { status: 200 }),
);

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.test" } }));
vi.mock("@/lib/client-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/client-api")>()),
  apiFetch: (url: string, init?: RequestInit, auth?: unknown) =>
    apiFetch(url, init, auth),
}));

const { useTranscriptPolling } = await import("@/hooks/useTranscriptPolling");

type Props = {
  uploadId: string | null;
  uploadReady: boolean;
  transcriptStatus: TranscriptStatus | null;
};

function setup(initial: Props) {
  const onTranscript = vi.fn<(next: TranscriptSummary | null) => void>();
  const view = renderHook(
    (props: Props) =>
      useTranscriptPolling({
        projectId: "proj-1",
        workspaceId: "ws-1",
        onTranscript,
        ...props,
      }),
    { initialProps: initial },
  );
  return { ...view, onTranscript };
}

async function elapse(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  apiFetch.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useTranscriptPolling", () => {
  it.each(["ready", "failed", "captions_unavailable"] as const)(
    "does not poll a transcript that is %s",
    async (status) => {
      setup({ uploadId: "up-1", uploadReady: true, transcriptStatus: status });
      await elapse(60_000);
      expect(apiFetch).not.toHaveBeenCalled();
    },
  );

  it("waits for the upload to be ready", async () => {
    setup({ uploadId: "up-1", uploadReady: false, transcriptStatus: null });
    await elapse(60_000);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("polls a pending transcript and hands each result back", async () => {
    const { onTranscript } = setup({
      uploadId: "up-1",
      uploadReady: true,
      transcriptStatus: "queued",
    });
    await elapse(0);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe(
      "http://api.test/projects/proj-1/transcript",
    );
    expect(onTranscript).toHaveBeenCalledWith({ status: "processing" });
  });

  it("keeps one steady poller while the status moves between pending states", async () => {
    const { rerender } = setup({
      uploadId: "up-1",
      uploadReady: true,
      transcriptStatus: "queued",
    });
    await elapse(0);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    rerender({
      uploadId: "up-1",
      uploadReady: true,
      transcriptStatus: "processing",
    });
    await elapse(100);
    // No restart, so no immediate second request.
    expect(apiFetch).toHaveBeenCalledTimes(1);
    await elapse(4900);
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });

  it("stops after the server reports a failed transcript", async () => {
    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "failed" }), { status: 200 }),
    );
    const { onTranscript } = setup({
      uploadId: "up-1",
      uploadReady: true,
      transcriptStatus: "processing",
    });
    await elapse(0);
    expect(onTranscript).toHaveBeenCalledWith({ status: "failed" });
    await elapse(60_000);
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it("reports a missing transcript as null", async () => {
    apiFetch.mockResolvedValueOnce(new Response(null, { status: 404 }));
    const { onTranscript } = setup({
      uploadId: "up-1",
      uploadReady: true,
      transcriptStatus: null,
    });
    await elapse(0);
    expect(onTranscript).toHaveBeenCalledWith(null);
  });
});
