"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  CaptionTrackSummary,
  EffectsConfigSummary,
} from "@motionly/shared";
import { Button } from "@/components/button";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";

export type BrandKitAsset = {
  label: string;
  asset_url: string;
  alt_text?: string;
};

export type BrandKitSettings = {
  colors?: string[];
  font_family?: string;
  caption_settings?: Record<string, unknown>;
  effects_settings?: Record<string, unknown>;
  style_profile_id?: string | null;
  saved_template_id?: string | null;
  creator_template?: string | null;
  intro_asset?: BrandKitAsset | null;
  outro_asset?: BrandKitAsset | null;
  logo_asset?: BrandKitAsset | null;
  watermark_asset?: BrandKitAsset | null;
  tone_rules?: {
    voice?: string;
    do_rules?: string[];
    avoid_rules?: string[];
  };
  default_export?: {
    platform?: string;
    resolution?: string;
    output_aspect?: string;
    fit_mode?: string;
    include_captions?: boolean;
  };
};

export type BrandKitResource = {
  id: string;
  name: string;
  kind: "brand_kit";
  status: string;
  payload: { settings?: BrandKitSettings };
  revision: number;
};

type ReferenceOption = { id: string; name: string; status?: string };

type BrandKitForm = {
  name: string;
  primaryColor: string;
  accentColor: string;
  surfaceColor: string;
  fontFamily: string;
  captionPreset: string;
  styleProfileId: string;
  savedTemplateId: string;
  creatorTemplate: string;
  introUrl: string;
  outroUrl: string;
  logoUrl: string;
  watermarkUrl: string;
  voice: string;
  doRules: string;
  avoidRules: string;
  platform: string;
  resolution: string;
  outputAspect: string;
  fitMode: string;
  includeCaptions: boolean;
};

const captionPresets = [
  ["luxury_minimal", "Minimal"],
  ["bold_influencer", "Bold & Loud"],
  ["vogue_white", "Editorial"],
  ["gold_premium", "Gold Accent"],
  ["founder_clean", "Clean Blue"],
  ["impact_stamp", "Impact Stamp"],
  ["minimal_line", "Minimal Line"],
  ["educational_card", "Education Card"],
  ["creator_ticker", "Creator Ticker"],
] as const;

const creatorTemplates = [
  ["luxury_reel_v1", "Cinematic luxury reel"],
  ["podcast_reel_v1", "Podcast clip"],
  ["talking_head_reel_v1", "Talking head"],
  ["product_promo_reel_v1", "Product promo"],
  ["travel_reel_v1", "Travel vlog"],
  ["founder_reel_v1", "Founder reel"],
  ["fashion_reel_v1", "Fashion reel"],
] as const;

const platforms = [
  ["instagram", "Instagram Reel"],
  ["tiktok", "TikTok"],
  ["shorts", "YouTube Shorts"],
  ["youtube_landscape", "YouTube Landscape"],
  ["instagram_post", "Instagram Post"],
  ["x_landscape", "X Landscape"],
  ["custom", "Custom download"],
] as const;

const emptyForm = (track: CaptionTrackSummary | null): BrandKitForm => ({
  name: "",
  primaryColor: "#111111",
  accentColor: "#ff5500",
  surfaceColor: "#ffffff",
  fontFamily: "Inter",
  captionPreset: track?.preset ?? "luxury_minimal",
  styleProfileId: "",
  savedTemplateId: "",
  creatorTemplate: "founder_reel_v1",
  introUrl: "",
  outroUrl: "",
  logoUrl: "",
  watermarkUrl: "",
  voice: "",
  doRules: "",
  avoidRules: "",
  platform: "instagram",
  resolution: "1080p",
  outputAspect: "9:16",
  fitMode: "fill",
  includeCaptions: true,
});

function formForKit(
  kit: BrandKitResource,
  track: CaptionTrackSummary | null,
): BrandKitForm {
  const settings = kit.payload.settings ?? {};
  const colors = settings.colors ?? [];
  const defaults = emptyForm(track);
  return {
    ...defaults,
    name: kit.name,
    primaryColor: colors[0] ?? defaults.primaryColor,
    accentColor: colors[1] ?? defaults.accentColor,
    surfaceColor: colors[2] ?? defaults.surfaceColor,
    fontFamily: settings.font_family ?? defaults.fontFamily,
    captionPreset: String(
      settings.caption_settings?.preset ??
        track?.preset ??
        defaults.captionPreset,
    ),
    styleProfileId: settings.style_profile_id ?? "",
    savedTemplateId: settings.saved_template_id ?? "",
    creatorTemplate: settings.creator_template ?? defaults.creatorTemplate,
    introUrl: settings.intro_asset?.asset_url ?? "",
    outroUrl: settings.outro_asset?.asset_url ?? "",
    logoUrl: settings.logo_asset?.asset_url ?? "",
    watermarkUrl: settings.watermark_asset?.asset_url ?? "",
    voice: settings.tone_rules?.voice ?? "",
    doRules: (settings.tone_rules?.do_rules ?? []).join("\n"),
    avoidRules: (settings.tone_rules?.avoid_rules ?? []).join("\n"),
    platform: settings.default_export?.platform ?? defaults.platform,
    resolution: settings.default_export?.resolution ?? defaults.resolution,
    outputAspect:
      settings.default_export?.output_aspect ?? defaults.outputAspect,
    fitMode: settings.default_export?.fit_mode ?? defaults.fitMode,
    includeCaptions:
      settings.default_export?.include_captions ?? defaults.includeCaptions,
  };
}

function nonEmptyLines(value: string) {
  return value
    .split("\n")
    .map((rule) => rule.trim())
    .filter(Boolean);
}

function optionalAsset(
  label: string,
  assetUrl: string,
): BrandKitAsset | undefined {
  const value = assetUrl.trim();
  return value ? { label, asset_url: value } : undefined;
}

function effectsSnapshot(
  config: EffectsConfigSummary | null,
): Record<string, unknown> {
  if (!config) return {};
  return {
    preset: config.preset,
    lut_intensity: config.lut_intensity,
    texture: config.texture,
    motion_effects: config.motion_effects,
    motion: config.motion,
    background_removal: config.background_removal,
    gradient_ramp: config.gradient_ramp,
    after_effects_presets: config.after_effects_presets,
    reset_to_defaults: false,
  };
}

export function BrandKitPanel({
  projectId,
  workspaceId,
  styleProfiles,
  savedTemplates,
  captionTrack,
  effectsConfig,
  onApply,
}: {
  projectId: string;
  workspaceId: string;
  styleProfiles: ReferenceOption[];
  savedTemplates: ReferenceOption[];
  captionTrack: CaptionTrackSummary | null;
  effectsConfig: EffectsConfigSummary | null;
  onApply: (kit: BrandKitResource) => Promise<void>;
}) {
  const [kits, setKits] = useState<BrandKitResource[]>([]);
  const [form, setForm] = useState<BrandKitForm>(() => emptyForm(captionTrack));
  const [editing, setEditing] = useState<BrandKitResource | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const auth = useMemo(() => ({ workspaceId }), [workspaceId]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const response = await editorApiRequest<{
        resources: BrandKitResource[];
      }>(`${env.apiBaseUrl}/phase5/brand-kits`, {
        ...auth,
        friendlyError: "Brand Kits could not be loaded.",
        signal,
      });
      if (!signal?.aborted) setKits(response.resources ?? []);
    },
    [auth],
  );

  useEffect(() => {
    const controller = new AbortController();
    const task = window.setTimeout(() => {
      void load(controller.signal).catch((error) => {
        if (!controller.signal.aborted) setMessage((error as Error).message);
      });
    }, 0);
    return () => {
      window.clearTimeout(task);
      controller.abort();
    };
  }, [load]);

  function update<K extends keyof BrandKitForm>(
    key: K,
    value: BrandKitForm[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function settingsFromForm(): BrandKitSettings {
    const colors = [form.primaryColor, form.accentColor, form.surfaceColor]
      .map((color) => color.trim())
      .filter(Boolean);
    const usesCurrentCaptionStyle = form.captionPreset === captionTrack?.preset;
    return {
      colors,
      font_family: form.fontFamily.trim(),
      caption_settings: {
        preset: form.captionPreset,
        ...(captionTrack
          ? {
              hide_filler_words: captionTrack.hide_filler_words,
              ...(usesCurrentCaptionStyle ? { style: captionTrack.style } : {}),
            }
          : {}),
      },
      effects_settings: effectsSnapshot(effectsConfig),
      style_profile_id: form.styleProfileId || null,
      saved_template_id: form.savedTemplateId || null,
      creator_template: form.creatorTemplate || null,
      intro_asset: optionalAsset("Brand intro", form.introUrl),
      outro_asset: optionalAsset("Brand outro", form.outroUrl),
      logo_asset: optionalAsset("Brand logo", form.logoUrl),
      watermark_asset: optionalAsset("Brand watermark", form.watermarkUrl),
      tone_rules: {
        voice: form.voice.trim(),
        do_rules: nonEmptyLines(form.doRules),
        avoid_rules: nonEmptyLines(form.avoidRules),
      },
      default_export: {
        platform: form.platform,
        resolution: form.resolution,
        output_aspect: form.outputAspect,
        fit_mode: form.fitMode,
        include_captions: form.includeCaptions,
      },
    };
  }

  async function save() {
    if (!form.name.trim()) {
      setMessage("Name this Brand Kit before saving.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const settings = settingsFromForm();
      const kit = await editorApiRequest<BrandKitResource>(
        editing
          ? `${env.apiBaseUrl}/phase5/brand-kits/${editing.id}`
          : `${env.apiBaseUrl}/phase5/brand-kits`,
        {
          ...auth,
          method: editing ? "PATCH" : "POST",
          body: {
            name: form.name.trim(),
            settings,
            ...(editing ? { expected_revision: editing.revision } : {}),
          },
          friendlyError:
            "Brand Kit could not be saved. Check its colors and media URLs.",
        },
      );
      setEditing(kit);
      setForm(formForKit(kit, captionTrack));
      await load();
      setMessage(`Brand Kit ${editing ? "updated" : "saved"}.`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function apply(kit: BrandKitResource) {
    setBusy(true);
    setMessage(null);
    try {
      await onApply(kit);
      window.localStorage.setItem(`motionly:brand-kit:${projectId}`, kit.id);
      setMessage(
        `${kit.name} applied to this project. Export defaults are ready in Export.`,
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(kit: BrandKitResource) {
    setBusy(true);
    setMessage(null);
    try {
      await editorApiRequest(`${env.apiBaseUrl}/phase5/brand-kits/${kit.id}`, {
        ...auth,
        method: "DELETE",
        friendlyError: "Brand Kit could not be deleted.",
      });
      if (editing?.id === kit.id) {
        setEditing(null);
        setForm(emptyForm(captionTrack));
      }
      if (
        window.localStorage.getItem(`motionly:brand-kit:${projectId}`) ===
        kit.id
      ) {
        window.localStorage.removeItem(`motionly:brand-kit:${projectId}`);
      }
      await load();
      setMessage("Brand Kit deleted.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 rounded-neu-lg bg-surface p-5 shadow-neu-raised">
      <div>
        <h3 className="text-sm font-bold tracking-tight text-foreground">
          Brand Kit
        </h3>
        <p className="mt-1 text-xxs font-medium text-muted-foreground">
          Apply your house look, assets, tone, and export defaults in one
          action.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Field label="Kit name">
          <input
            value={form.name}
            onChange={(event) => update("name", event.target.value)}
            placeholder="Acme Social"
          />
        </Field>
        <Field label="Font family">
          <input
            value={form.fontFamily}
            onChange={(event) => update("fontFamily", event.target.value)}
            placeholder="Inter"
          />
        </Field>
        <Field label="Primary color">
          <input
            value={form.primaryColor}
            onChange={(event) => update("primaryColor", event.target.value)}
            placeholder="#111111"
          />
        </Field>
        <Field label="Accent color">
          <input
            value={form.accentColor}
            onChange={(event) => update("accentColor", event.target.value)}
            placeholder="#ff5500"
          />
        </Field>
        <Field label="Surface color">
          <input
            value={form.surfaceColor}
            onChange={(event) => update("surfaceColor", event.target.value)}
            placeholder="#ffffff"
          />
        </Field>
        <Field label="Caption preset">
          <select
            value={form.captionPreset}
            onChange={(event) => update("captionPreset", event.target.value)}
          >
            {captionPresets.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Style profile">
          <select
            value={form.styleProfileId}
            onChange={(event) => update("styleProfileId", event.target.value)}
          >
            <option value="">No reference style</option>
            {styleProfiles.map((profile) => (
              <option
                key={profile.id}
                value={profile.id}
                disabled={profile.status !== "ready"}
              >
                {profile.name}
                {profile.status !== "ready" ? ` (${profile.status})` : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Saved edit template">
          <select
            value={form.savedTemplateId}
            onChange={(event) => update("savedTemplateId", event.target.value)}
          >
            <option value="">No saved template</option>
            {savedTemplates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Creator template">
          <select
            value={form.creatorTemplate}
            onChange={(event) => update("creatorTemplate", event.target.value)}
          >
            {creatorTemplates.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Intro video URL">
          <input
            value={form.introUrl}
            onChange={(event) => update("introUrl", event.target.value)}
            placeholder="https://…/intro.mp4"
          />
        </Field>
        <Field label="Outro video URL">
          <input
            value={form.outroUrl}
            onChange={(event) => update("outroUrl", event.target.value)}
            placeholder="https://…/outro.mp4"
          />
        </Field>
        <Field label="Logo image URL">
          <input
            value={form.logoUrl}
            onChange={(event) => update("logoUrl", event.target.value)}
            placeholder="https://…/logo.png"
          />
        </Field>
        <Field label="Watermark image URL">
          <input
            value={form.watermarkUrl}
            onChange={(event) => update("watermarkUrl", event.target.value)}
            placeholder="https://…/watermark.png"
          />
        </Field>
      </div>
      <p className="-mt-1 text-2xs font-medium text-muted-foreground">
        Use public HTTPS or workspace media URLs. Logos and watermarks become
        editable canvas graphics; intros/outros become video overlays.
      </p>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Field label="Tone / voice">
          <textarea
            value={form.voice}
            onChange={(event) => update("voice", event.target.value)}
            placeholder="Warm expert, never salesy"
          />
        </Field>
        <Field label="Always do (one rule per line)">
          <textarea
            value={form.doRules}
            onChange={(event) => update("doRules", event.target.value)}
            placeholder="Lead with proof\nKeep language direct"
          />
        </Field>
        <Field label="Avoid (one rule per line)">
          <textarea
            value={form.avoidRules}
            onChange={(event) => update("avoidRules", event.target.value)}
            placeholder="Avoid hype\nAvoid jargon"
          />
        </Field>
      </div>

      <div className="border-t border-hairline pt-3">
        <p className="mb-2 text-xs font-semibold text-foreground">
          Default export
        </p>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Platform">
            <select
              value={form.platform}
              onChange={(event) => update("platform", event.target.value)}
            >
              {platforms.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Quality">
            <select
              value={form.resolution}
              onChange={(event) => update("resolution", event.target.value)}
            >
              <option value="720p">720p</option>
              <option value="1080p">1080p</option>
              <option value="4k">4K</option>
            </select>
          </Field>
          <Field label="Aspect">
            <select
              value={form.outputAspect}
              onChange={(event) => update("outputAspect", event.target.value)}
            >
              <option value="9:16">Vertical 9:16</option>
              <option value="original">Original</option>
              <option value="1:1">Square 1:1</option>
              <option value="4:5">Portrait 4:5</option>
              <option value="16:9">Landscape 16:9</option>
            </select>
          </Field>
          <Field label="Crop">
            <select
              value={form.fitMode}
              onChange={(event) => update("fitMode", event.target.value)}
            >
              <option value="fill">Fill / crop</option>
              <option value="fit">Fit with padding</option>
              <option value="custom">Custom crop</option>
            </select>
          </Field>
        </div>
        <label className="mt-2 flex items-center gap-2 text-xs font-semibold text-foreground">
          <input
            type="checkbox"
            checked={form.includeCaptions}
            onChange={(event) =>
              update("includeCaptions", event.target.checked)
            }
          />{" "}
          Burn captions into exports
        </label>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => void save()} className="text-xs">
          {editing ? "Update Brand Kit" : "Save Brand Kit"}
        </Button>
        {editing ? (
          <Button
            disabled={busy}
            variant="secondary"
            onClick={() => {
              setEditing(null);
              setForm(emptyForm(captionTrack));
            }}
            className="text-xs"
          >
            New kit
          </Button>
        ) : null}
      </div>

      {kits.length ? (
        <div className="space-y-2 border-t border-hairline pt-3">
          {kits.map((kit) => {
            const settings = kit.payload.settings ?? {};
            const exportPreset = settings.default_export;
            return (
              <div
                key={kit.id}
                className="rounded-neu-md bg-surface p-3 shadow-neu-raised-sm"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-foreground">
                      {kit.name}
                    </p>
                    <p className="mt-1 text-2xs font-medium text-muted-foreground">
                      {(settings.colors ?? []).join(" · ") || "No colors"} ·{" "}
                      {settings.font_family ?? "System"} ·{" "}
                      {String(settings.caption_settings?.preset ?? "captions")}
                    </p>
                    {exportPreset ? (
                      <p className="text-2xs font-medium text-muted-foreground">
                        {exportPreset.platform ?? "instagram"} ·{" "}
                        {exportPreset.output_aspect ?? "9:16"} ·{" "}
                        {exportPreset.resolution ?? "1080p"}
                      </p>
                    ) : null}
                  </div>
                  <span className="shrink-0 text-2xs font-medium text-muted-foreground tabular-nums">
                    v{kit.revision}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Button
                    disabled={busy}
                    onClick={() => void apply(kit)}
                    className="h-8 px-2 text-2xs"
                  >
                    Apply
                  </Button>
                  <Button
                    disabled={busy}
                    variant="secondary"
                    onClick={() => {
                      setEditing(kit);
                      setForm(formForKit(kit, captionTrack));
                      setMessage(null);
                    }}
                    className="h-8 px-2 text-2xs"
                  >
                    Edit
                  </Button>
                  <Button
                    disabled={busy}
                    variant="secondary"
                    onClick={() => void remove(kit)}
                    className="h-8 px-2 text-2xs"
                  >
                    Delete
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-xxs font-medium text-muted-foreground">
          Save your first kit to reuse it across projects.
        </p>
      )}
      {message ? (
        <p
          role="status"
          className="rounded-neu bg-surface p-2 text-xxs font-semibold text-foreground shadow-neu-inset-sm"
        >
          {message}
        </p>
      ) : null}
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-2xs font-semibold tracking-wide text-muted-foreground">
      <span className="mb-1 block">{label}</span>
      <span className="block [&_input]:min-h-9 [&_input]:w-full [&_input]:rounded-neu [&_input]:border [&_input]:border-control [&_input]:bg-surface [&_input]:px-2 [&_input]:text-xs [&_input]:font-medium [&_input]:text-foreground [&_input]:shadow-neu-inset-sm [&_select]:min-h-9 [&_select]:w-full [&_select]:rounded-neu [&_select]:border [&_select]:border-control [&_select]:bg-surface [&_select]:px-2 [&_select]:text-xs [&_select]:font-medium [&_select]:text-foreground [&_select]:shadow-neu-inset-sm [&_textarea]:min-h-16 [&_textarea]:w-full [&_textarea]:rounded-neu [&_textarea]:border [&_textarea]:border-control [&_textarea]:bg-surface [&_textarea]:p-2 [&_textarea]:text-xs [&_textarea]:font-medium [&_textarea]:text-foreground [&_textarea]:shadow-neu-inset-sm">
        {children}
      </span>
    </label>
  );
}
