import type { ComponentType, SVGProps } from "react";
import {
  Captions,
  CircleCheck,
  Clapperboard,
  CreditCard,
  Download,
  FolderOpen,
  Info,
  LayoutDashboard,
  Music,
  Play,
  Plus,
  Settings,
  TriangleAlert,
  Upload,
  WandSparkles,
  X,
} from "lucide-react";
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

const icons: Record<PixelIconName, ComponentType<SVGProps<SVGSVGElement>>> = {
  upload: Upload,
  "ai-cut": WandSparkles,
  captions: Captions,
  broll: Clapperboard,
  music: Music,
  export: Download,
  dashboard: LayoutDashboard,
  project: FolderOpen,
  new: Plus,
  billing: CreditCard,
  settings: Settings,
  play: Play,
  warning: TriangleAlert,
  success: CircleCheck,
  info: Info,
  x: X,
};

export function PixelIcon({
  name,
  className,
  ...props
}: SVGProps<SVGSVGElement> & { name: PixelIconName }) {
  const Icon = icons[name];
  return (
    <Icon
      aria-hidden="true"
      strokeWidth={1.75}
      className={cn("size-4 shrink-0", className)}
      {...props}
    />
  );
}
