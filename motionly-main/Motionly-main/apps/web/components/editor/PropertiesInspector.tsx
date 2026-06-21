"use client";

import React, { useState } from "react";
import { Loader2, Trash2, X } from "lucide-react";
import type { ManualElement } from "@motionly/shared";

export function PropertiesInspector({ element, saving, onChange, onDelete, onClose }: { element: ManualElement; saving?: boolean; onChange: (element: ManualElement) => Promise<void> | void; onDelete: (id: string) => Promise<void> | void; onClose: () => void }) {
  const [draft, setDraft] = useState(element);
  const property = (key: string, fallback: unknown = "") => draft.properties[key] ?? fallback;
  const updateProperty = (key: string, value: unknown) => setDraft((current) => ({ ...current, properties: { ...current.properties, [key]: value } }));
  return (
    <div className="flex h-full min-h-0 flex-col bg-[hsl(var(--surface))]">
      <div className="flex min-h-12 items-center gap-2 border-b border-border px-3">
        <div className="min-w-0 flex-1"><p className="truncate text-xs font-black uppercase tracking-wide">{draft.name}</p><p className="text-[9px] uppercase text-muted-foreground">{draft.type} · {draft.zone}</p></div>
        <button type="button" onClick={onClose} className="pixel-button flex size-9 items-center justify-center" aria-label="Close element properties"><X className="size-3.5" /></button>
      </div>
      <form className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3" onSubmit={(event) => { event.preventDefault(); void onChange(draft); }}>
        {(["text", "caption", "graphic"] as string[]).includes(draft.type) ? (
          <InspectorSection title="Content"><label className="block text-xs font-bold">Text<textarea value={String(property("content", draft.name))} onChange={(event) => updateProperty("content", event.target.value)} className="pixel-input mt-1 min-h-20 w-full p-2" /></label></InspectorSection>
        ) : null}
        <InspectorSection title="Timing">
          <div className="grid grid-cols-2 gap-2"><NumberField label="Start ms" value={draft.start_ms} min={0} onChange={(value) => setDraft((current) => ({ ...current, start_ms: value }))} /><NumberField label="End ms" value={draft.end_ms} min={draft.start_ms + 1} onChange={(value) => setDraft((current) => ({ ...current, end_ms: value }))} /></div>
          {draft.clip_id ? <p className="mt-2 text-[10px] text-muted-foreground">Applied to {draft.clip_id}</p> : null}
        </InspectorSection>
        {draft.zone === "canvas" ? <InspectorSection title="Position & size"><div className="grid grid-cols-2 gap-2"><NumberField label="X %" value={draft.x_pct} min={0} max={100} onChange={(value) => setDraft((current) => ({ ...current, x_pct: value }))} /><NumberField label="Y %" value={draft.y_pct} min={0} max={100} onChange={(value) => setDraft((current) => ({ ...current, y_pct: value }))} /><NumberField label="Width %" value={draft.width_pct} min={1} max={100} onChange={(value) => setDraft((current) => ({ ...current, width_pct: value }))} /><NumberField label="Height %" value={draft.height_pct} min={1} max={100} onChange={(value) => setDraft((current) => ({ ...current, height_pct: value }))} /><NumberField label="Rotation" value={draft.rotation_deg} min={-360} max={360} onChange={(value) => setDraft((current) => ({ ...current, rotation_deg: value }))} /><NumberField label="Opacity %" value={Math.round(draft.opacity * 100)} min={0} max={100} onChange={(value) => setDraft((current) => ({ ...current, opacity: value / 100 }))} /></div></InspectorSection> : null}
        <InspectorSection title="Style / behavior">
          {(["text", "caption", "graphic", "shape"] as string[]).includes(draft.type) ? <><label className="block text-xs font-bold">Color<input type="color" value={String(property("color", "#ffffff"))} onChange={(event) => updateProperty("color", event.target.value)} className="mt-1 h-10 w-full" /></label><NumberField label="Font size" value={Number(property("fontSize", 28))} min={8} max={160} onChange={(value) => updateProperty("fontSize", value)} /></> : null}
          {draft.type === "transition" ? <NumberField label="Duration ms" value={Number(property("durationMs", draft.end_ms - draft.start_ms))} min={1} max={3000} onChange={(value) => updateProperty("durationMs", value)} /> : null}
          {draft.type === "sfx" || draft.type === "music" ? <NumberField label="Volume %" value={Number(property("volume", 80))} min={0} max={100} onChange={(value) => updateProperty("volume", value)} /> : null}
          {draft.type === "overlay" ? <NumberField label="Intensity %" value={Math.round(Number(property("intensity", .5)) * 100)} min={0} max={100} onChange={(value) => updateProperty("intensity", value / 100)} /> : null}
        </InspectorSection>
        {draft.ai_reason ? <div className="border border-primary/30 bg-primary/5 p-2 text-xs"><p><strong>AI reason:</strong> {draft.ai_reason}</p>{draft.confidence != null ? <p className="mt-1">Confidence {Math.round(draft.confidence * 100)}%</p> : null}</div> : null}
        <div className="flex gap-2"><button type="submit" disabled={saving} className="pixel-button flex min-h-11 flex-1 items-center justify-center gap-2 bg-primary px-3 font-bold text-primary-foreground">{saving ? <Loader2 className="size-4 animate-spin" /> : null}Save</button><button type="button" onClick={() => void onDelete(draft.id)} disabled={saving} className="pixel-button flex min-h-11 items-center gap-2 px-3 text-destructive"><Trash2 className="size-4" />Delete</button></div>
      </form>
    </div>
  );
}

function InspectorSection({ title, children }: { title: string; children: React.ReactNode }) { return <fieldset className="space-y-2 border-t border-border pt-3"><legend className="pr-2 text-[10px] font-black uppercase tracking-wider text-muted-foreground">{title}</legend>{children}</fieldset>; }
function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min?: number; max?: number; onChange: (value: number) => void }) { return <label className="block text-xs font-bold">{label}<input type="number" value={Number.isFinite(value) ? value : 0} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} className="pixel-input mt-1 min-h-10 w-full px-2" /></label>; }
