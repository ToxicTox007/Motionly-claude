"use client";

import React, { useCallback, useMemo, useRef, useState } from "react";
import { Download, RotateCcw, Share2, Film } from "lucide-react";
import type { ExportSummary } from "@motionly/shared";
import { apiFetch, editorApiRequest } from "@/lib/client-api";
import { copyText } from "@/lib/clipboard";
import { env } from "@/lib/env";
import { CopyField } from "../copy-field";
import { useToast } from "../toast";
import { Label, Select } from "./ui";
import {
  SHARE_LINK_EXPIRY_OPTIONS,
  buildWatchUrl,
  shareStrategy,
} from "./export-sharing.mjs";

interface ExportShareLink {
  id: string;
  export_id: string;
  allow_download: boolean;
  expires_at: string;
  revoked_at?: string | null;
  last_accessed_at?: string | null;
  created_at: string;
}

interface ExportsPanelProps {
  projectId: string;
  workspaceId: string;
  exports: ExportSummary[];
  /** Optional: lift restored/updated exports to the parent. */
  onExportsChange?: (exports: ExportSummary[]) => void;
}

/**
 * Lists every finished export version with an accessible, seekable player and
 * per-version Download, Restore, and Share actions. Restoring re-publishes the
 * plan version that produced the export through the standard revision checks.
 */
export function ExportsPanel({
  projectId,
  workspaceId,
  exports: initialExports,
  onExportsChange,
}: ExportsPanelProps) {
  const { toast } = useToast();
  // Restored versions are tracked as an overlay and merged with the props so we
  // never synchronise full list state from props inside an effect.
  const [restored, setRestored] = useState<ExportSummary[]>([]);
  const exports = useMemo(() => {
    const byId = new Map<string, ExportSummary>();
    for (const item of initialExports) byId.set(item.id, item);
    for (const item of restored) byId.set(item.id, item);
    return [...byId.values()];
  }, [initialExports, restored]);
  const sorted = useMemo(
    () => [...exports].sort((a, b) => b.version - a.version),
    [exports],
  );
  const [selectedId, setSelectedId] = useState<string | null>(
    sorted[0]?.id ?? null,
  );
  const [playerUrl, setPlayerUrl] = useState<string | null>(null);
  const [playerExportId, setPlayerExportId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const selected = sorted.find((item) => item.id === selectedId) ?? sorted[0];

  const createDownloadLink = useCallback(
    async (exportId: string): Promise<{ url: string; filename?: string }> => {
      const response = await apiFetch(
        `${env.apiBaseUrl}/exports/${exportId}/download-url`,
        {},
        { workspaceId },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          payload.error?.message ?? "Download link could not be created.",
        );
      }
      return {
        url: payload.signed_download_url as string,
        filename: payload.download_filename as string | undefined,
      };
    },
    [workspaceId],
  );

  async function playExport(exportId: string) {
    setBusy(true);
    setStatus("Preparing player…");
    try {
      const { url } = await createDownloadLink(exportId);
      setPlayerUrl(url);
      setPlayerExportId(exportId);
      setStatus(null);
      // Move focus to the player so keyboard users land on the control.
      window.setTimeout(() => videoRef.current?.focus(), 0);
    } catch (error) {
      setStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function downloadExport(exportId: string) {
    setBusy(true);
    setStatus("Creating secure download link…");
    try {
      const { url } = await createDownloadLink(exportId);
      window.location.href = url;
      setStatus("Download started. The link expires shortly.");
    } catch (error) {
      setStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function restoreExport(exportId: string) {
    setBusy(true);
    setStatus("Restoring export version…");
    try {
      const payload = await editorApiRequest<{ export: ExportSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/exports/${exportId}/restore`,
        {
          method: "POST",
          workspaceId,
          friendlyError: "This export version could not be restored.",
          body: {},
        },
      );
      const next = [
        payload.export,
        ...exports.filter((item) => item.id !== payload.export.id),
      ];
      setRestored((current) => [
        payload.export,
        ...current.filter((item) => item.id !== payload.export.id),
      ]);
      onExportsChange?.(next);
      setSelectedId(payload.export.id);
      setStatus(`Restored as v${payload.export.version}.`);
      toast({
        title: "Export Restored",
        description: `The export was restored as v${payload.export.version}.`,
        variant: "success",
      });
    } catch (error) {
      setStatus((error as Error).message);
      toast({
        title: "Restore Failed",
        description:
          "The plan changed since this export. Refresh and try again.",
        variant: "error",
      });
    } finally {
      setBusy(false);
    }
  }

  if (sorted.length === 0) {
    return (
      <div className="rounded-neu-lg bg-surface p-6 text-center text-sm font-semibold text-muted-foreground shadow-neu-inset">
        No exports yet. Render your edit to see finished versions here.
      </div>
    );
  }

  return (
    <section
      aria-labelledby="exports-panel-heading"
      className="space-y-4"
      id="editor-exports"
    >
      <div className="space-y-1 border-b border-hairline pb-3">
        <h2
          id="exports-panel-heading"
          className="text-xl font-bold tracking-tight text-foreground"
        >
          Exports
        </h2>
        <p className="text-xs font-medium text-muted-foreground">
          Play, download, share, or restore any finished version.
        </p>
      </div>

      {selected ? (
        <div className="space-y-2">
          <Label>Preview</Label>
          {playerUrl && playerExportId === selected.id ? (
            <video
              ref={videoRef}
              key={playerUrl}
              src={playerUrl}
              controls
              playsInline
              tabIndex={0}
              aria-label={`Export version ${selected.version} player`}
              className="w-full rounded-neu-lg bg-ink shadow-neu-inset"
            />
          ) : (
            <button
              type="button"
              onClick={() => playExport(selected.id)}
              disabled={busy}
              aria-label={`Play export version ${selected.version}`}
              className="flex w-full items-center justify-center gap-2 rounded-neu-lg bg-ink px-4 py-10 text-sm font-semibold text-white shadow-neu-inset transition-shadow duration-200 hover:shadow-neu-glow disabled:opacity-50"
            >
              <Film className="size-5" aria-hidden="true" />
              Load player for v{selected.version}
            </button>
          )}
        </div>
      ) : null}

      <ul className="space-y-2" aria-label="Export versions">
        {sorted.map((item) => (
          <li
            key={item.id}
            className={`rounded-neu-md p-3 transition-shadow duration-200 ${
              item.id === selected?.id
                ? "bg-surface shadow-neu-glow"
                : "bg-surface shadow-neu-raised-sm"
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => setSelectedId(item.id)}
                aria-pressed={item.id === selected?.id}
                className="text-left"
              >
                <span className="block text-xs font-semibold tracking-wide text-muted-foreground tabular-nums">
                  v{item.version}
                  {item.restored_from_export_id ? " · restored" : ""}
                </span>
                <span className="block text-xs font-medium text-muted-foreground tabular-nums">
                  {item.width}x{item.height} · {item.format.toUpperCase()} ·{" "}
                  {item.fps}fps
                </span>
              </button>
              <div className="flex gap-1.5">
                <IconAction
                  label={`Play export version ${item.version}`}
                  onClick={() => playExport(item.id)}
                  disabled={busy}
                >
                  <Film className="size-3.5" aria-hidden="true" />
                </IconAction>
                <IconAction
                  label={`Download export version ${item.version}`}
                  onClick={() => downloadExport(item.id)}
                  disabled={busy}
                >
                  <Download className="size-3.5" aria-hidden="true" />
                </IconAction>
                <IconAction
                  label={`Restore export version ${item.version}`}
                  onClick={() => restoreExport(item.id)}
                  disabled={busy}
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                </IconAction>
              </div>
            </div>
            <ShareControls
              exportId={item.id}
              version={item.version}
              workspaceId={workspaceId}
              onStatus={setStatus}
            />
          </li>
        ))}
      </ul>

      {status ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-neu bg-surface p-3 text-xs font-semibold text-muted-foreground shadow-neu-inset-sm"
        >
          {status}
        </p>
      ) : null}
    </section>
  );
}

function IconAction({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex size-9 items-center justify-center rounded-full bg-surface text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

function ShareControls({
  exportId,
  version,
  workspaceId,
  onStatus,
}: {
  exportId: string;
  version: number;
  workspaceId: string;
  onStatus: (message: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [allowDownload, setAllowDownload] = useState(true);
  const [links, setLinks] = useState<ExportShareLink[]>([]);
  const [busy, setBusy] = useState(false);
  const [uncopiedUrl, setUncopiedUrl] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const response = await apiFetch(
      `${env.apiBaseUrl}/exports/${exportId}/share-links`,
      {},
      { workspaceId },
    );
    const payload = await response.json();
    if (response.ok) setLinks(payload.links ?? []);
  }, [exportId, workspaceId]);

  function toggleOpen() {
    setOpen((value) => {
      const next = !value;
      // Load existing links when the panel opens (external data sync).
      if (next) void refresh();
      return next;
    });
  }

  async function createLink() {
    setBusy(true);
    setUncopiedUrl(null);
    onStatus("Creating share link…");
    try {
      const response = await apiFetch(
        `${env.apiBaseUrl}/exports/${exportId}/share-links`,
        {
          method: "POST",
          body: JSON.stringify({
            expires_in_days: expiresInDays,
            allow_download: allowDownload,
          }),
          headers: { "content-type": "application/json" },
        },
        { workspaceId },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          payload.error?.message ?? "Share link could not be created.",
        );
      }
      // The token is returned exactly once; build the fragment URL and share it
      // now. We never store the token.
      const watchUrl = buildWatchUrl(env.appUrl, payload.token as string);
      if (!(await shareWatchUrl(watchUrl, version, onStatus))) {
        setUncopiedUrl(watchUrl);
      }
      await refresh();
    } catch (error) {
      onStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revokeLink(linkId: string) {
    setBusy(true);
    onStatus("Revoking share link…");
    try {
      const response = await apiFetch(
        `${env.apiBaseUrl}/exports/${exportId}/share-links/${linkId}/revoke`,
        { method: "POST" },
        { workspaceId },
      );
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(
          payload.error?.message ?? "Share link could not be revoked.",
        );
      }
      onStatus("Share link revoked. The link no longer works.");
      await refresh();
    } catch (error) {
      onStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 border-t border-hairline pt-2">
      <button
        type="button"
        onClick={toggleOpen}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-foreground"
      >
        <Share2 className="size-3.5" aria-hidden="true" />
        Share v{version}
      </button>
      {open ? (
        <div className="mt-2 space-y-2">
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label className="mb-0">Link expires</Label>
              <Select
                id={`share-expiry-${exportId}`}
                label="Link expires"
                value={String(expiresInDays)}
                onChange={(value) => setExpiresInDays(Number(value))}
                options={SHARE_LINK_EXPIRY_OPTIONS.map((option) => ({
                  value: String(option.value),
                  label: option.label,
                }))}
              />
            </div>
            <label className="flex items-center gap-2 text-xs font-semibold text-foreground">
              <input
                type="checkbox"
                aria-label="Allow viewers to download"
                checked={allowDownload}
                onChange={(event) => setAllowDownload(event.target.checked)}
                className="size-4 accent-primary"
              />
              Allow download
            </label>
          </div>
          <button
            type="button"
            onClick={createLink}
            disabled={busy}
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-neu bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-shadow duration-200 active:shadow-neu-pressed disabled:opacity-50"
          >
            Create & share link
          </button>
          {uncopiedUrl ? (
            <CopyField value={uncopiedUrl} label="Share link" />
          ) : null}
          {links.length ? (
            <ul
              className="space-y-1"
              aria-label={`Share links for v${version}`}
            >
              {links.map((link) => (
                <li
                  key={link.id}
                  className="flex items-center justify-between gap-2 rounded-neu bg-surface px-2 py-1 text-[11px] font-medium text-muted-foreground shadow-neu-inset-sm"
                >
                  <span>
                    {link.revoked_at
                      ? "Revoked"
                      : link.allow_download
                        ? "Watch + download"
                        : "Watch only"}
                    {" · expires "}
                    {new Date(link.expires_at).toLocaleDateString()}
                  </span>
                  {!link.revoked_at ? (
                    <button
                      type="button"
                      onClick={() => revokeLink(link.id)}
                      disabled={busy}
                      aria-label={`Revoke share link created ${new Date(
                        link.created_at,
                      ).toLocaleString()}`}
                      className="font-semibold text-danger"
                    >
                      Revoke
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Shares or copies the watch link; false when neither worked, so the caller
 * shows the link for copying by hand (never inside the status live region,
 * since the link carries the share token). */
async function shareWatchUrl(
  watchUrl: string,
  version: number,
  onStatus: (message: string | null) => void,
): Promise<boolean> {
  const nav = typeof navigator !== "undefined" ? navigator : undefined;
  if (shareStrategy(nav) === "native" && nav) {
    try {
      await nav.share({
        title: `Motionly export v${version}`,
        text: "Watch this video",
        url: watchUrl,
      });
      onStatus("Shared.");
      return true;
    } catch {
      // Fall through to copy if the user dismisses the native sheet.
    }
  }
  if ((await copyText(watchUrl)).ok) {
    onStatus("Link copied to clipboard.");
    return true;
  }
  onStatus("Share link created but not copied automatically. Copy it below.");
  return false;
}
