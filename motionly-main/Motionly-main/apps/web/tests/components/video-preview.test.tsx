import { DndContext } from "@dnd-kit/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";
import { VideoPreview } from "@/components/editor/preview/VideoPreview";

function preview(
  sourceUrl: string | null,
  videoRef = createRef<HTMLVideoElement>(),
) {
  return (
    <DndContext>
      <VideoPreview
        videoRef={videoRef}
        sourceUrl={sourceUrl}
        thumbnailUrl={null}
        playheadMs={0}
        captionSegments={[]}
        captionStyle={null}
        totalDurationMs={10_000}
      />
    </DndContext>
  );
}

describe("VideoPreview play state", () => {
  it("follows play, pause and ended on the video element", () => {
    render(preview("https://media.test/source.mp4"));
    const video = screen.getByLabelText("Source video preview");
    fireEvent.play(video);
    expect(
      screen.getByRole("button", { name: "Pause preview video" }),
    ).toBeInTheDocument();
    fireEvent.pause(video);
    expect(
      screen.getByRole("button", { name: "Play preview video" }),
    ).toBeInTheDocument();
    fireEvent.play(video);
    fireEvent.ended(video);
    expect(
      screen.getByRole("button", { name: "Play preview video" }),
    ).toBeInTheDocument();
  });

  it("still tracks play state when the video arrives after the first render", () => {
    const videoRef = createRef<HTMLVideoElement>();
    const { rerender } = render(preview(null, videoRef));
    rerender(preview("https://media.test/source.mp4", videoRef));
    const video = screen.getByLabelText("Source video preview");
    fireEvent.play(video);
    expect(
      screen.getByRole("button", { name: "Pause preview video" }),
    ).toBeInTheDocument();
  });
});
