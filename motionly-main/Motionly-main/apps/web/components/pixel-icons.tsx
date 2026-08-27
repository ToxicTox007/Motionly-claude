import type { SVGProps } from "react";
import { cn } from "@/lib/utils";

export type PixelIconName =
  | "upload"
  | "ai-cut"
  | "captions"
  | "broll"
  | "music"
  | "export"
  | "dashboard"
  | "project"
  | "new"
  | "billing"
  | "settings"
  | "play"
  | "warning"
  | "success"
  | "info"
  | "x";

const pixelRects: Record<PixelIconName, Array<[number, number, number, number]>> = {
  upload: [
    [7, 2, 2, 8],
    [4, 5, 8, 2],
    [5, 3, 2, 2],
    [9, 3, 2, 2],
    [3, 11, 10, 2],
    [2, 12, 2, 2],
    [12, 12, 2, 2],
  ],
  "ai-cut": [
    [2, 3, 3, 3],
    [11, 3, 3, 3],
    [5, 6, 6, 2],
    [7, 2, 2, 12],
    [2, 10, 3, 3],
    [11, 10, 3, 3],
    [5, 9, 6, 2],
  ],
  captions: [
    [2, 3, 12, 10],
    [3, 4, 10, 1],
    [4, 7, 8, 1],
    [4, 10, 5, 1],
    [10, 10, 2, 1],
  ],
  broll: [
    [2, 3, 12, 10],
    [3, 4, 2, 2],
    [7, 4, 2, 2],
    [11, 4, 2, 2],
    [3, 10, 2, 2],
    [7, 10, 2, 2],
    [11, 10, 2, 2],
    [5, 7, 6, 2],
  ],
  music: [
    [4, 2, 8, 2],
    [10, 4, 2, 8],
    [4, 6, 2, 6],
    [2, 11, 4, 3],
    [8, 11, 4, 3],
  ],
  export: [
    [7, 2, 2, 7],
    [10, 5, 2, 2],
    [12, 7, 2, 2],
    [4, 5, 2, 2],
    [2, 7, 2, 2],
    [3, 11, 10, 2],
    [2, 12, 2, 2],
    [12, 12, 2, 2],
  ],
  dashboard: [
    [2, 2, 5, 5],
    [9, 2, 5, 3],
    [9, 7, 5, 7],
    [2, 9, 5, 5],
  ],
  project: [
    [2, 3, 12, 10],
    [3, 2, 5, 2],
    [4, 6, 8, 1],
    [4, 9, 6, 1],
  ],
  new: [
    [7, 3, 2, 10],
    [3, 7, 10, 2],
  ],
  billing: [
    [2, 4, 12, 8],
    [3, 6, 10, 1],
    [4, 9, 3, 1],
    [10, 9, 2, 1],
  ],
  settings: [
    [7, 2, 2, 3],
    [7, 11, 2, 3],
    [2, 7, 3, 2],
    [11, 7, 3, 2],
    [5, 5, 6, 6],
    [7, 7, 2, 2],
  ],
  play: [
    [4, 3, 2, 10],
    [6, 4, 2, 8],
    [8, 5, 2, 6],
    [10, 6, 2, 4],
    [12, 7, 2, 2],
  ],
  warning: [
    [7, 2, 2, 2],
    [6, 4, 4, 2],
    [5, 6, 6, 2],
    [4, 8, 8, 2],
    [3, 10, 10, 2],
    [2, 12, 12, 2],
    [7, 7, 2, 3],
    [7, 11, 2, 1],
  ],
  success: [
    [12, 3, 2, 2],
    [10, 5, 2, 2],
    [8, 7, 2, 2],
    [6, 9, 2, 2],
    [4, 7, 2, 2],
    [2, 5, 2, 2],
  ],
  info: [
    [7, 3, 2, 2],
    [7, 6, 2, 7],
    [6, 12, 4, 1],
  ],
  x: [
    [3, 3, 2, 2],
    [11, 3, 2, 2],
    [5, 5, 2, 2],
    [9, 5, 2, 2],
    [7, 7, 2, 2],
    [5, 9, 2, 2],
    [9, 9, 2, 2],
    [3, 11, 2, 2],
    [11, 11, 2, 2],
  ],
};

export function PixelIcon({
  name,
  className,
  ...props
}: SVGProps<SVGSVGElement> & { name: PixelIconName }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
      shapeRendering="crispEdges"
      className={cn("size-4 shrink-0", className)}
      {...props}
    >
      {pixelRects[name].map(([x, y, width, height], index) => (
        <rect key={`${name}-${index}`} x={x} y={y} width={width} height={height} />
      ))}
    </svg>
  );
}
