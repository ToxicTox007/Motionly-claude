"use client";

// Task 32 — the accessible per-capability model picker (plan Section 2.11).
//
// This module holds two pieces:
//
//   * `CapabilityOptionRadioGroup` — the reusable, presentational radio group.
//     One accessible control per capability: each option shows its display
//     name, description, labels and quota weight ("uses 2× quota"). A
//     plan-locked option is rendered disabled with "Requires <plan>" using
//     `aria-disabled` so keyboard focus is preserved (the codebase's focus
//     pattern skips only `[disabled]`), never the `disabled` attribute. State
//     is carried by text, never colour alone. It shows the option id/display
//     text only — never a model id, URL or secret.
//
//   * `ProjectModelPicker` — mounts the radio group in the project editor and
//     persists a choice through the project settings route. When the workspace
//     is locked it shows the picker read-only with the reason.
//
// The workspace settings UI reuses `CapabilityOptionRadioGroup` for the
// allow-list and default controls (see the account AI-models client).

import { useCallback, useId, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  type CapabilityOptions,
  type ModelOption,
  clearProjectModelSetting,
  labelText,
  optionDisabledReason,
  optionSelectable,
  quotaWeightText,
  saveProjectModelSetting,
} from "@/lib/model-options";

// Human copy for a capability id. Neutral, no vendor identity. Unknown ids fall
// back to a titleised form of the id.
const CAPABILITY_TITLES: Record<string, string> = {
  "planning.chat": "Edit planning",
  "transcription.speech_to_text": "Transcription",
  "vision.caption": "Visual captions",
  "vision.object_detection": "Object detection",
  "embeddings.text": "Text matching",
  "embeddings.image": "Image matching",
  "audio.diarization": "Speaker labels",
  "audio.emotion": "Emotion analysis",
  "audio.stem_separation": "Audio stems",
  "image.generation": "Image generation",
  "video.generation.submit": "Video generation",
  "matte.segmentation": "Background matte",
  "audio.sound_classification": "Sound labels",
};

export function capabilityTitle(capability: string): string {
  const known = CAPABILITY_TITLES[capability];
  if (known) return known;
  return capability
    .split(/[._]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/**
 * The reusable per-capability radio group. `value` is the currently chosen
 * option id (or null for "use the default"). `onChange` is called with the
 * chosen option id. When `readOnly` is set the group is non-interactive and the
 * `readOnlyReason` is announced beside it.
 */
export function CapabilityOptionRadioGroup({
  capability,
  value,
  onChange,
  disabled = false,
  readOnly = false,
  readOnlyReason,
  includeDefaultOption = false,
  defaultOptionLabel = "Use the workspace default",
  busy = false,
}: {
  capability: CapabilityOptions;
  value: string | null;
  onChange: (optionId: string | null) => void;
  disabled?: boolean;
  readOnly?: boolean;
  readOnlyReason?: string | null;
  /** Show a leading "use the default" radio whose value is null. */
  includeDefaultOption?: boolean;
  defaultOptionLabel?: string;
  busy?: boolean;
}) {
  const groupId = useId();
  const descriptionId = `${groupId}-desc`;
  const title = capabilityTitle(capability.capability);

  return (
    <fieldset
      className="rounded-neu-md bg-surface p-4 shadow-neu-inset-sm"
      data-model-picker={capability.capability}
      aria-describedby={readOnly && readOnlyReason ? descriptionId : undefined}
    >
      <legend className="flex items-center gap-2 text-sm font-semibold text-foreground">
        {title}
        {busy ? (
          <Loader2
            className="size-3.5 animate-spin text-muted-foreground"
            strokeWidth={1.75}
            aria-hidden="true"
          />
        ) : null}
      </legend>
      {readOnly && readOnlyReason ? (
        <p
          id={descriptionId}
          role="status"
          className="mt-1 text-xs font-medium text-muted-foreground"
        >
          {readOnlyReason}
        </p>
      ) : null}
      <div role="radiogroup" aria-label={title} className="mt-3 space-y-2">
        {includeDefaultOption ? (
          <OptionRow
            groupName={groupId}
            optionId=""
            displayName={defaultOptionLabel}
            description={null}
            labels={[]}
            quotaText={null}
            checked={value === null}
            blocked={false}
            blockedReason={null}
            disabled={disabled || readOnly}
            onSelect={() => onChange(null)}
          />
        ) : null}
        {capability.options.map((option) => {
          const blockedReason = optionDisabledReason(option);
          const selectable = optionSelectable(option);
          return (
            <OptionRow
              key={option.option_id}
              groupName={groupId}
              optionId={option.option_id}
              displayName={displayName(option)}
              description={option.description ?? null}
              labels={option.labels ?? []}
              quotaText={quotaWeightText(option.quota_weight)}
              checked={value === option.option_id}
              blocked={!selectable}
              blockedReason={blockedReason}
              disabled={disabled || readOnly}
              onSelect={() => onChange(option.option_id)}
            />
          );
        })}
      </div>
    </fieldset>
  );
}

/** Operator display name for an option, falling back to a neutral label. */
function displayName(option: ModelOption): string {
  return option.display_name?.trim() || "Standard model";
}

function OptionRow({
  groupName,
  optionId,
  displayName,
  description,
  labels,
  quotaText,
  checked,
  blocked,
  blockedReason,
  disabled,
  onSelect,
}: {
  groupName: string;
  optionId: string;
  displayName: string;
  description: string | null;
  labels: string[];
  quotaText: string | null;
  checked: boolean;
  blocked: boolean;
  blockedReason: string | null;
  disabled: boolean;
  onSelect: () => void;
}) {
  const id = `${groupName}-${optionId || "default"}`;
  const reasonId = `${id}-reason`;
  // Plan-locked / unavailable options keep focus: aria-disabled, never the
  // disabled attribute. An available but transiently blocked (saving) state
  // uses the real disabled attribute via `disabled`.
  const ariaDisabled = blocked;
  return (
    <label
      htmlFor={id}
      className="flex cursor-pointer items-start gap-3 rounded-neu-sm bg-surface px-3 py-2.5 shadow-neu-raised-sm aria-disabled:cursor-not-allowed aria-disabled:opacity-70"
      aria-disabled={ariaDisabled}
      data-option-id={optionId}
      data-option-blocked={blocked ? "true" : "false"}
    >
      <input
        id={id}
        type="radio"
        name={groupName}
        className="mt-1 size-4 shrink-0 accent-primary"
        checked={checked}
        aria-checked={checked}
        aria-disabled={ariaDisabled}
        aria-describedby={
          [description ? `${id}-desc` : null, blocked ? reasonId : null]
            .filter(Boolean)
            .join(" ") || undefined
        }
        disabled={disabled && !blocked}
        onChange={() => {
          if (blocked || disabled) return;
          onSelect();
        }}
        onClick={(event) => {
          // aria-disabled controls stay focusable but must not activate.
          if (blocked) event.preventDefault();
        }}
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">
            {displayName}
          </span>
          {labels.map((label) => (
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
        {description ? (
          <span
            id={`${id}-desc`}
            className="mt-1 block text-xs font-medium text-muted-foreground"
          >
            {description}
          </span>
        ) : null}
        {blocked && blockedReason ? (
          <span
            id={reasonId}
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

/**
 * The project-level picker mounted in the editor. It lets a member who can edit
 * the project override the workspace default per capability. When the workspace
 * is locked (`effective_source === "workspace_locked"`) the picker is shown
 * read-only with the reason. Saving never throws: a failure shows neutral copy.
 */
export function ProjectModelPicker({
  workspaceId,
  projectId,
  capability,
  onChanged,
}: {
  workspaceId: string;
  projectId: string;
  capability: CapabilityOptions;
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [value, setValue] = useState<string | null>(
    capability.effective_option_id ?? null,
  );

  const locked = capability.effective_source === "workspace_locked";

  const onChange = useCallback(
    async (optionId: string | null) => {
      setBusy(true);
      setError(null);
      const previous = value;
      setValue(optionId);
      const result =
        optionId === null
          ? await clearProjectModelSetting({
              workspaceId,
              projectId,
              capability: capability.capability,
            })
          : await saveProjectModelSetting({
              workspaceId,
              projectId,
              update: {
                capability: capability.capability,
                option_id: optionId,
              },
            });
      setBusy(false);
      if (!result.ok) {
        setValue(previous);
        setError(saveErrorText(result.error));
        return;
      }
      onChanged?.();
    },
    [value, workspaceId, projectId, capability.capability, onChanged],
  );

  return (
    <div className="space-y-2">
      <CapabilityOptionRadioGroup
        capability={capability}
        value={value}
        onChange={(optionId) => void onChange(optionId)}
        includeDefaultOption
        busy={busy}
        readOnly={locked}
        readOnlyReason={
          locked
            ? "Your workspace owner locked this choice. It cannot be changed per project."
            : null
        }
      />
      {error ? (
        <p role="alert" className="text-xs font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Neutral copy for a save error code. Never shows a model id or a URL. */
export function saveErrorText(code: string): string {
  switch (code) {
    case "model_choice_locked":
      return "Your workspace owner locked this choice. It cannot be changed per project.";
    case "model_choice_option_invalid":
      return "That option is not available for this workspace. Choose another.";
    case "workspace_role_forbidden":
      return "Only the workspace owner or an admin can change this.";
    default:
      return "That choice could not be saved. Please try again.";
  }
}
