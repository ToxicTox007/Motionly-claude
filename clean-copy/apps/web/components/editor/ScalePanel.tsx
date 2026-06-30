"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import { Activity, BrainCircuit, Film, Layers3, Loader2, Save, Users } from "lucide-react";
import type { CaptionTrackSummary, EditPlanRecordSummary, EffectsConfigSummary } from "@motionly/shared";
import { Button } from "@/components/button";
import { editorApiRequest, pollPlanJob } from "@/lib/client-api";
import { env } from "@/lib/env";
import { createClient } from "@/lib/supabase/browser";
import { cn } from "@/lib/utils";

type Resource = {
  id: string;
  kind: string;
  name: string;
  status: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  revision: number;
  error_message?: string | null;
};

interface ScalePanelProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  activePlan: EditPlanRecordSummary | null;
  captionTrack: CaptionTrackSummary | null;
  effectsConfig: EffectsConfigSummary | null;
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onEffectsConfigUpdated: (config: EffectsConfigSummary) => void;
}

export function ScalePanel(props: ScalePanelProps) {
  const { projectId, workspaceId, userId, apiAccessToken, activePlan, onPlanUpdated } = props;
  const [resources, setResources] = useState<Resource[]>([]);
  const [templates, setTemplates] = useState<Resource[]>([]);
  const [audio, setAudio] = useState<Record<string, unknown>>({});
  const [analytics, setAnalytics] = useState<Record<string, unknown>>({});
  const [collaboration, setCollaboration] = useState<Resource | null>(null);
  const [presenceCount, setPresenceCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [runwayPrompt, setRunwayPrompt] = useState("");
  const [runwayImage, setRunwayImage] = useState("");
  const [profileName, setProfileName] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [batchProjects, setBatchProjects] = useState(projectId);
  const collaborationRevision = useRef(-1);

  const load = useCallback(async () => {
    const [resourceData, templateData, audioData, analyticsData, collaborationData] = await Promise.all([
      editorApiRequest<{ resources: Resource[] }>(`${env.apiBaseUrl}/projects/${projectId}/phase5/resources`, auth({ workspaceId, userId, apiAccessToken })),
      editorApiRequest<{ resources: Resource[] }>(`${env.apiBaseUrl}/phase5/templates`, auth({ workspaceId, userId, apiAccessToken })),
      editorApiRequest<{ audio_intelligence: Record<string, unknown> }>(`${env.apiBaseUrl}/projects/${projectId}/phase5/audio-intelligence`, auth({ workspaceId, userId, apiAccessToken })).catch(() => ({ audio_intelligence: {} })),
      editorApiRequest<Record<string, unknown>>(`${env.apiBaseUrl}/projects/${projectId}/analytics/ai-edits`, auth({ workspaceId, userId, apiAccessToken })),
      editorApiRequest<Resource>(`${env.apiBaseUrl}/projects/${projectId}/phase5/collaboration`, auth({ workspaceId, userId, apiAccessToken })),
    ]);
    setResources(resourceData.resources);
    setTemplates(templateData.resources);
    setAudio(audioData.audio_intelligence);
    setAnalytics(analyticsData);
    setCollaboration(collaborationData);
    const shared = (collaborationData.payload?.manifest ?? null) as EditPlanRecordSummary | null;
    if (shared?.id && collaborationData.revision > collaborationRevision.current) {
      collaborationRevision.current = collaborationData.revision;
      onPlanUpdated(shared);
    }
  }, [apiAccessToken, onPlanUpdated, projectId, userId, workspaceId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load().catch((error) => setMessage((error as Error).message));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    const supabase = createClient();
    const channel = supabase.channel(`workspace:${workspaceId}:project:${projectId}:editors`, {
      config: { presence: { key: userId } },
    });
    channel
      .on("presence", { event: "sync" }, () => {
        setPresenceCount(Object.values(channel.presenceState()).flat().length || 1);
      })
      .on("broadcast", { event: "manifest_updated" }, () => void load())
      .on("postgres_changes", { event: "UPDATE", schema: "public", table: "phase5_resources", filter: `project_id=eq.${projectId}` }, () => void load())
      .subscribe(async (status) => {
        if (status === "SUBSCRIBED") {
          await channel.track({ user_id: userId, project_id: projectId, online_at: new Date().toISOString() });
        }
      });
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [load, projectId, userId, workspaceId]);

  useEffect(() => {
    if (!resources.some((item) => ["queued", "processing"].includes(item.status))) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load, resources]);

  const profiles = useMemo(() => resources.filter((item) => item.kind === "style_profile"), [resources]);
  const speech = (audio.emotion_pacing ?? {}) as Record<string, unknown>;
  const rhythm = (speech.speech_rhythm ?? {}) as Record<string, unknown>;
  const summary = (analytics.summary ?? {}) as Record<string, unknown>;
  const analyticsLayers = (summary.layers ?? []) as Array<Record<string, unknown>>;

  async function action<T>(work: () => Promise<T>, success: string) {
    setBusy(true);
    setMessage(null);
    try {
      await work();
      await load();
      setMessage(success);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function uploadReferences(profile: Resource, files: FileList | null) {
    if (!files) return;
    const existingCount = Array.isArray(profile.payload.references) ? profile.payload.references.length : 0;
    const selected = Array.from(files).slice(0, Math.max(0, 5 - existingCount));
    await action(async () => {
      for (const file of selected) {
        const initialized = await editorApiRequest<{
          reference_id: string;
          signed_upload_url: string;
          upload_headers: Record<string, string>;
        }>(`${env.apiBaseUrl}/projects/${projectId}/phase5/style-profiles/${profile.id}/references/init`, {
          ...auth(props), method: "POST", body: { filename: file.name, mime_type: file.type || "video/mp4", file_size_bytes: file.size },
        });
        const uploaded = await fetch(initialized.signed_upload_url, { method: "PUT", headers: initialized.upload_headers, body: file });
        if (!uploaded.ok) throw new Error(`Could not upload ${file.name}.`);
        await editorApiRequest(`${env.apiBaseUrl}/projects/${projectId}/phase5/style-profiles/${profile.id}/references/complete`, {
          ...auth(props), method: "POST", body: { reference_id: initialized.reference_id },
        });
      }
    }, "Style references uploaded.");
  }

  async function syncManifest() {
    if (!activePlan || !collaboration) return;
    await action(async () => {
      const updated = await editorApiRequest<Resource>(`${env.apiBaseUrl}/projects/${projectId}/phase5/collaboration`, {
        ...auth(props), method: "PATCH", body: { base_revision: collaboration.revision, manifest: activePlan },
      });
      collaborationRevision.current = updated.revision;
      setCollaboration(updated);
    }, "Shared manifest synchronized.");
  }

  async function applySavedTemplate(template: Resource) {
    const settings = (template.payload.settings ?? {}) as Record<string, unknown>;
    const payload = await editorApiRequest<{
      plan?: EditPlanRecordSummary;
      status: string;
      job_id?: string;
      status_url?: string;
    }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
      ...auth(props), method: "POST", body: { saved_template_id: template.id, force: true },
    });

    let plan: EditPlanRecordSummary;
    if (payload.status === "queued" && payload.status_url) {
      setMessage("Applying template: enqueued...");
      const planId = await pollPlanJob(
        `${env.apiBaseUrl}${payload.status_url}`,
        auth(props),
        (progress) => {
          setMessage(`Applying template: ${progress}%`);
        }
      );
      const planList = await editorApiRequest<{ plans: EditPlanRecordSummary[] }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans`,
        auth(props)
      );
      const foundPlan = planList.plans.find((p) => p.id === planId);
      if (!foundPlan) {
        throw new Error("Template plan was generated successfully but could not be loaded.");
      }
      plan = foundPlan;
    } else {
      plan = payload.plan as EditPlanRecordSummary;
    }

    onPlanUpdated(plan);

    const captionSettings = settings.caption_settings as Record<string, unknown> | undefined;
    if (captionSettings && props.captionTrack) {
      const captions = await editorApiRequest<{ track: CaptionTrackSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/captions/${props.captionTrack.id}`,
        { ...auth(props), method: "PATCH", body: captionSettings },
      );
      props.onCaptionTrackUpdated(captions.track);
    }
    const effectsSettings = settings.effects_settings as Record<string, unknown> | undefined;
    if (effectsSettings && Object.keys(effectsSettings).length) {
      const effects = await editorApiRequest<{ config: EffectsConfigSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/effects`,
        { ...auth(props), method: "PATCH", body: { ...effectsSettings, force: true } },
      );
      props.onEffectsConfigUpdated(effects.config);
    }
  }

  async function applyStyleProfile(profile: Resource) {
    const payload = await editorApiRequest<{
      plan?: EditPlanRecordSummary;
      status: string;
      job_id?: string;
      status_url?: string;
    }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
      ...auth(props), method: "POST", body: { style_profile_id: profile.id, force: true },
    });

    let plan: EditPlanRecordSummary;
    if (payload.status === "queued" && payload.status_url) {
      setMessage("Applying style: enqueued...");
      const planId = await pollPlanJob(
        `${env.apiBaseUrl}${payload.status_url}`,
        auth(props),
        (progress) => {
          setMessage(`Applying style: ${progress}%`);
        }
      );
      const planList = await editorApiRequest<{ plans: EditPlanRecordSummary[] }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans`,
        auth(props)
      );
      const foundPlan = planList.plans.find((p) => p.id === planId);
      if (!foundPlan) {
        throw new Error("Style plan was generated successfully but could not be loaded.");
      }
      plan = foundPlan;
    } else {
      plan = payload.plan as EditPlanRecordSummary;
    }

    onPlanUpdated(plan);
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-2 border-b-2 border-foreground pb-3">
        <div>
          <h2 className="text-lg font-black uppercase tracking-tight text-foreground">Intelligence & Scale</h2>
          <p className="text-xs text-muted-foreground font-mono">Phase 5 production workflows.</p>
        </div>
        <span className="flex items-center gap-1.5 text-xs font-mono font-bold bg-surface border-2 border-foreground px-2.5 py-1 rounded shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
          <Users className="size-3.5 text-primary" strokeWidth={2} />
          {presenceCount} online
        </span>
      </div>

      <Section title="Audio intelligence" icon={Activity}>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Metric label="Emotion" value={String(speech.aggregate_emotion ?? "pending")} />
          <Metric label="Rhythm" value={rhythm.bpm_equivalent ? `${rhythm.bpm_equivalent} BPM-eq` : "pending"} />
          <Metric label="Pauses" value={String(speech.pause_count ?? 0)} />
          <Metric label="Windows" value={String(Array.isArray(speech.windows) ? speech.windows.length : 0)} />
        </div>
        <Button disabled={busy} variant="secondary" className="mt-3.5 w-full text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000] gap-1.5" onClick={() => action(() => editorApiRequest(`${env.apiBaseUrl}/projects/${projectId}/phase5/stems`, { ...auth(props), method: "POST", body: { model: "htdemucs" } }), "Demucs separation queued.")}>Separate vocals and music</Button>
      </Section>

      <Section title="Runway Gen-3 B-roll" icon={Film}>
        <div className="space-y-2">
          <input className="min-h-[40px] w-full px-3 text-xs font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all" placeholder="Describe the generated shot" value={runwayPrompt} onChange={(event) => setRunwayPrompt(event.target.value)} />
          <input className="min-h-[40px] w-full px-3 text-xs font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all" placeholder="Public prompt image URL (required by Gen-3)" value={runwayImage} onChange={(event) => setRunwayImage(event.target.value)} />
        </div>
        <Button disabled={busy || runwayPrompt.length < 3 || !runwayImage} className="mt-3 w-full text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000]" onClick={() => action(() => editorApiRequest(`${env.apiBaseUrl}/projects/${projectId}/phase5/generated-broll`, { ...auth(props), method: "POST", body: { prompt: runwayPrompt, prompt_image_url: runwayImage, duration_s: 5, ratio: "768:1280" } }), "Runway generation queued.")}>Generate premium B-roll</Button>
      </Section>

      <Section title="Reference style analysis" icon={BrainCircuit}>
        <div className="flex gap-2">
          <input className="min-h-[40px] min-w-0 flex-1 px-3 text-xs font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all" placeholder="Style profile name" value={profileName} onChange={(event) => setProfileName(event.target.value)} />
          <Button disabled={busy || !profileName.trim()} onClick={() => action(() => editorApiRequest(`${env.apiBaseUrl}/projects/${projectId}/phase5/style-profiles`, { ...auth(props), method: "POST", body: { name: profileName, target_style: activePlan?.style ?? "creator_influencer" } }), "Style profile created.")} className="text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000]">Create</Button>
        </div>
        {profiles.map((profile) => {
          const references = (profile.payload.references ?? []) as Array<{ status: string }>;
          return (
            <div key={profile.id} className="mt-3.5 border-2 border-foreground bg-surface p-4 rounded shadow-[2px_2px_0px_#000] space-y-3">
              <p className="text-xs font-bold text-foreground">
                {profile.name} · <span className="text-muted-foreground font-mono">{references.length}/5 references</span> · <span className="badge badge-primary scale-90">{profile.status}</span>
              </p>
              <input type="file" accept="video/*" multiple disabled={busy || references.length >= 5} className="w-full text-xs font-mono text-muted-foreground file:mr-3 file:py-1 file:px-2.5 file:rounded file:border-2 file:border-foreground file:text-xs file:font-bold file:bg-primary file:text-primary-foreground hover:file:-translate-y-0.5 hover:file:shadow-[1px_1px_0px_#000] transition-all" onChange={(event) => void uploadReferences(profile, event.target.files)} />
              <div className="flex gap-2 pt-1">
                <Button variant="secondary" disabled={busy || references.filter((item) => item.status === "ready").length < 5} onClick={() => action(() => editorApiRequest(`${env.apiBaseUrl}/projects/${projectId}/phase5/style-profiles/${profile.id}/train`, { ...auth(props), method: "POST", body: {} }), "Style analysis queued.")} className="text-xs py-1.5 h-8 border-2 border-foreground hover:shadow-[1px_1px_0px_#000]">Analyze</Button>
                {profile.status === "ready" ? <Button disabled={busy} onClick={() => action(() => applyStyleProfile(profile), "Reference style applied.")} className="text-xs py-1.5 h-8 border-2 border-foreground hover:shadow-[1px_1px_0px_#000]">Apply</Button> : null}
              </div>
            </div>
          );
        })}
      </Section>

      <Section title="Template library & batch" icon={Layers3}>
        <div className="flex gap-2">
          <input className="min-h-[40px] min-w-0 flex-1 px-3 text-xs font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all" placeholder="Template name" value={templateName} onChange={(event) => setTemplateName(event.target.value)} />
          <Button disabled={busy || !activePlan || !templateName.trim()} onClick={() => action(() => editorApiRequest(`${env.apiBaseUrl}/phase5/templates`, { ...auth(props), method: "POST", body: { name: templateName, settings: { style: activePlan?.style, target_duration_s: activePlan?.target_duration_s, broll_density: activePlan?.broll_density, direction: activePlan?.prompt, caption_settings: captionTemplateSettings(props.captionTrack), effects_settings: effectsTemplateSettings(props.effectsConfig) } } }), "Template saved.")} className="text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000] gap-1.5"><Save className="size-3.5" strokeWidth={2} />Save</Button>
        </div>
        <p className="mt-3.5 text-xs font-mono font-bold text-muted-foreground">{templates.length} saved template{templates.length === 1 ? "" : "s"}</p>
        <div className="space-y-2 mt-2">
          {templates.map((template) => (
            <div key={template.id} className="flex items-center justify-between border-2 border-foreground bg-surface p-3 rounded text-xs gap-3 shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
              <span className="truncate font-bold text-foreground">{template.name}</span>
              <Button variant="secondary" disabled={busy} className="min-h-8 h-8 px-3 text-[10px] border-2 border-foreground hover:shadow-[1px_1px_0px_#000]" onClick={() => action(() => applySavedTemplate(template), "Template applied to timeline, captions, and effects.")}>Apply</Button>
            </div>
          ))}
        </div>
        <textarea className="mt-3 min-h-[72px] w-full p-3 text-xs font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all" aria-label="Batch project IDs" value={batchProjects} onChange={(event) => setBatchProjects(event.target.value)} placeholder="Project UUIDs, comma separated" />
        <Button disabled={busy || !batchProjects.trim()} variant="secondary" className="mt-2 w-full text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000]" onClick={() => action(() => editorApiRequest(`${env.apiBaseUrl}/phase5/batches`, { ...auth(props), method: "POST", body: { name: "Same-style batch", project_ids: batchProjects.split(/[\s,]+/).filter(Boolean), style: activePlan?.style ?? null, target_duration_s: activePlan?.target_duration_s ?? 35, broll_density: activePlan?.broll_density ?? "medium", saved_template_id: templates[0]?.id ?? null } }), "Batch processing enqueued.")}>Process video batch</Button>
      </Section>

      <Section title="Collaboration & learning" icon={Users}>
        <Button disabled={busy || !activePlan || !collaboration} className="w-full text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000] h-11" onClick={syncManifest}>Sync shared manifest</Button>
        <p className="mt-2 text-[10px] text-muted-foreground font-mono leading-relaxed">V1 collaboration uses whole-timeline optimistic revisions. Concurrent conflicts reload latest state.</p>
        <p className="mt-3 text-xs font-mono font-bold text-foreground">{String(summary.total_events ?? 0)} edit signals · {String(summary.training_example_count ?? 0)} training examples</p>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {analyticsLayers.map((layer) => (
            <div key={String(layer.layer)} className="border-2 border-foreground bg-surface p-2.5 rounded text-[10px] shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
              <span className="font-bold text-foreground font-mono">{String(layer.layer)}</span>
              <span className="block text-muted-foreground mt-0.5 font-mono">kept {String(layer.kept ?? 0)} · changed {String(layer.changed ?? 0)} · rejected {String(layer.rejected ?? 0)}</span>
            </div>
          ))}
        </div>
        <Button disabled={busy} variant="secondary" className="mt-3.5 w-full text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000]" onClick={() => action(() => editorApiRequest(`${env.apiBaseUrl}/projects/${projectId}/phase5/model-training`, { ...auth(props), method: "POST", body: { minimum_examples: 3 } }), "Preference summary enqueued.")}>Learn preferences from overrides</Button>
      </Section>

      <div className="space-y-1.5 pt-2">
        {resources.slice(0, 8).map((resource) => (
          <div key={resource.id} className="flex justify-between items-center gap-2 border-2 border-foreground bg-surface px-3 py-2 text-[10px] rounded font-mono shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
            <span className="truncate font-bold text-foreground">{resource.name}</span>
            <span className={cn("capitalize font-black text-2xs", resource.status === "failed" ? "text-danger" : "text-muted-foreground")}>{resource.status}</span>
          </div>
        ))}
      </div>
      {busy ? (
        <p className="flex items-center gap-2 text-xs text-primary font-bold pl-1 font-mono">
          <Loader2 className="size-4 animate-spin text-primary" strokeWidth={2} />
          Working…
        </p>
      ) : null}
      {message ? (
        <p role="status" className="rounded border-2 border-foreground bg-muted p-3.5 text-xs font-bold text-foreground font-mono shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: React.ElementType;
  children: React.ReactNode;
}) {
  return (
    <section className="border border-border bg-surface/30 rounded-xl p-4 space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Icon className="size-4 text-primary" strokeWidth={1.5} />
        {title}
      </h3>
      {children}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-elevated p-3">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="truncate text-sm font-bold mt-1">{value}</p>
    </div>
  );
}

function auth(props: Pick<ScalePanelProps, "workspaceId" | "userId" | "apiAccessToken">) {
  return { workspaceId: props.workspaceId, userId: props.userId, accessToken: props.apiAccessToken };
}

function captionTemplateSettings(track: CaptionTrackSummary | null) {
  return track ? { preset: track.preset, style: track.style, hide_filler_words: track.hide_filler_words } : {};
}

function effectsTemplateSettings(config: EffectsConfigSummary | null) {
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
