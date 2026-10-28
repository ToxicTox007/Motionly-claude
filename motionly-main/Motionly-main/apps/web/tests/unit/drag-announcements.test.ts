import type { Active, Over } from "@dnd-kit/core";
import { describe, expect, it } from "vitest";
import {
  editorDragAnnouncements,
  editorDragInstructions,
} from "@/components/editor/dnd/announcements";

function active(data: Record<string, unknown>, id = "palette:word-pop") {
  return { id, data: { current: data } } as unknown as Active;
}

function over(id: string, data: Record<string, unknown> = {}) {
  return { id, data: { current: data }, disabled: false } as unknown as Over;
}

const wordPop = active({
  kind: "palette",
  element: { name: "Word Pop", accepts: ["canvas", "timeline", "clip"] },
});
const placedLogo = active(
  { kind: "placed", element: { name: "Logo" } },
  "placed:logo",
);

describe("editor drag announcements", () => {
  it("tells keyboard users how to pick up, move, drop and cancel", () => {
    const text = editorDragInstructions.draggable;
    expect(text).toMatch(/Space or Enter/);
    expect(text).toMatch(/arrow keys/);
    expect(text).toMatch(/Escape/);
  });

  it("names the element that was picked up", () => {
    expect(editorDragAnnouncements.onDragStart({ active: wordPop })).toBe(
      "Picked up Word Pop.",
    );
    expect(editorDragAnnouncements.onDragStart({ active: placedLogo })).toBe(
      "Picked up Logo.",
    );
  });

  it("describes drop targets in words instead of internal ids", () => {
    const { onDragOver } = editorDragAnnouncements;
    expect(
      onDragOver({ active: wordPop, over: over("canvas", { zone: "canvas" }) }),
    ).toBe("Word Pop is over the video preview.");
    expect(
      onDragOver({
        active: wordPop,
        over: over("track:broll", { zone: "timeline", trackKind: "broll" }),
      }),
    ).toBe("Word Pop is over the B-roll track.");
    expect(
      onDragOver({
        active: wordPop,
        over: over("clip:c1", { zone: "clip", clip: { label: "Intro" } }),
      }),
    ).toBe("Word Pop is over the clip Intro.");
    expect(
      onDragOver({
        active: wordPop,
        over: over("transition:1", {
          zone: "transition",
          afterClipIndex: 1,
        }),
      }),
    ).toBe("Word Pop is over the transition after clip 2.");
    expect(onDragOver({ active: wordPop, over: null })).toBe(
      "Word Pop is no longer over a drop target.",
    );
  });

  it("says when a drop lands, is refused, or misses every target", () => {
    const { onDragEnd } = editorDragAnnouncements;
    expect(
      onDragEnd({ active: wordPop, over: over("canvas", { zone: "canvas" }) }),
    ).toBe("Dropped Word Pop on the video preview.");
    expect(
      onDragEnd({
        active: wordPop,
        over: over("transition:0", { zone: "transition", afterClipIndex: 0 }),
      }),
    ).toBe("Word Pop cannot be dropped on the transition after clip 1.");
    expect(
      onDragEnd({
        active: placedLogo,
        over: over("track:video", { zone: "timeline", trackKind: "video" }),
      }),
    ).toBe("Logo cannot be dropped on the Video track.");
    expect(onDragEnd({ active: wordPop, over: null })).toBe(
      "Word Pop was not dropped on a target.",
    );
  });

  it("confirms a cancelled drag", () => {
    expect(
      editorDragAnnouncements.onDragCancel({ active: wordPop, over: null }),
    ).toBe("Cancelled moving Word Pop.");
  });
});

describe("editor DndContext wiring", () => {
  it("passes the announcements and instructions to dnd-kit", async () => {
    const { readFile } = await import("node:fs/promises");
    const { resolve } = await import("node:path");
    // Vitest runs from apps/web; jsdom gives import.meta.url an http scheme.
    const source = await readFile(
      resolve(process.cwd(), "components/editor-client.tsx"),
      "utf8",
    );
    const context = source.slice(source.indexOf("<DndContext"));
    expect(context.slice(0, context.indexOf(">\n"))).toMatch(
      /accessibility=\{\{\s*announcements: editorDragAnnouncements,\s*screenReaderInstructions: editorDragInstructions,?\s*\}\}/,
    );
  });
});
