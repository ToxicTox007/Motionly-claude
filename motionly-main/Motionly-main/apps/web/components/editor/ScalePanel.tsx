"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import {
  Activity,
  BrainCircuit,
  Film,
  Layers3,
  Loader2,
  RefreshCw,
  Save,
  Trash2,
  Users,
} from "lucide-react";
import type {
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EffectsConfigSummary,
} from "@motionly/shared";
import { Button } from "@/components/button";
import {
  editorApiRequest,
  pollPlanJob,
  resolveQueuedLayer,
  startAdaptivePolling,
  type PlanJobIdentity,
} from "@/lib/client-api";
import { env } from "@/lib/env";
import {
  genericInitToHelper,
  genericRefreshToHelper,
  putBlobWithProgress,
  runResumableUpload,
  type GenericMediaSession,
  type ResumableUploadTransport,
} from "@/lib/resumable-upload";
import { createClient } from "@/lib/supabase/browser";
import {
  MANIFEST_UPDATED,
  PANEL_PLAN,
  PLAN_REVISION_UPDATED,
  panelReloadForBroadcast,
  shouldApplyRevision,
} from "@/lib/collaboration-reload.mjs";
import { cn } from "@/lib/utils";
import { useCapabilities } from "@/hooks/useCapabilities";
import { CAPABILITY_IDS, capabilityControlProps } from "@/lib/capabilities";
import { BrandKitPanel, type BrandKitResource } from "./BrandKitPanel";
import { CapabilityStatus } from "./CapabilityGate";
import { CreativeVariantsPanel } from "./CreativeVariantsPanel";

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

type EditorialLearningStatus = {
  enabled: boolean;
  minimum_examples: number;
};

type EditorialLearningPolicy = {
  training_enabled: boolean;
  revoked_at?: string | null;
};

function handoffPlanApproval(
  identity: PlanJobIdentity,
  statusUrl: string,
  onProposalCreated: ScalePanelProps["onProposalCreated"],
): identity is Extract<PlanJobIdentity, { status: "awaiting_approval" }> {
  if (identity.status !== "awaiting_approval") return false;
  onProposalCreated({
    proposalId: identity.proposalId,
    proposalRevision: identity.proposalRevision,
    statusUrl,
  });
  return true;
}

interface ScalePanelProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  activePlan: EditPlanRecordSummary | null;
  captionTrack: CaptionTrackSummary | null;
  effectsConfig: EffectsConfigSummary | null;
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
  onProposalCreated: (proposal: {
    proposalId: string;
    proposalRevision: number;
    statusUrl: string | null;
  }) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onEffectsConfigUpdated: (config: EffectsConfigSummary) => void;
}

export function ScalePanel(props: ScalePanelProps) {
  const { projectId, workspaceId, userId, activePlan, onPlanUpdated } = props;
  const { resolve: resolveCapabilityState } = useCapabilities(workspaceId);
  const stemsCapability = resolveCapabilityState(CAPABILITY_IDS.stemSeparation);
  const videoGenerationCapability = resolveCapabilityState(
    CAPABILITY_IDS.videoGeneration,
  );
  const stemsControl = capabilityControlProps(stemsCapability);
  const videoGenerationControl = capabilityControlProps(
    videoGenerationCapability,
  );
  const [resources, setResources] = useState<Resource[]>([]);
  const [templates, setTemplates] = useState<Resource[]>([]);
  const [audio, setAudio] = useState<Record<string, unknown>>({});
  const [analytics, setAnalytics] = useState<Record<string, unknown>>({});
  const [collaboration, setCollaboration] = useState<Resource | null>(null);
  const [editorialStatus, setEditorialStatus] =
    useState<EditorialLearningStatus | null>(null);
  const [editorialPolicy, setEditorialPolicy] =
    useState<EditorialLearningPolicy | null>(null);
  const [presenceCount, setPresenceCount] = useState(1);
  const [onlineMembers, setOnlineMembers] = useState<
    { userId: string; name: string }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [generatedPrompt, setGeneratedPrompt] = useState("");
  const [generatedImage, setGeneratedImage] = useState("");
  const [profileName, setProfileName] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [batchProjects, setBatchProjects] = useState(projectId);
  const [regenerateLayers, setRegenerateLayers] = useState<
    Record<"captions" | "broll" | "music" | "effects", boolean>
  >({ captions: false, broll: false, music: false, effects: false });
  const [editingTemplateId, setEditingTemplateId] = useState<string | null>(
    null,
  );
  const [editingTemplateName, setEditingTemplateName] = useState("");
  const [pendingDeleteTemplateId, setPendingDeleteTemplateId] = useState<
    string | null
  >(null);
  const collaborationRevision = useRef(-1);
  const trackedPlanRevision = useRef(-1);
  const [lifecycleController] = useState(() => new AbortController());

  useEffect(() => {
    return () => lifecycleController.abort();
  }, [lifecycleController]);

  const load = useCallback(async () => {
    const [
      resourceData,
      templateData,
      audioData,
      analyticsData,
      collaborationData,
      editorialStatusData,
      editorialPolicyData,
    ] = await Promise.all([
      editorApiRequest<{ resources: Resource[] }>(
        `${env.apiBaseUrl}/projects/${projectId}/phase5/resources`,
        auth({ workspaceId }, lifecycleController.signal),
      ),
      editorApiRequest<{ resources: Resource[] }>(
        `${env.apiBaseUrl}/phase5/templates`,
        auth({ workspaceId }, lifecycleController.signal),
      ),
      editorApiRequest<{ audio_intelligence: Record<string, unknown> }>(
        `${env.apiBaseUrl}/projects/${projectId}/phase5/audio-intelligence`,
        auth({ workspaceId }, lifecycleController.signal),
      ).catch(() => ({ audio_intelligence: {} })),
      editorApiRequest<Record<string, unknown>>(
        `${env.apiBaseUrl}/projects/${projectId}/analytics/ai-edits`,
        auth({ workspaceId }, lifecycleController.signal),
      ),
      editorApiRequest<Resource>(
        `${env.apiBaseUrl}/projects/${projectId}/phase5/collaboration`,
        auth({ workspaceId }, lifecycleController.signal),
      ),
      editorApiRequest<EditorialLearningStatus>(
        `${env.apiBaseUrl}/editorial-learning/status`,
        auth({ workspaceId }, lifecycleController.signal),
      ).catch(() => null),
      editorApiRequest<EditorialLearningPolicy>(
        `${env.apiBaseUrl}/editorial-learning/policy`,
        auth({ workspaceId }, lifecycleController.signal),
      ).catch(() => null),
    ]);
    setResources(resourceData.resources);
    setTemplates(templateData.resources);
    setAudio(audioData.audio_intelligence);
    setAnalytics(analyticsData);
    setCollaboration(collaborationData);
    setEditorialStatus(editorialStatusData);
    setEditorialPolicy(editorialPolicyData);
    const shared = (collaborationData.payload?.manifest ??
      null) as EditPlanRecordSummary | null;
    if (
      shared?.id &&
      collaborationData.revision > collaborationRevision.current
    ) {
      collaborationRevision.current = collaborationData.revision;
      onPlanUpdated(shared);
    }
  }, [lifecycleController, onPlanUpdated, projectId, workspaceId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load().catch((error) => setMessage((error as Error).message));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    // Keep the conflict tracker aligned with whatever plan the editor shows, so
    // a broadcast is judged "newer" only when it actually advances past it.
    const revision = activePlan?.revision;
    if (
      typeof revision === "number" &&
      revision > trackedPlanRevision.current
    ) {
      trackedPlanRevision.current = revision;
    }
  }, [activePlan]);

  const refreshOnlineMembers = useCallback(
    async (presentIds: Set<string>) => {
      // Presence carries only user ids; names come from the workspace-scoped
      // members directory (no PII beyond a display name).
      try {
        const directory = await editorApiRequest<{
          members: { user_id: string; name: string; role: string }[];
        }>(
          `${env.apiBaseUrl}/team/members`,
          auth({ workspaceId }, lifecycleController.signal),
        );
        const byId = new Map(directory.members.map((m) => [m.user_id, m.name]));
        setOnlineMembers(
          Array.from(presentIds).map((id) => ({
            userId: id,
            name: byId.get(id) ?? `Member ${id.slice(0, 8)}`,
          })),
        );
      } catch {
        // The indicator still shows a count even if names can't be fetched.
        setOnlineMembers(
          Array.from(presentIds).map((id) => ({
            userId: id,
            name: `Member ${id.slice(0, 8)}`,
          })),
        );
      }
    },
    [lifecycleController, workspaceId],
  );

  useEffect(() => {
    const supabase = createClient();
    // Task 25 private topic for this project's live channel; RLS
    // (realtime.motionly_can_access_topic) authorizes only active members of
    // the owning workspace. The browser's authenticated client supplies the JWT.
    const channel = supabase.channel(`project:${projectId}`, {
      config: { private: true, presence: { key: userId } },
    });

    const applyBroadcast = (message: {
      event: string;
      payload?: Record<string, unknown>;
    }) => {
      const mapped = panelReloadForBroadcast(message);
      if (!mapped) return;
      const tracked =
        mapped.panel === PANEL_PLAN
          ? trackedPlanRevision.current
          : collaborationRevision.current;
      if (!shouldApplyRevision(mapped.revision, tracked)) return;
      // Advance the tracked revision so our next save sends a fresh
      // expected_revision and does not conflict needlessly, then reload the
      // one affected panel's data.
      if (mapped.revision !== null) {
        if (mapped.panel === PANEL_PLAN) {
          trackedPlanRevision.current = mapped.revision;
        } else {
          collaborationRevision.current = mapped.revision;
        }
      }
      void load().catch((error) => setMessage((error as Error).message));
    };

    channel
      .on("presence", { event: "sync" }, () => {
        const ids = new Set(Object.keys(channel.presenceState()));
        setPresenceCount(ids.size || 1);
        void refreshOnlineMembers(ids);
      })
      .on("broadcast", { event: MANIFEST_UPDATED }, (message) =>
        applyBroadcast(message),
      )
      .on("broadcast", { event: PLAN_REVISION_UPDATED }, (message) =>
        applyBroadcast(message),
      )
      .subscribe(async (status) => {
        if (status === "SUBSCRIBED") {
          await channel.track({
            user_id: userId,
            project_id: projectId,
            online_at: new Date().toISOString(),
          });
        }
      });
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [load, projectId, refreshOnlineMembers, userId]);

  useEffect(() => {
    if (
      !resources.some((item) => ["queued", "processing"].includes(item.status))
    )
      return;
    return startAdaptivePolling(() => load(), {
      intervalMs: 3000,
      maxIntervalMs: 9000,
    });
  }, [load, resources]);

  const profiles = useMemo(
    () => resources.filter((item) => item.kind === "style_profile"),
    [resources],
  );
  const speech = (audio.emotion_pacing ?? {}) as Record<string, unknown>;
  const rhythm = (speech.speech_rhythm ?? {}) as Record<string, unknown>;
  const summary = (analytics.summary ?? {}) as Record<string, unknown>;
  const analyticsLayers = (summary.layers ?? []) as Array<
    Record<string, unknown>
  >;
  const trainingConsentReady = Boolean(
    editorialStatus?.enabled &&
    editorialPolicy?.training_enabled &&
    !editorialPolicy.revoked_at,
  );
  const trainingMinimumExamples = editorialStatus?.minimum_examples ?? 100;

  async function action<T>(work: () => Promise<T>, success: string) {
    setBusy(true);
    setMessage(null);
    try {
      await work();
      await load();
      setMessage(success);
    } catch (error) {
      if (lifecycleController.signal.aborted) return;
      setMessage((error as Error).message);
    } finally {
      if (!lifecycleController.signal.aborted) setBusy(false);
    }
  }

  const regenerateSelection = Object.entries(regenerateLayers)
    .filter(([, on]) => on)
    .map(([layer]) => layer);

  async function regenerateLayersNow() {
    // Unified regeneration (apps/api/app/api/regeneration.py): rebuild only the
    // chosen layers (captions, B-roll, music, effects) while preserving the rest
    // and keeping the current export. Creates a new artifact version.
    await action(
      () =>
        editorApiRequest(`${env.apiBaseUrl}/projects/${projectId}/regenerate`, {
          ...auth(props, lifecycleController.signal),
          method: "POST",
          body: { layers: regenerateSelection, force: true },
          friendlyError:
            "Those layers could not be regenerated. Pick at least one and try again.",
        }),
      `Regeneration queued for ${regenerateSelection.join(", ")}.`,
    );
    setRegenerateLayers({
      captions: false,
      broll: false,
      music: false,
      effects: false,
    });
  }

  async function renameTemplate(template: Resource) {
    const nextName = editingTemplateName.trim();
    if (!nextName) return;
    await action(
      () =>
        editorApiRequest(`${env.apiBaseUrl}/phase5/templates/${template.id}`, {
          ...auth(props, lifecycleController.signal),
          method: "PATCH",
          body: { name: nextName },
          friendlyError: "The template could not be renamed.",
        }),
      "Template renamed.",
    );
    setEditingTemplateId(null);
    setEditingTemplateName("");
  }

  async function deleteTemplate(template: Resource) {
    await action(
      () =>
        editorApiRequest(`${env.apiBaseUrl}/phase5/templates/${template.id}`, {
          ...auth(props, lifecycleController.signal),
          method: "DELETE",
          friendlyError: "The template could not be deleted.",
        }),
      "Template deleted.",
    );
    setPendingDeleteTemplateId(null);
  }

  async function cancelBatch(batch: Resource) {
    await action(
      () =>
        editorApiRequest(
          `${env.apiBaseUrl}/phase5/batches/${batch.id}/cancel`,
          {
            ...auth(props, lifecycleController.signal),
            method: "POST",
            body: {},
            friendlyError: "The batch could not be canceled.",
          },
        ),
      "Batch canceled.",
    );
  }

  const batches = resources.filter((item) => item.kind === "batch");

  async function uploadReferences(profile: Resource, files: FileList | null) {
    if (!files) return;
    const existingCount = Array.isArray(profile.payload.references)
      ? profile.payload.references.length
      : 0;
    const selected = Array.from(files).slice(0, Math.max(0, 5 - existingCount));
    await action(async () => {
      for (const file of selected) {
        // One resumable, server-verified path for every media type: init →
        // parts → confirm → complete (verified checksum/size) via the shared
        // helper against the style-reference resumable multipart endpoints.
        const base = `${env.apiBaseUrl}/projects/${projectId}/phase5/style-profiles/${profile.id}/references/multipart`;
        const transport: ResumableUploadTransport = {
          async initUpload() {
            const initialized = await editorApiRequest<{
              reference_id: string;
              session: GenericMediaSession;
            }>(`${base}/init`, {
              ...auth(props, lifecycleController.signal),
              method: "POST",
              body: {
                filename: file.name,
                mime_type: file.type || "video/mp4",
                file_size_bytes: file.size,
              },
            });
            return genericInitToHelper(initialized.session);
          },
          putBlob: (url, blob, headers, signal, onProgress) =>
            putBlobWithProgress(url, blob, headers, signal, onProgress),
          async refreshSession(sessionId) {
            return genericRefreshToHelper(
              await editorApiRequest<GenericMediaSession>(
                `${base}/${sessionId}`,
                auth(props, lifecycleController.signal),
              ),
            );
          },
          async confirmPart(sessionId, partNumber, etag) {
            return editorApiRequest(
              `${base}/${sessionId}/parts/${partNumber}/complete`,
              {
                ...auth(props, lifecycleController.signal),
                method: "POST",
                body: etag ? { etag } : {},
              },
            );
          },
          async completeUpload(sessionId) {
            await editorApiRequest(`${base}/${sessionId}/complete`, {
              ...auth(props, lifecycleController.signal),
              method: "POST",
              body: {},
            });
            return { status: "completed" } as never;
          },
        };
        await runResumableUpload({
          file,
          transport,
          signal: lifecycleController.signal,
          onProgress: (progress) =>
            setMessage(`Uploading ${file.name}… ${progress.percent}%`),
        });
      }
    }, "Style references uploaded.");
  }

  async function syncManifest() {
    if (!activePlan || !collaboration) return;
    await action(async () => {
      const updated = await editorApiRequest<Resource>(
        `${env.apiBaseUrl}/projects/${projectId}/phase5/collaboration`,
        {
          ...auth(props, lifecycleController.signal),
          method: "PATCH",
          body: { base_revision: collaboration.revision, manifest: activePlan },
        },
      );
      collaborationRevision.current = updated.revision;
      setCollaboration(updated);
    }, "Shared manifest synchronized.");
  }

  async function applySavedTemplate(template: Resource) {
    const settings = (template.payload.settings ?? {}) as Record<
      string,
      unknown
    >;
    const payload = await editorApiRequest<{
      plan?: EditPlanRecordSummary;
      status: string;
      job_id?: string;
      status_url?: string;
    }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
      ...auth(props, lifecycleController.signal),
      method: "POST",
      body: { saved_template_id: template.id },
    });

    let plan: EditPlanRecordSummary;
    if (payload.status === "queued" && payload.status_url) {
      setMessage("Applying template: enqueued...");
      const identity = await pollPlanJob(
        `${env.apiBaseUrl}${payload.status_url}`,
        auth(props, lifecycleController.signal),
        (progress) => {
          setMessage(`Applying template: ${progress}%`);
        },
        true,
      );
      if (
        handoffPlanApproval(
          identity,
          payload.status_url,
          props.onProposalCreated,
        )
      ) {
        setMessage(
          "Template proposal ready. Review it in the AI panel before applying it.",
        );
        return;
      }
      const planId = identity.planId;
      const planList = await editorApiRequest<{
        plans: EditPlanRecordSummary[];
      }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans`,
        auth(props, lifecycleController.signal),
      );
      const foundPlan = planList.plans.find((p) => p.id === planId);
      if (!foundPlan) {
        throw new Error(
          "Template plan was generated successfully but could not be loaded.",
        );
      }
      plan = foundPlan;
    } else {
      plan = payload.plan as EditPlanRecordSummary;
    }

    onPlanUpdated(plan);

    const captionSettings = settings.caption_settings as
      | Record<string, unknown>
      | undefined;
    if (captionSettings && props.captionTrack) {
      const captions = await editorApiRequest<{ track: CaptionTrackSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/captions/${props.captionTrack.id}`,
        {
          ...auth(props, lifecycleController.signal),
          method: "PATCH",
          body: captionSettings,
        },
      );
      props.onCaptionTrackUpdated(captions.track);
    }
    const effectsSettings = settings.effects_settings as
      | Record<string, unknown>
      | undefined;
    if (effectsSettings && Object.keys(effectsSettings).length) {
      const effects = await editorApiRequest<{
        config?: EffectsConfigSummary | null;
        status: "ready" | "queued";
        status_url?: string | null;
      }>(`${env.apiBaseUrl}/projects/${projectId}/effects`, {
        ...auth(props, lifecycleController.signal),
        method: "PATCH",
        body: effectsSettings,
      });
      const config = await resolveQueuedLayer({
        submission: effects,
        immediate: effects.config,
        apiBaseUrl: env.apiBaseUrl,
        options: auth(props, lifecycleController.signal),
        load: () =>
          editorApiRequest<EffectsConfigSummary | null>(
            `${env.apiBaseUrl}/projects/${projectId}/effects`,
            auth(props, lifecycleController.signal),
          ),
        resourceLabel: "Template effects",
        onProgress: (progress) => setMessage(`Applying effects: ${progress}%`),
      });
      props.onEffectsConfigUpdated(config);
    }
  }

  async function applyBrandKit(kit: BrandKitResource) {
    const settings = kit.payload.settings ?? {};
    const toneRules = settings.tone_rules ?? {};
    const toneDirection = [
      toneRules.voice ? `Brand voice: ${toneRules.voice}` : "",
      ...(toneRules.do_rules ?? []).map((rule) => `Always: ${rule}`),
      ...(toneRules.avoid_rules ?? []).map((rule) => `Avoid: ${rule}`),
    ]
      .filter(Boolean)
      .join("\n");
    let plan = activePlan;
    if (
      settings.style_profile_id ||
      settings.saved_template_id ||
      toneDirection
    ) {
      const payload = await editorApiRequest<{
        plan?: EditPlanRecordSummary;
        status: string;
        status_url?: string;
      }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
        ...auth(props, lifecycleController.signal),
        method: "POST",
        body: {
          ...(settings.style_profile_id
            ? { style_profile_id: settings.style_profile_id }
            : {}),
          ...(settings.saved_template_id
            ? { saved_template_id: settings.saved_template_id }
            : {}),
          ...(toneDirection ? { feedback: toneDirection } : {}),
        },
      });
      if (payload.status === "queued" && payload.status_url) {
        setMessage("Applying Brand Kit direction: enqueued…");
        const identity = await pollPlanJob(
          `${env.apiBaseUrl}${payload.status_url}`,
          auth(props, lifecycleController.signal),
          (progress) =>
            setMessage(`Applying Brand Kit direction: ${progress}%`),
          true,
        );
        if (
          handoffPlanApproval(
            identity,
            payload.status_url,
            props.onProposalCreated,
          )
        ) {
          setMessage(
            "Brand Kit proposal ready. Review it in the AI panel before applying it.",
          );
          return;
        }
        const planId = identity.planId;
        const planList = await editorApiRequest<{
          plans: EditPlanRecordSummary[];
        }>(
          `${env.apiBaseUrl}/projects/${projectId}/plans`,
          auth(props, lifecycleController.signal),
        );
        plan = planList.plans.find((item) => item.id === planId) ?? null;
      } else {
        plan = payload.plan ?? null;
      }
      if (!plan)
        throw new Error(
          "Brand Kit direction completed but no edit plan was returned.",
        );
      onPlanUpdated(plan);
    }

    if (settings.caption_settings && props.captionTrack) {
      const captions = await editorApiRequest<{ track: CaptionTrackSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/captions/${props.captionTrack.id}`,
        {
          ...auth(props, lifecycleController.signal),
          method: "PATCH",
          body: settings.caption_settings,
          friendlyError: "Brand Kit caption preset could not be applied.",
        },
      );
      props.onCaptionTrackUpdated(captions.track);
    }

    if (
      settings.effects_settings &&
      Object.keys(settings.effects_settings).length
    ) {
      const effects = await editorApiRequest<{
        config?: EffectsConfigSummary | null;
        status: "ready" | "queued";
        status_url?: string | null;
      }>(`${env.apiBaseUrl}/projects/${projectId}/effects`, {
        ...auth(props, lifecycleController.signal),
        method: "PATCH",
        body: settings.effects_settings,
        friendlyError: "Brand Kit look settings could not be applied.",
      });
      const config = await resolveQueuedLayer({
        submission: effects,
        immediate: effects.config,
        apiBaseUrl: env.apiBaseUrl,
        options: auth(props, lifecycleController.signal),
        load: () =>
          editorApiRequest<EffectsConfigSummary | null>(
            `${env.apiBaseUrl}/projects/${projectId}/effects`,
            auth(props, lifecycleController.signal),
          ),
        resourceLabel: "Brand Kit effects",
        onProgress: (progress) =>
          setMessage(`Applying Brand Kit look: ${progress}%`),
      });
      props.onEffectsConfigUpdated(config);
    }

    const assets = [
      ["intro", settings.intro_asset],
      ["outro", settings.outro_asset],
      ["logo", settings.logo_asset],
      ["watermark", settings.watermark_asset],
    ] as const;
    const hasAssets = assets.some(([, asset]) => asset?.asset_url);
    if (!plan) {
      if (hasAssets) {
        throw new Error(
          "Generate an AI edit before applying Brand Kit media assets.",
        );
      }
      return;
    }
    if (!hasAssets) return;
    const timelineDuration =
      plan.plan.timeline?.duration_ms ?? plan.target_duration_s * 1000;
    const assetDuration = Math.min(3000, Math.max(1, timelineDuration));
    const brandElements = assets.flatMap(([kind, asset]) => {
      if (!asset?.asset_url) return [];
      const isVideo = kind === "intro" || kind === "outro";
      const start =
        kind === "outro" ? Math.max(0, timelineDuration - assetDuration) : 0;
      const end = isVideo ? start + assetDuration : timelineDuration;
      const positioning =
        kind === "logo"
          ? { x_pct: 12, y_pct: 10, width_pct: 18, height_pct: 10, opacity: 1 }
          : kind === "watermark"
            ? {
                x_pct: 88,
                y_pct: 90,
                width_pct: 16,
                height_pct: 9,
                opacity: 0.82,
              }
            : {
                x_pct: 50,
                y_pct: 50,
                width_pct: 100,
                height_pct: 100,
                opacity: 1,
              };
      return [
        {
          id: `brand-kit-${kit.id}-${kind}`,
          type: isVideo ? "broll" : "graphic",
          name: asset.label || `Brand ${kind}`,
          zone: "canvas",
          start_ms: start,
          end_ms: Math.max(start + 1, end),
          rotation_deg: 0,
          clip_id: null,
          properties: {
            assetUrl: asset.asset_url,
            altText: asset.alt_text || asset.label || `Brand ${kind}`,
            source: "brand_kit",
            brandKitId: kit.id,
            objectFit: isVideo ? "cover" : "contain",
          },
          ai_reason: "Applied from Brand Kit",
          confidence: 1,
          ...positioning,
        },
      ];
    });
    const currentElements = plan.plan.manual_elements ?? [];
    const nextElements = [
      ...currentElements.filter(
        (element) => String(element.properties?.brandKitId ?? "") !== kit.id,
      ),
      ...brandElements,
    ];
    const updated = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
      `${env.apiBaseUrl}/projects/${projectId}/plans/${plan.id}/manual-elements`,
      {
        ...auth(props, lifecycleController.signal),
        method: "PATCH",
        body: { manual_elements: nextElements },
        friendlyError: "Brand Kit assets could not be added to the timeline.",
      },
    );
    onPlanUpdated(updated.plan);
  }

  async function applyStyleProfile(profile: Resource) {
    const payload = await editorApiRequest<{
      plan?: EditPlanRecordSummary;
      status: string;
      job_id?: string;
      status_url?: string;
    }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
      ...auth(props, lifecycleController.signal),
      method: "POST",
      body: { style_profile_id: profile.id },
    });

    let plan: EditPlanRecordSummary;
    if (payload.status === "queued" && payload.status_url) {
      setMessage("Applying style: enqueued...");
      const identity = await pollPlanJob(
        `${env.apiBaseUrl}${payload.status_url}`,
        auth(props, lifecycleController.signal),
        (progress) => {
          setMessage(`Applying style: ${progress}%`);
        },
        true,
      );
      if (
        handoffPlanApproval(
          identity,
          payload.status_url,
          props.onProposalCreated,
        )
      ) {
        setMessage(
          "Style proposal ready. Review it in the AI panel before applying it.",
        );
        return;
      }
      const planId = identity.planId;
      const planList = await editorApiRequest<{
        plans: EditPlanRecordSummary[];
      }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans`,
        auth(props, lifecycleController.signal),
      );
      const foundPlan = planList.plans.find((p) => p.id === planId);
      if (!foundPlan) {
        throw new Error(
          "Style plan was generated successfully but could not be loaded.",
        );
      }
      plan = foundPlan;
    } else {
      plan = payload.plan as EditPlanRecordSummary;
    }

    onPlanUpdated(plan);
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-2 border-b border-hairline pb-3">
        <div>
          <h2 className="text-lg font-bold tracking-tight text-foreground">
            Intelligence & Scale
          </h2>
          <p className="text-xs font-medium text-muted-foreground">
            Phase 5 production workflows.
          </p>
        </div>
        <span
          className="inline-flex items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-xs font-semibold text-foreground tabular-nums shadow-neu-raised-sm"
          title={
            onlineMembers.length
              ? onlineMembers.map((member) => member.name).join(", ")
              : undefined
          }
        >
          <Users className="size-3.5 text-primary" strokeWidth={2} />
          {presenceCount} online
        </span>
      </div>

      <Section title="Regenerate layers" icon={RefreshCw}>
        <p className="text-[11px] text-muted-foreground">
          Rebuild only the layers you pick. Other layers and your current export
          are preserved; this creates a new version.
        </p>
        <fieldset className="mt-3 grid grid-cols-2 gap-2">
          <legend className="sr-only">Layers to regenerate</legend>
          {(
            [
              ["captions", "Captions"],
              ["broll", "B-roll"],
              ["music", "Music"],
              ["effects", "Effects"],
            ] as const
          ).map(([layer, label]) => (
            <label
              key={layer}
              className="flex min-h-11 cursor-pointer items-center justify-between gap-2 rounded-neu bg-surface px-3 text-xs font-semibold text-foreground shadow-neu-raised-sm select-none"
            >
              {label}
              <input
                type="checkbox"
                aria-label={`Regenerate ${label}`}
                checked={regenerateLayers[layer]}
                onChange={(event) =>
                  setRegenerateLayers((current) => ({
                    ...current,
                    [layer]: event.target.checked,
                  }))
                }
                className="size-4.5 accent-primary cursor-pointer"
              />
            </label>
          ))}
        </fieldset>
        <Button
          disabled={busy || regenerateSelection.length === 0}
          className="mt-3 w-full text-xs gap-1.5"
          onClick={() => void regenerateLayersNow()}
        >
          <RefreshCw className="size-3.5" strokeWidth={2} aria-hidden="true" />
          Regenerate selected layers
        </Button>
      </Section>

      <BrandKitPanel
        projectId={projectId}
        workspaceId={workspaceId}
        styleProfiles={profiles}
        savedTemplates={templates}
        captionTrack={props.captionTrack}
        effectsConfig={props.effectsConfig}
        onApply={applyBrandKit}
      />

      <CreativeVariantsPanel
        projectId={projectId}
        workspaceId={workspaceId}
        activePlan={activePlan}
        onPlanUpdated={onPlanUpdated}
      />

      <Section title="Audio intelligence" icon={Activity}>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Metric
            label="Emotion"
            value={String(speech.aggregate_emotion ?? "pending")}
          />
          <Metric
            label="Rhythm"
            value={
              rhythm.bpm_equivalent
                ? `${rhythm.bpm_equivalent} BPM-eq`
                : "pending"
            }
          />
          <Metric label="Pauses" value={String(speech.pause_count ?? 0)} />
          <Metric
            label="Windows"
            value={String(
              Array.isArray(speech.windows) ? speech.windows.length : 0,
            )}
          />
        </div>
        <Button
          disabled={busy}
          aria-disabled={stemsControl["aria-disabled"]}
          aria-describedby={
            stemsControl.statusText ? "scale-stems-status" : undefined
          }
          data-capability={stemsControl["data-capability"]}
          data-capability-status={stemsControl["data-capability-status"]}
          variant="secondary"
          className="mt-3.5 w-full text-xs gap-1.5"
          onClick={() => {
            if (stemsControl.blocked) return;
            action(
              () =>
                editorApiRequest(
                  `${env.apiBaseUrl}/projects/${projectId}/phase5/stems`,
                  {
                    ...auth(props, lifecycleController.signal),
                    method: "POST",
                    body: { model: "htdemucs" },
                  },
                ),
              "Demucs separation queued.",
            );
          }}
        >
          Separate vocals and music
        </Button>
        <CapabilityStatus state={stemsCapability} id="scale-stems-status" />
      </Section>

      <Section title="Generated B-roll" icon={Film}>
        <div className="space-y-2">
          <input
            className="min-h-[40px] w-full px-3 text-xs font-medium text-foreground border border-control bg-surface rounded-neu shadow-neu-inset-sm focus:outline-none focus:border-focus focus:ring-4 focus:ring-primary/30 transition-shadow"
            placeholder="Describe the generated shot"
            value={generatedPrompt}
            onChange={(event) => setGeneratedPrompt(event.target.value)}
          />
          <input
            className="min-h-[40px] w-full px-3 text-xs font-medium text-foreground border border-control bg-surface rounded-neu shadow-neu-inset-sm focus:outline-none focus:border-focus focus:ring-4 focus:ring-primary/30 transition-shadow"
            placeholder="Public prompt image URL"
            value={generatedImage}
            onChange={(event) => setGeneratedImage(event.target.value)}
          />
        </div>
        <Button
          disabled={busy || generatedPrompt.length < 3 || !generatedImage}
          aria-disabled={videoGenerationControl["aria-disabled"]}
          aria-describedby={
            videoGenerationControl.statusText
              ? "scale-generated-broll-status"
              : undefined
          }
          data-capability={videoGenerationControl["data-capability"]}
          data-capability-status={
            videoGenerationControl["data-capability-status"]
          }
          className="mt-3 w-full text-xs"
          onClick={() => {
            if (videoGenerationControl.blocked) return;
            action(
              () =>
                editorApiRequest(
                  `${env.apiBaseUrl}/projects/${projectId}/phase5/generated-broll`,
                  {
                    ...auth(props, lifecycleController.signal),
                    method: "POST",
                    body: {
                      prompt: generatedPrompt,
                      prompt_image_url: generatedImage,
                      duration_s: 5,
                      ratio: "768:1280",
                    },
                  },
                ),
              "Generated B-roll queued.",
            );
          }}
        >
          Generate premium B-roll
        </Button>
        <CapabilityStatus
          state={videoGenerationCapability}
          id="scale-generated-broll-status"
        />
      </Section>

      <Section title="Reference style analysis" icon={BrainCircuit}>
        <div className="flex gap-2">
          <input
            className="min-h-[40px] min-w-0 flex-1 px-3 text-xs font-medium text-foreground border border-control bg-surface rounded-neu shadow-neu-inset-sm focus:outline-none focus:border-focus focus:ring-4 focus:ring-primary/30 transition-shadow"
            placeholder="Style profile name"
            value={profileName}
            onChange={(event) => setProfileName(event.target.value)}
          />
          <Button
            disabled={busy || !profileName.trim()}
            onClick={() =>
              action(
                () =>
                  editorApiRequest(
                    `${env.apiBaseUrl}/projects/${projectId}/phase5/style-profiles`,
                    {
                      ...auth(props, lifecycleController.signal),
                      method: "POST",
                      body: {
                        name: profileName,
                        target_style: activePlan?.style ?? "creator_influencer",
                      },
                    },
                  ),
                "Style profile created.",
              )
            }
            className="text-xs"
          >
            Create
          </Button>
        </div>
        {profiles.map((profile) => {
          const references = (profile.payload.references ?? []) as Array<{
            status: string;
          }>;
          return (
            <div
              key={profile.id}
              className="mt-3.5 rounded-neu-md bg-surface p-4 shadow-neu-raised-sm space-y-3"
            >
              <p className="text-xs font-semibold text-foreground">
                {profile.name} ·{" "}
                <span className="text-muted-foreground tabular-nums">
                  {references.length}/5 references
                </span>{" "}
                ·{" "}
                <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold text-primary shadow-neu-inset-sm">
                  {profile.status}
                </span>
              </p>
              <input
                type="file"
                accept="video/*"
                multiple
                disabled={busy || references.length >= 5}
                className="w-full text-xs font-medium text-muted-foreground file:mr-3 file:py-1 file:px-2.5 file:rounded-neu-sm file:border-0 file:text-xs file:font-semibold file:bg-primary file:text-primary-foreground file:shadow-neu-raised-sm hover:file:shadow-neu-raised transition-shadow"
                onChange={(event) =>
                  void uploadReferences(profile, event.target.files)
                }
              />
              <div className="flex gap-2 pt-1">
                <Button
                  variant="secondary"
                  disabled={
                    busy ||
                    references.filter((item) => item.status === "ready")
                      .length < 5
                  }
                  onClick={() =>
                    action(
                      () =>
                        editorApiRequest(
                          `${env.apiBaseUrl}/projects/${projectId}/phase5/style-profiles/${profile.id}/train`,
                          {
                            ...auth(props, lifecycleController.signal),
                            method: "POST",
                            body: {},
                          },
                        ),
                      "Style analysis queued.",
                    )
                  }
                  className="text-xs py-1.5 h-8"
                >
                  Analyze
                </Button>
                {profile.status === "ready" ? (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      action(
                        () => applyStyleProfile(profile),
                        "Reference style applied.",
                      )
                    }
                    className="text-xs py-1.5 h-8"
                  >
                    Apply
                  </Button>
                ) : null}
              </div>
            </div>
          );
        })}
      </Section>

      <Section title="Template library & batch" icon={Layers3}>
        <div className="flex gap-2">
          <input
            className="min-h-[40px] min-w-0 flex-1 px-3 text-xs font-medium text-foreground border border-control bg-surface rounded-neu shadow-neu-inset-sm focus:outline-none focus:border-focus focus:ring-4 focus:ring-primary/30 transition-shadow"
            placeholder="Template name"
            value={templateName}
            onChange={(event) => setTemplateName(event.target.value)}
          />
          <Button
            disabled={busy || !activePlan || !templateName.trim()}
            onClick={() =>
              action(
                () =>
                  editorApiRequest(`${env.apiBaseUrl}/phase5/templates`, {
                    ...auth(props, lifecycleController.signal),
                    method: "POST",
                    body: {
                      name: templateName,
                      settings: {
                        style: activePlan?.style,
                        target_duration_s: activePlan?.target_duration_s,
                        broll_density: activePlan?.broll_density,
                        direction: activePlan?.prompt,
                        caption_settings: captionTemplateSettings(
                          props.captionTrack,
                        ),
                        effects_settings: effectsTemplateSettings(
                          props.effectsConfig,
                        ),
                      },
                    },
                  }),
                "Template saved.",
              )
            }
            className="text-xs gap-1.5"
          >
            <Save className="size-3.5" strokeWidth={2} />
            Save
          </Button>
        </div>
        <p className="mt-3.5 text-xs font-semibold text-muted-foreground tabular-nums">
          {templates.length} saved template{templates.length === 1 ? "" : "s"}
        </p>
        <div className="space-y-2 mt-2">
          {templates.map((template) => (
            <div
              key={template.id}
              className="flex flex-col gap-2 rounded-neu-md bg-surface p-3 text-xs shadow-neu-raised-sm"
            >
              <div className="flex items-center justify-between gap-3">
                {editingTemplateId === template.id ? (
                  <input
                    className="min-h-8 min-w-0 flex-1 px-2 text-xs font-medium text-foreground border border-control bg-surface rounded-neu shadow-neu-inset-sm focus:outline-none focus:border-focus focus:ring-4 focus:ring-primary/30"
                    aria-label={`New name for ${template.name}`}
                    value={editingTemplateName}
                    autoFocus
                    onChange={(event) =>
                      setEditingTemplateName(event.target.value)
                    }
                  />
                ) : (
                  <span className="truncate font-bold text-foreground">
                    {template.name}
                  </span>
                )}
                <div className="flex shrink-0 gap-2">
                  {editingTemplateId === template.id ? (
                    <>
                      <Button
                        disabled={busy || !editingTemplateName.trim()}
                        className="min-h-8 h-8 px-3 text-[10px]"
                        onClick={() => void renameTemplate(template)}
                      >
                        Save name
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={busy}
                        className="min-h-8 h-8 px-3 text-[10px]"
                        onClick={() => {
                          setEditingTemplateId(null);
                          setEditingTemplateName("");
                        }}
                      >
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        variant="secondary"
                        disabled={busy}
                        className="min-h-8 h-8 px-3 text-[10px]"
                        onClick={() =>
                          action(
                            () => applySavedTemplate(template),
                            "Template applied to timeline, captions, and effects.",
                          )
                        }
                      >
                        Apply
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={busy}
                        aria-label={`Rename ${template.name}`}
                        className="min-h-8 h-8 px-3 text-[10px]"
                        onClick={() => {
                          setEditingTemplateId(template.id);
                          setEditingTemplateName(template.name);
                        }}
                      >
                        Rename
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={busy}
                        aria-label={`Delete ${template.name}`}
                        className="min-h-8 h-8 px-3 text-[10px] text-danger gap-1"
                        onClick={() => setPendingDeleteTemplateId(template.id)}
                      >
                        <Trash2
                          className="size-3"
                          strokeWidth={2}
                          aria-hidden="true"
                        />
                        Delete
                      </Button>
                    </>
                  )}
                </div>
              </div>
              {pendingDeleteTemplateId === template.id ? (
                <div
                  role="alertdialog"
                  aria-label={`Confirm deleting template ${template.name}`}
                  className="flex items-center justify-between gap-2 rounded-neu-md bg-surface p-2 shadow-neu-raised-sm ring-1 ring-danger/30"
                >
                  <span className="text-[11px] font-semibold text-danger">
                    Delete “{template.name}” permanently?
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="danger"
                      disabled={busy}
                      className="min-h-8 h-8 px-3 text-[10px]"
                      onClick={() => void deleteTemplate(template)}
                    >
                      Confirm delete
                    </Button>
                    <Button
                      variant="secondary"
                      disabled={busy}
                      className="min-h-8 h-8 px-3 text-[10px]"
                      onClick={() => setPendingDeleteTemplateId(null)}
                    >
                      Keep
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
        <textarea
          className="mt-3 min-h-[72px] w-full p-3 text-xs font-medium text-foreground border border-control bg-surface rounded-neu shadow-neu-inset-sm focus:outline-none focus:border-focus focus:ring-4 focus:ring-primary/30 transition-shadow"
          aria-label="Batch project IDs"
          value={batchProjects}
          onChange={(event) => setBatchProjects(event.target.value)}
          placeholder="Project UUIDs, comma separated"
        />
        <Button
          disabled={busy || !batchProjects.trim()}
          variant="secondary"
          className="mt-2 w-full text-xs"
          onClick={() =>
            action(
              () =>
                editorApiRequest(`${env.apiBaseUrl}/phase5/batches`, {
                  ...auth(props, lifecycleController.signal),
                  method: "POST",
                  body: {
                    name: "Same-style batch",
                    project_ids: batchProjects.split(/[\s,]+/).filter(Boolean),
                    style: activePlan?.style ?? null,
                    target_duration_s: activePlan?.target_duration_s ?? 35,
                    broll_density: activePlan?.broll_density ?? "medium",
                    saved_template_id: templates[0]?.id ?? null,
                  },
                }),
              "Batch processing enqueued.",
            )
          }
        >
          Process video batch
        </Button>
        {batches.length ? (
          <div className="mt-3 space-y-2">
            <p className="text-[10px] font-semibold tracking-wide text-muted-foreground">
              Batches
            </p>
            {batches.slice(0, 6).map((batch) => {
              const cancelable = !["completed", "partial", "canceled"].includes(
                batch.status,
              );
              return (
                <div
                  key={batch.id}
                  className="flex items-center justify-between gap-3 rounded-neu-md bg-surface p-3 text-xs shadow-neu-raised-sm"
                >
                  <span className="min-w-0 truncate">
                    <span className="font-semibold text-foreground">
                      {batch.name}
                    </span>{" "}
                    <span className="text-muted-foreground capitalize">
                      {batch.status}
                    </span>
                  </span>
                  <Button
                    variant="secondary"
                    disabled={busy || !cancelable}
                    aria-label={`Cancel batch ${batch.name}`}
                    className="min-h-8 h-8 px-3 text-[10px]"
                    onClick={() => void cancelBatch(batch)}
                  >
                    Cancel
                  </Button>
                </div>
              );
            })}
          </div>
        ) : null}
      </Section>

      <Section title="Collaboration & learning" icon={Users}>
        <Button
          disabled={busy || !activePlan || !collaboration}
          className="w-full text-xs h-11"
          onClick={syncManifest}
        >
          Sync shared manifest
        </Button>
        <p className="mt-2 text-[10px] font-medium text-muted-foreground leading-relaxed">
          V1 collaboration uses whole-timeline optimistic revisions. Concurrent
          conflicts reload latest state.
        </p>
        <p className="mt-3 text-xs font-semibold text-foreground tabular-nums">
          {String(summary.total_events ?? 0)} edit signals ·{" "}
          {String(summary.training_example_count ?? 0)} training examples
        </p>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {analyticsLayers.map((layer) => (
            <div
              key={String(layer.layer)}
              className="rounded-neu-md bg-surface p-2.5 text-[10px] shadow-neu-raised-sm"
            >
              <span className="font-semibold text-foreground">
                {String(layer.layer)}
              </span>
              <span className="block text-muted-foreground mt-0.5 tabular-nums">
                kept {String(layer.kept ?? 0)} · changed{" "}
                {String(layer.changed ?? 0)} · rejected{" "}
                {String(layer.rejected ?? 0)}
              </span>
            </div>
          ))}
        </div>
        {trainingConsentReady ? (
          <>
            <p className="mt-3 text-[10px] font-medium text-muted-foreground">
              Training consent is active. A frozen dataset needs at least{" "}
              {trainingMinimumExamples} consented edit signals; promotion
              remains an explicit admin operation.
            </p>
            <Button
              disabled={busy}
              variant="secondary"
              className="mt-2 w-full text-xs"
              onClick={() =>
                action(
                  () =>
                    editorApiRequest(
                      `${env.apiBaseUrl}/projects/${projectId}/phase5/model-training`,
                      {
                        ...auth(props, lifecycleController.signal),
                        method: "POST",
                        body: { minimum_examples: trainingMinimumExamples },
                      },
                    ),
                  "Preference summary enqueued.",
                )
              }
            >
              Create a consented preference dataset
            </Button>
          </>
        ) : (
          <p className="mt-3 rounded-neu bg-surface p-3 text-[10px] font-medium text-muted-foreground shadow-neu-inset-sm">
            Preference learning is blocked until you enable training consent in{" "}
            <a className="font-semibold text-primary underline" href="/account">
              Account settings
            </a>
            . Analytics signals never imply consent.
          </p>
        )}
      </Section>

      <div className="space-y-1.5 pt-2">
        {resources.slice(0, 8).map((resource) => (
          <div
            key={resource.id}
            className="flex justify-between items-center gap-2 rounded-neu bg-surface px-3 py-2 text-[10px] shadow-neu-raised-sm"
          >
            <span className="truncate font-semibold text-foreground">
              {resource.name}
            </span>
            <span
              className={cn(
                "capitalize font-semibold text-[10px]",
                resource.status === "failed"
                  ? "text-danger"
                  : "text-muted-foreground",
              )}
            >
              {resource.status}
            </span>
          </div>
        ))}
      </div>
      {busy ? (
        <p className="flex items-center gap-2 text-xs font-semibold text-primary pl-1">
          <Loader2
            className="size-4 animate-spin text-primary"
            strokeWidth={2}
          />
          Working…
        </p>
      ) : null}
      {message ? (
        <p
          role="status"
          className="rounded-neu bg-surface p-3.5 text-xs font-semibold text-foreground shadow-neu-inset-sm"
        >
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
    <section className="rounded-neu-lg bg-surface p-4 space-y-3 shadow-neu-raised">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Icon className="size-4 text-primary" strokeWidth={1.75} />
        {title}
      </h3>
      {children}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-neu-md bg-surface p-3 shadow-neu-raised-sm">
      <p className="text-[10px] font-semibold tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="truncate text-sm font-bold text-foreground mt-1">{value}</p>
    </div>
  );
}

function auth(
  props: Pick<ScalePanelProps, "workspaceId">,
  signal?: AbortSignal,
) {
  return {
    workspaceId: props.workspaceId,
    signal,
  };
}

function captionTemplateSettings(track: CaptionTrackSummary | null) {
  return track
    ? {
        preset: track.preset,
        style: track.style,
        hide_filler_words: track.hide_filler_words,
      }
    : {};
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
