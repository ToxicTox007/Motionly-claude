import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.test" } }));
const api = vi.hoisted(() => ({ editorApiRequest: vi.fn() }));
vi.mock("@/lib/client-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/client-api")>()),
  editorApiRequest: api.editorApiRequest,
}));
const clipboard = vi.hoisted(() => ({
  copyText: vi.fn(async (_text: string) => ({ ok: true as boolean })),
}));
vi.mock("@/lib/clipboard", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/clipboard")>()),
  copyText: (text: string) => clipboard.copyText(text),
}));

const { ReviewPanel } = await import("@/components/editor/ReviewPanel");

const review = {
  id: "rev-1",
  edit_plan_id: "plan-1",
  edit_plan_revision: 1,
  uploaded_video_id: "up-1",
  source_media_revision: 1,
  source_object_reference_id: "ref-1",
  source_sha256: "sha",
  status: "open",
  status_revision: 1,
  decision_reason: null,
  comments: [],
  created_at: "2026-10-01T00:00:00Z",
};

beforeEach(() => {
  clipboard.copyText.mockReset();
  api.editorApiRequest.mockReset();
  api.editorApiRequest.mockImplementation(async (url: string) => {
    if (url.endsWith("/reviews?limit=1")) return { reviews: [review] };
    if (url.endsWith("/share-links")) {
      return {
        url: "/review/share-1#secret-token",
        expires_at: "2026-11-01T00:00:00Z",
      };
    }
    return review;
  });
});

function renderPanel() {
  render(
    <ReviewPanel
      projectId="proj-1"
      workspaceId="ws-1"
      activePlan={null}
      mediaRevision={1}
      uploadedVideoId="up-1"
      sourceObjectReferenceId="ref-1"
      sourceSha256="sha"
      playheadMs={0}
      onSeek={() => {}}
      onProposalCreated={() => {}}
    />,
  );
}

describe("ReviewPanel share link", () => {
  it("says copied only when the copy worked", async () => {
    clipboard.copyText.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: /Copy 7-day link/ }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      /Review link copied/,
    );
    expect(clipboard.copyText).toHaveBeenCalledWith(
      `${window.location.origin}/review/share-1#secret-token`,
    );
  });

  it("shows the link for copying by hand when the browser refuses", async () => {
    clipboard.copyText.mockResolvedValue({ ok: false });
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: /Copy 7-day link/ }),
    );
    expect(
      await screen.findByText(/not copied automatically/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Review link copied/)).toBeNull();
    expect(
      screen.getByText(`${window.location.origin}/review/share-1#secret-token`),
    ).toBeInTheDocument();
  });
});
