"use client";

import type React from "react";
import {
  Download,
  History,
  Languages,
  Layers,
  MessageSquare,
  Music2,
  Network,
  Palette,
  Sparkles,
  Type,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type InspectorTab =
  | "ai"
  | "captions"
  | "broll"
  | "audio"
  | "look"
  | "history"
  | "scale"
  | "review"
  | "localize"
  | "export";

export const INSPECTOR_TABS: Array<{
  id: InspectorTab;
  label: string;
  icon: React.ElementType;
  requiresPlan: boolean;
}> = [
  { id: "ai", label: "AI", icon: Sparkles, requiresPlan: false },
  { id: "captions", label: "Text", icon: Type, requiresPlan: false },
  { id: "broll", label: "B-roll", icon: Layers, requiresPlan: false },
  { id: "audio", label: "Audio", icon: Music2, requiresPlan: false },
  { id: "look", label: "Look", icon: Palette, requiresPlan: false },
  { id: "history", label: "History", icon: History, requiresPlan: true },
  { id: "review", label: "Review", icon: MessageSquare, requiresPlan: true },
  { id: "scale", label: "Scale", icon: Network, requiresPlan: false },
  { id: "localize", label: "Localize", icon: Languages, requiresPlan: false },
  { id: "export", label: "Export", icon: Download, requiresPlan: false },
];

export function inspectorTabId(tab: InspectorTab) {
  return `editor-tab-${tab}`;
}

export function inspectorPanelId(tab: InspectorTab) {
  return `editor-panel-${tab}`;
}

/**
 * The editor's tool tabs. Only the selected panel is rendered, so only the
 * selected tab carries aria-controls (pointing at a missing id is an ARIA
 * error). aria-selected already tells screen readers which tab is current,
 * and the icons are decorative.
 */
export function InspectorTabList({
  tab,
  onTabChange,
  hasActivePlan,
  plansUnavailable = false,
}: {
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  hasActivePlan: boolean;
  /** The plans failed to load: say so rather than "Generate an AI edit". */
  plansUnavailable?: boolean;
}) {
  const disabledReason = plansUnavailable
    ? "Edit plans couldn't be loaded"
    : "Generate an AI edit first";
  function onTabKeyDown(
    event: React.KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) {
    if (event.key === "Escape") {
      event.currentTarget.blur();
      return;
    }
    const direction =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!direction && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const enabledTabs = INSPECTOR_TABS.filter(
      (item) => !(item.requiresPlan && !hasActivePlan),
    );
    const current = enabledTabs.findIndex(
      (item) => item.id === INSPECTOR_TABS[currentIndex].id,
    );
    const next =
      event.key === "Home"
        ? enabledTabs[0]
        : event.key === "End"
          ? enabledTabs[enabledTabs.length - 1]
          : enabledTabs[
              (current + direction + enabledTabs.length) % enabledTabs.length
            ];
    onTabChange(next.id);
    window.requestAnimationFrame(() => {
      document.getElementById(inspectorTabId(next.id))?.focus();
    });
  }

  return (
    <div
      className="flex shrink-0 snap-x gap-1.5 overflow-x-auto overscroll-x-contain rounded-full bg-surface px-1.5 py-2 shadow-neu-inset-sm sm:gap-2 sm:px-2"
      role="tablist"
      aria-label="Editor tools"
    >
      {INSPECTOR_TABS.map(({ id, label, icon: Icon, requiresPlan }, index) => {
        const disabled = requiresPlan && !hasActivePlan;
        const selected = tab === id;
        return (
          <button
            id={inspectorTabId(id)}
            key={id}
            role="tab"
            type="button"
            aria-selected={selected}
            aria-controls={selected ? inspectorPanelId(id) : undefined}
            tabIndex={selected ? 0 : -1}
            onClick={() => !disabled && onTabChange(id)}
            onKeyDown={(event) => onTabKeyDown(event, index)}
            disabled={disabled}
            title={disabled ? disabledReason : label}
            className={cn(
              "flex min-h-11 min-w-[4.6rem] shrink-0 snap-start items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-3 py-2 text-xs font-semibold transition-shadow duration-200 sm:min-w-20 sm:px-4 sm:text-sm",
              selected
                ? "bg-primary text-primary-foreground shadow-neu-glow"
                : disabled
                  ? "cursor-not-allowed text-muted-foreground opacity-45"
                  : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            {label}
          </button>
        );
      })}
    </div>
  );
}
