"use client";

import React, { useState } from "react";
import { Loader2, Trash2, X } from "lucide-react";
import type { ManualElement } from "@motionly/shared";

export function PropertiesInspector({
  element,
  saving,
  onChange,
  onDelete,
  onClose,
}: {
  element: ManualElement;
  saving?: boolean;
  onChange: (element: ManualElement) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(element);
  const property = (key: string, fallback: unknown = "") =>
    draft.properties[key] ?? fallback;
  const updateProperty = (key: string, value: unknown) =>
    setDraft((current) => ({
      ...current,
      properties: { ...current.properties, [key]: value },
    }));

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      {/* Panel Header */}
      <div className="flex min-h-[56px] items-center gap-3 border-b-2 border-foreground px-4 shrink-0">
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-black uppercase tracking-wider text-foreground">
            {draft.name}
          </p>
          <p className="text-[10px] font-bold uppercase text-muted-foreground mt-0.5 font-mono">
            {draft.type} · {draft.zone}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex size-8 items-center justify-center rounded border-2 border-foreground bg-surface hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] p-0"
          aria-label="Close element properties"
        >
          <X className="size-4 text-foreground" strokeWidth={2} />
        </button>
      </div>

      {/* Panel Form */}
      <form
        className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4"
        onSubmit={(event) => {
          event.preventDefault();
          void onChange(draft);
        }}
      >
        {(["text", "caption", "graphic"] as string[]).includes(draft.type) ? (
          <InspectorSection title="Content">
            <label className="block text-xs font-bold text-foreground">
              Text
              <textarea
                value={String(property("content", draft.name))}
                onChange={(event) => updateProperty("content", event.target.value)}
                className="mt-1.5 min-h-[80px] w-full p-3 resize-y font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all"
              />
            </label>
          </InspectorSection>
        ) : null}

        <InspectorSection title="Timing">
          <div className="grid grid-cols-2 gap-3">
            <NumberField
              label="Start ms"
              value={draft.start_ms}
              min={0}
              onChange={(value) =>
                setDraft((current) => ({ ...current, start_ms: value }))
              }
            />
            <NumberField
              label="End ms"
              value={draft.end_ms}
              min={draft.start_ms + 1}
              onChange={(value) =>
                setDraft((current) => ({ ...current, end_ms: value }))
              }
            />
          </div>
          {draft.clip_id ? (
            <p className="mt-2 text-[10px] font-bold font-mono text-muted-foreground">
              Applied to {draft.clip_id}
            </p>
          ) : null}
        </InspectorSection>

        {draft.zone === "canvas" ? (
          <InspectorSection title="Position & size">
            <div className="grid grid-cols-2 gap-3">
              <NumberField
                label="X %"
                value={draft.x_pct}
                min={0}
                max={100}
                onChange={(value) =>
                  setDraft((current) => ({ ...current, x_pct: value }))
                }
              />
              <NumberField
                label="Y %"
                value={draft.y_pct}
                min={0}
                max={100}
                onChange={(value) =>
                  setDraft((current) => ({ ...current, y_pct: value }))
                }
              />
              <NumberField
                label="Width %"
                value={draft.width_pct}
                min={1}
                max={100}
                onChange={(value) =>
                  setDraft((current) => ({ ...current, width_pct: value }))
                }
              />
              <NumberField
                label="Height %"
                value={draft.height_pct}
                min={1}
                max={100}
                onChange={(value) =>
                  setDraft((current) => ({ ...current, height_pct: value }))
                }
              />
              <NumberField
                label="Rotation"
                value={draft.rotation_deg}
                min={-360}
                max={360}
                onChange={(value) =>
                  setDraft((current) => ({ ...current, rotation_deg: value }))
                }
              />
              <NumberField
                label="Opacity %"
                value={Math.round(draft.opacity * 100)}
                min={0}
                max={100}
                onChange={(value) =>
                  setDraft((current) => ({ ...current, opacity: value / 100 }))
                }
              />
            </div>
          </InspectorSection>
        ) : null}

        <InspectorSection title="Style / behavior">
          {(["text", "caption", "graphic", "shape"] as string[]).includes(
            draft.type,
          ) ? (
            <div className="space-y-3.5">
              <label className="block text-xs font-bold text-foreground">
                Color
                <input
                  type="color"
                  value={String(property("color", "#ffffff"))}
                  onChange={(event) => updateProperty("color", event.target.value)}
                  className="mt-1.5 h-10 w-full rounded border-2 border-foreground bg-transparent p-0.5 cursor-pointer"
                />
              </label>
              <NumberField
                label="Font size"
                value={Number(property("fontSize", 28))}
                min={8}
                max={160}
                onChange={(value) => updateProperty("fontSize", value)}
              />
            </div>
          ) : null}
          {draft.type === "transition" ? (
            <NumberField
              label="Duration ms"
              value={Number(
                property("durationMs", draft.end_ms - draft.start_ms),
              )}
              min={1}
              max={3000}
              onChange={(value) => updateProperty("durationMs", value)}
            />
          ) : null}
          {draft.type === "sfx" || draft.type === "music" ? (
            <NumberField
              label="Volume %"
              value={Number(property("volume", 80))}
              min={0}
              max={100}
              onChange={(value) => updateProperty("volume", value)}
            />
          ) : null}
          {draft.type === "overlay" ? (
            <NumberField
              label="Intensity %"
              value={Math.round(Number(property("intensity", 0.5)) * 100)}
              min={0}
              max={100}
              onChange={(value) => updateProperty("intensity", value / 100)}
            />
          ) : null}
        </InspectorSection>

        {draft.ai_reason ? (
          <div className="rounded border-2 border-foreground bg-accent/10 p-3.5 text-xs mb-4 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
            <p className="font-bold uppercase tracking-wide text-foreground">
              AI Insight
            </p>
            <p className="text-muted-foreground mt-1 leading-relaxed font-mono">
              {draft.ai_reason}
            </p>
            {draft.confidence != null ? (
              <p className="mt-1.5 font-black text-[10px] text-primary uppercase font-mono">
                Confidence {Math.round(draft.confidence * 100)}%
              </p>
            ) : null}
          </div>
        ) : null}

        {/* Action Buttons */}
        <div className="flex gap-2.5 pt-3">
          <button
            type="submit"
            disabled={saving}
            className="flex-1 inline-flex items-center justify-center gap-2 h-11 rounded border-2 border-foreground bg-primary text-primary-foreground text-xs font-black hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none transition-all duration-[100ms] focus:outline-none"
          >
            {saving ? <Loader2 className="size-4 animate-spin" /> : null}
            Save Changes
          </button>
          <button
            type="button"
            onClick={() => void onDelete(draft.id)}
            disabled={saving}
            className="inline-flex items-center justify-center gap-2 h-11 px-4 text-xs font-black border-2 border-danger text-danger bg-surface rounded hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms]"
          >
            <Trash2 className="size-4" strokeWidth={2} />
            Delete
          </button>
        </div>
      </form>
    </div>
  );
}

function InspectorSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="space-y-3.5 border-t-2 border-foreground pt-4 mt-4 first:border-t-0 first:pt-0 first:mt-0">
      <legend className="pr-2 text-[10px] font-black uppercase tracking-wider text-foreground font-mono select-none">
        {title}
      </legend>
      {children}
    </fieldset>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs font-bold text-foreground">
      {label}
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        min={min}
        max={max}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1.5 min-h-[40px] w-full px-3 font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all"
      />
    </label>
  );
}
