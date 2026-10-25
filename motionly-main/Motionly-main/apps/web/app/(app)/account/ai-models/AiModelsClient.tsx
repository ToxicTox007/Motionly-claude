"use client";

// Task 32 — the workspace "AI models" settings client (plan Section 2.11).
//
// Owners and admins edit, per capability: the workspace default, the member
// allow-list, and the lock. Members see the current effective choice read-only.
// Everything is accessible (labelled controls, announced status, text — never
// colour alone) and neutral: an option is shown by its operator display name
// and the opaque option id, never a model id, URL or secret.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Lock } from "lucide-react";
import { Button } from "@/components/button";
import {
  CapabilityOptionRadioGroup,
  capabilityTitle,
  saveErrorText,
} from "@/components/editor/ModelPicker";
import {
  type CapabilityOptions,
  type ModelOption,
  type WorkspaceModelSettings,
  fetchWorkspaceModelSettings,
  labelText,
  optionDisabledReason,
  optionSelectable,
  quotaWeightText,
  saveWorkspaceModelSetting,
} from "@/lib/model-options";
import { useModelOptions } from "@/hooks/useModelOptions";

type SettingByCapability = Map<
  string,
  {
    default_option_id: string | null;
    allowed_option_ids: string[] | null;
    locked: boolean;
  }
>;

function settingsToMap(settings: WorkspaceModelSettings): SettingByCapability {
  const map: SettingByCapability = new Map();
  for (const item of settings) {
    map.set(item.capability, {
      default_option_id: item.default_option_id ?? null,
      allowed_option_ids: item.allowed_option_ids ?? null,
      locked: item.locked ?? false,
    });
  }
  return map;
}

export function AiModelsClient({
  workspaceId,
  canManage,
}: {
  workspaceId: string;
  canManage: boolean;
}) {
  const { catalog, loading, statusUnknown, refresh } =
    useModelOptions(workspaceId);
  const [settings, setSettings] = useState<SettingByCapability>(new Map());
  const [settingsLoaded, setSettingsLoaded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const loaded = await fetchWorkspaceModelSettings({
        workspaceId,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setSettings(settingsToMap(loaded));
      setSettingsLoaded(true);
    })();
    return () => controller.abort();
  }, [workspaceId]);

  const onSettingSaved = useCallback(
    (
      capability: string,
      next: {
        default_option_id: string | null;
        allowed_option_ids: string[] | null;
        locked: boolean;
      },
    ) => {
      setSettings((current) => {
        const copy = new Map(current);
        copy.set(capability, next);
        return copy;
      });
      void refresh();
    },
    [refresh],
  );

  if (loading || !settingsLoaded) {
    return (
      <div
        className="flex items-center gap-2 rounded-neu-lg bg-surface p-6 text-sm font-medium text-muted-foreground shadow-neu-raised"
        role="status"
      >
        <Loader2 className="size-4 animate-spin" strokeWidth={1.75} />
        Loading model options…
      </div>
    );
  }

  if (statusUnknown || catalog.capabilities.length === 0) {
    return (
      <div
        className="rounded-neu-lg bg-surface p-6 text-sm font-medium text-muted-foreground shadow-neu-raised"
        role="status"
      >
        Model options are unavailable right now. Your operator may not have
        configured selectable models for any capability yet.
      </div>
    );
  }

  return (
    <div className="space-y-5" data-testid="ai-models-settings">
      {catalog.capabilities.map((capability) => (
        <CapabilitySettingCard
          key={capability.capability}
          workspaceId={workspaceId}
          canManage={canManage}
          capability={capability}
          setting={
            settings.get(capability.capability) ?? {
              default_option_id: null,
              allowed_option_ids: null,
              locked: false,
            }
          }
          onSaved={(next) => onSettingSaved(capability.capability, next)}
        />
      ))}
    </div>
  );
}

function CapabilitySettingCard({
  workspaceId,
  canManage,
  capability,
  setting,
  onSaved,
}: {
  workspaceId: string;
  canManage: boolean;
  capability: CapabilityOptions;
  setting: {
    default_option_id: string | null;
    allowed_option_ids: string[] | null;
    locked: boolean;
  };
  onSaved: (next: {
    default_option_id: string | null;
    allowed_option_ids: string[] | null;
    locked: boolean;
  }) => void;
}) {
  const [defaultOptionId, setDefaultOptionId] = useState<string | null>(
    setting.default_option_id,
  );
  const [allowed, setAllowed] = useState<string[] | null>(
    setting.allowed_option_ids,
  );
  const [locked, setLocked] = useState(setting.locked);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const title = capabilityTitle(capability.capability);
  const effectiveLabel = useMemo(
    () => effectiveOptionLabel(capability),
    [capability],
  );

  // Members (and anyone without manage rights) see the effective choice only.
  if (!canManage) {
    return (
      <section
        className="rounded-neu-lg bg-surface p-5 shadow-neu-raised"
        data-model-setting={capability.capability}
      >
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <p className="mt-1 text-xs font-medium text-muted-foreground">
          Current choice:{" "}
          <span className="font-semibold">{effectiveLabel}</span>
          {capability.effective_source === "workspace_locked" ? (
            <span className="ml-2 inline-flex items-center gap-1">
              <Lock className="size-3" aria-hidden="true" /> Locked
            </span>
          ) : null}
        </p>
      </section>
    );
  }

  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    const result = await saveWorkspaceModelSetting({
      workspaceId,
      update: {
        capability: capability.capability,
        default_option_id: defaultOptionId,
        allowed_option_ids: allowed,
        locked,
      },
    });
    setBusy(false);
    if (!result.ok) {
      setError(saveErrorText(result.error));
      return;
    }
    setSaved(true);
    onSaved({
      default_option_id: defaultOptionId,
      allowed_option_ids: allowed,
      locked,
    });
  };

  const toggleAllowed = (optionId: string, next: boolean) => {
    setAllowed((current) => {
      // null means "every option the plan allows". The first explicit change
      // seeds the list from the currently selectable options.
      const base =
        current ??
        capability.options
          .filter((option) => optionSelectable(option))
          .map((option) => option.option_id);
      const set = new Set(base);
      if (next) set.add(optionId);
      else set.delete(optionId);
      return Array.from(set);
    });
  };

  const lockId = `lock-${capability.capability}`;
  const allowAllId = `allow-all-${capability.capability}`;

  return (
    <section
      className="space-y-4 rounded-neu-lg bg-surface p-5 shadow-neu-raised"
      data-model-setting={capability.capability}
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {busy ? (
          <Loader2
            className="size-4 animate-spin text-muted-foreground"
            strokeWidth={1.75}
            aria-hidden="true"
          />
        ) : null}
      </div>

      {/* The workspace default (a per-capability radio group). */}
      <CapabilityOptionRadioGroup
        capability={capability}
        value={defaultOptionId}
        onChange={setDefaultOptionId}
        includeDefaultOption
        defaultOptionLabel="Use the operator default"
        disabled={busy}
      />

      {/* The member allow-list. null (allow all) until the owner restricts it. */}
      <fieldset className="rounded-neu-md bg-surface p-4 shadow-neu-inset-sm">
        <legend className="text-sm font-semibold text-foreground">
          Options members may pick
        </legend>
        <label
          htmlFor={allowAllId}
          className="mt-2 flex items-center gap-2 text-xs font-medium text-muted-foreground"
        >
          <input
            id={allowAllId}
            type="checkbox"
            className="size-4 accent-primary"
            checked={allowed === null}
            onChange={(event) => setAllowed(event.target.checked ? null : [])}
            disabled={busy}
          />
          Allow every option this plan permits
        </label>
        {allowed !== null ? (
          <div
            role="group"
            aria-label="Allowed options"
            className="mt-3 space-y-2"
          >
            {capability.options.map((option) => (
              <AllowedOptionRow
                key={option.option_id}
                option={option}
                checked={allowed.includes(option.option_id)}
                disabled={busy}
                onToggle={(next) => toggleAllowed(option.option_id, next)}
              />
            ))}
          </div>
        ) : null}
      </fieldset>

      {/* The project-override lock. */}
      <label
        htmlFor={lockId}
        className="flex items-center gap-2 text-sm font-medium text-foreground"
      >
        <input
          id={lockId}
          type="checkbox"
          className="size-4 accent-primary"
          checked={locked}
          onChange={(event) => setLocked(event.target.checked)}
          disabled={busy}
        />
        <Lock className="size-3.5 text-muted-foreground" aria-hidden="true" />
        Lock this choice (projects cannot override it)
      </label>

      <div className="flex items-center gap-3">
        <Button type="button" onClick={() => void save()} disabled={busy}>
          Save {title}
        </Button>
        {saved ? (
          <span role="status" className="text-xs font-semibold text-success">
            Saved
          </span>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-xs font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function AllowedOptionRow({
  option,
  checked,
  disabled,
  onToggle,
}: {
  option: ModelOption;
  checked: boolean;
  disabled: boolean;
  onToggle: (next: boolean) => void;
}) {
  const blockedReason = optionDisabledReason(option);
  const selectable = optionSelectable(option);
  const id = `allow-${option.option_id}`;
  const name = option.display_name?.trim() || "Standard model";
  const quotaText = quotaWeightText(option.quota_weight);
  return (
    <label
      htmlFor={id}
      className="flex items-start gap-3 rounded-neu-sm bg-surface px-3 py-2 shadow-neu-raised-sm aria-disabled:opacity-70"
      aria-disabled={!selectable}
    >
      <input
        id={id}
        type="checkbox"
        className="mt-1 size-4 accent-primary"
        checked={checked}
        aria-disabled={!selectable}
        disabled={disabled || !selectable}
        onChange={(event) => onToggle(event.target.checked)}
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{name}</span>
          {(option.labels ?? []).map((label) => (
            <span
              key={label}
              className="inline-flex items-center rounded-full bg-surface px-2 py-0.5 text-[11px] font-semibold text-muted-foreground shadow-neu-raised-sm"
            >
              {labelText(label)}
            </span>
          ))}
          {quotaText ? (
            <span className="inline-flex items-center rounded-full bg-surface px-2 py-0.5 text-[11px] font-semibold text-muted-foreground shadow-neu-raised-sm">
              {quotaText}
            </span>
          ) : null}
        </span>
        {!selectable && blockedReason ? (
          <span
            role="status"
            className="mt-1 block text-xs font-semibold text-muted-foreground"
          >
            {blockedReason}
          </span>
        ) : null}
      </span>
    </label>
  );
}

/** The effective choice as operator display text, for the member read-only view. */
function effectiveOptionLabel(capability: CapabilityOptions): string {
  const id =
    capability.effective_option_id ?? capability.operator_default_option_id;
  const option = capability.options.find((entry) => entry.option_id === id);
  return option?.display_name?.trim() || "Operator default";
}
