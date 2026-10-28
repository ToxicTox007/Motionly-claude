import type {
  Active,
  Announcements,
  Over,
  ScreenReaderInstructions,
} from "@dnd-kit/core";

// Screen-reader text for dragging editor elements. dnd-kit's defaults read
// out internal ids ("palette:word-pop over track:broll"); these name the
// element and the target in words, and say when a drop is refused, matching
// what handleDragEnd in editor-client actually does.

export const editorDragInstructions: ScreenReaderInstructions = {
  draggable:
    "To pick up an element, press Space or Enter. Use the arrow keys to move it over the video preview or a timeline track, then press Space or Enter again to drop it, or press Escape to cancel.",
};

const TRACK_NAMES: Record<string, string> = {
  video: "Video",
  broll: "B-roll",
  caption: "Captions",
  transition: "Transitions",
  overlay: "Overlay",
  music: "Music",
  sfx: "Sound effects",
  zoom: "Zoom",
};

type DragData = {
  kind?: unknown;
  element?: { name?: unknown; accepts?: unknown };
};

type DropData = {
  zone?: unknown;
  trackKind?: unknown;
  clip?: { label?: unknown };
  afterClipIndex?: unknown;
};

function dragData(active: Active): DragData {
  return (active.data.current ?? {}) as DragData;
}

function dropData(over: Over): DropData {
  return (over.data.current ?? {}) as DropData;
}

function itemName(active: Active) {
  const name = dragData(active).element?.name;
  return typeof name === "string" && name.trim() ? name : "Element";
}

function dropZone(over: Over) {
  const zone = dropData(over).zone;
  if (typeof zone === "string") return zone;
  return String(over.id) === "canvas" ? "canvas" : "";
}

function targetName(over: Over) {
  const data = dropData(over);
  switch (dropZone(over)) {
    case "canvas":
      return "the video preview";
    case "timeline": {
      const kind = typeof data.trackKind === "string" ? data.trackKind : "";
      return TRACK_NAMES[kind]
        ? `the ${TRACK_NAMES[kind]} track`
        : "the timeline";
    }
    case "clip": {
      const label = data.clip?.label;
      return typeof label === "string" && label.trim()
        ? `the clip ${label}`
        : "a clip";
    }
    case "transition": {
      const index = Number(data.afterClipIndex);
      return Number.isInteger(index) && index >= 0
        ? `the transition after clip ${index + 1}`
        : "a transition";
    }
    default:
      return "this target";
  }
}

/** Mirrors handleDragEnd: placed elements move only on the preview; palette
 * elements drop only on the zones they list in `accepts`. */
function accepts(active: Active, over: Over) {
  const data = dragData(active);
  const zone = dropZone(over);
  if (data.kind === "placed") return String(over.id) === "canvas";
  const accepted = data.element?.accepts;
  return Array.isArray(accepted) && accepted.includes(zone);
}

export const editorDragAnnouncements: Announcements = {
  onDragStart({ active }) {
    return `Picked up ${itemName(active)}.`;
  },
  onDragOver({ active, over }) {
    if (!over) return `${itemName(active)} is no longer over a drop target.`;
    return `${itemName(active)} is over ${targetName(over)}.`;
  },
  onDragEnd({ active, over }) {
    if (!over) return `${itemName(active)} was not dropped on a target.`;
    if (!accepts(active, over)) {
      return `${itemName(active)} cannot be dropped on ${targetName(over)}.`;
    }
    return `Dropped ${itemName(active)} on ${targetName(over)}.`;
  },
  onDragCancel({ active }) {
    return `Cancelled moving ${itemName(active)}.`;
  },
};
