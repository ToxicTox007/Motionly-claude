import type { ProjectTimelineSourceClip } from "@motionly/shared";

export function outputMsToSourceMs(
  outputMs: number,
  clips: ProjectTimelineSourceClip[],
  edge?: "start" | "end",
): number;
