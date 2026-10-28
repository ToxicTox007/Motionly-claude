"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useDraggable } from "@dnd-kit/core";
import { ChevronDown, ChevronRight, GripVertical, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import {
  genericInitToHelper,
  genericRefreshToHelper,
  putBlobWithProgress,
  runResumableUpload,
  type GenericMediaSession,
  type ResumableUploadTransport,
} from "@/lib/resumable-upload";
import type { StockVideoAssetSummary } from "@motionly/shared";

type MediaUploadSession = GenericMediaSession;
import {
  CATEGORY_LABELS,
  ELEMENT_CATALOG,
  type PaletteCategory,
  type PaletteElement,
} from "./element-catalog";

const PRIMARY_CREATOR_CATEGORIES = new Set<PaletteCategory>([
  "text",
  "media",
  "audio",
  "effects",
  "ai",
]);

export function ElementPalette({
  projectId,
  workspaceId,
}: {
  projectId: string;
  workspaceId: string;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<PaletteCategory | "all">("all");
  const [open, setOpen] = useState<Record<string, boolean>>(
    Object.fromEntries(
      (Object.keys(CATEGORY_LABELS) as PaletteCategory[]).map((key) => [
        key,
        PRIMARY_CREATOR_CATEGORIES.has(key),
      ]),
    ),
  );
  const [library, setLibrary] = useState<StockVideoAssetSummary[]>([]);
  const [libraryBusy, setLibraryBusy] = useState(false);
  const [libraryMessage, setLibraryMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const normalizedQuery = query.trim().toLowerCase();
  const items = useMemo(
    () =>
      ELEMENT_CATALOG.filter(
        (entry) =>
          (filter === "all" || entry.category === filter) &&
          (!normalizedQuery ||
            `${entry.name} ${CATEGORY_LABELS[entry.category]}`
              .toLowerCase()
              .includes(normalizedQuery)),
      ),
    [filter, normalizedQuery],
  );
  useEffect(() => {
    let cancelled = false;
    void editorApiRequest<{ assets: StockVideoAssetSummary[] }>(
      `${env.apiBaseUrl}/projects/${projectId}/broll/library`,
      {
        workspaceId,
        friendlyError: "B-roll library could not be loaded.",
      },
    )
      .then((payload) => {
        if (!cancelled) setLibrary(payload.assets);
      })
      .catch((error) => {
        if (!cancelled) setLibraryMessage((error as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, workspaceId]);

  async function uploadLibraryFile(file: File) {
    setLibraryBusy(true);
    setLibraryMessage("Preparing upload…");
    const controller = new AbortController();
    try {
      const metadata = await probeVideoFile(file);
      // One resumable, server-verified path for every media type: init →
      // parts → confirm → complete (verified checksum/size) via the shared
      // helper against the B-roll resumable multipart endpoints.
      let completedAsset: StockVideoAssetSummary | null = null;
      const base = `${env.apiBaseUrl}/projects/${projectId}/broll/library/multipart`;
      const transport: ResumableUploadTransport = {
        async initUpload() {
          const initialized = await editorApiRequest<{
            asset: StockVideoAssetSummary;
            session: MediaUploadSession;
          }>(`${base}/init`, {
            method: "POST",
            workspaceId,
            friendlyError: "B-roll upload could not be started.",
            body: {
              filename: file.name,
              mime_type: file.type || "video/mp4",
              file_size_bytes: file.size,
              ...metadata,
            },
            signal: controller.signal,
          });
          return genericInitToHelper(initialized.session);
        },
        putBlob: (url, blob, headers, signal, onProgress) =>
          putBlobWithProgress(url, blob, headers, signal, onProgress),
        async refreshSession(sessionId) {
          return genericRefreshToHelper(
            await editorApiRequest<MediaUploadSession>(`${base}/${sessionId}`, {
              workspaceId,
              friendlyError: "B-roll upload could not be resumed.",
              signal: controller.signal,
            }),
          );
        },
        async confirmPart(sessionId, partNumber, etag) {
          return editorApiRequest(
            `${base}/${sessionId}/parts/${partNumber}/complete`,
            {
              method: "POST",
              workspaceId,
              friendlyError: "A B-roll upload part could not be confirmed.",
              body: etag ? { etag } : {},
              signal: controller.signal,
            },
          );
        },
        async completeUpload(sessionId) {
          const completed = await editorApiRequest<{
            asset: StockVideoAssetSummary;
          }>(`${base}/${sessionId}/complete`, {
            method: "POST",
            workspaceId,
            friendlyError: "B-roll upload could not be finalized.",
            body: {},
            signal: controller.signal,
          });
          completedAsset = completed.asset;
          return { status: "completed" } as never;
        },
      };
      await runResumableUpload({
        file,
        transport,
        signal: controller.signal,
        onProgress: (progress) =>
          setLibraryMessage(`Uploading B-roll… ${progress.percent}%`),
      });
      if (!completedAsset) throw new Error("B-roll upload did not finalize.");
      const asset: StockVideoAssetSummary = completedAsset;
      setLibrary((current) => [
        asset,
        ...current.filter((item) => item.id !== asset.id),
      ]);
      setLibraryMessage("B-roll added to your library.");
    } catch (error) {
      setLibraryMessage((error as Error).message);
    } finally {
      setLibraryBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function removeLibraryAsset(assetId: string) {
    setLibraryBusy(true);
    try {
      await editorApiRequest(
        `${env.apiBaseUrl}/projects/${projectId}/broll/library/${assetId}`,
        {
          method: "DELETE",
          workspaceId,
          friendlyError: "Library clip could not be deleted.",
        },
      );
      setLibrary((current) => current.filter((item) => item.id !== assetId));
    } catch (error) {
      setLibraryMessage((error as Error).message);
    } finally {
      setLibraryBusy(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <div className="sticky top-0 z-10 space-y-2 border-b border-hairline bg-surface p-3">
        <h2 id="media-heading" className="text-sm font-bold text-foreground">
          Elements
        </h2>
        <label className="relative block">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search elements…"
            className="min-h-10 w-full rounded-neu border border-control bg-surface pl-9 pr-3 text-sm font-medium text-foreground shadow-neu-inset-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
        </label>
        <select
          value={filter}
          onChange={(event) =>
            setFilter(event.target.value as PaletteCategory | "all")
          }
          className="min-h-10 w-full rounded-neu border border-control bg-surface px-3 text-sm font-medium text-foreground shadow-neu-inset-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          aria-label="Filter element category"
        >
          <option value="all">All categories</option>
          {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <section className="border-b border-hairline p-3">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold text-muted-foreground">
              My B-roll
            </h3>
            <button
              type="button"
              disabled={libraryBusy}
              onClick={() => fileRef.current?.click()}
              className="neu-pressable min-h-9 rounded-neu bg-surface px-3 text-xs font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised disabled:opacity-50 disabled:shadow-neu-raised-sm"
            >
              Upload
            </button>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept="video/mp4,video/quicktime,video/webm,video/x-m4v"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void uploadLibraryFile(file);
            }}
          />
          {library.length ? (
            <div className="grid grid-cols-2 gap-1.5">
              {library.map((asset) => (
                <LibraryTile
                  key={asset.id}
                  asset={asset}
                  onDelete={() => void removeLibraryAsset(asset.id)}
                />
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Upload reusable clips, then drag them to B-roll.
            </p>
          )}
          {libraryMessage ? (
            <p
              className="mt-2 text-xs text-muted-foreground"
              aria-live="polite"
            >
              {libraryMessage}
            </p>
          ) : null}
        </section>
        {(Object.keys(CATEGORY_LABELS) as PaletteCategory[]).map((category) => {
          const categoryItems = items.filter(
            (entry) => entry.category === category,
          );
          if (!categoryItems.length) return null;
          const expanded = open[category] ?? true;
          return (
            <section key={category} className="border-b border-hairline">
              <button
                type="button"
                onClick={() =>
                  setOpen((current) => ({ ...current, [category]: !expanded }))
                }
                className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-xs font-semibold text-muted-foreground"
                aria-expanded={expanded}
              >
                {expanded ? (
                  <ChevronDown className="size-3.5" strokeWidth={1.75} />
                ) : (
                  <ChevronRight className="size-3.5" strokeWidth={1.75} />
                )}
                {CATEGORY_LABELS[category]}
              </button>
              {expanded ? (
                <div className="grid grid-cols-2 gap-2 px-3 pb-3">
                  {categoryItems.map((entry) => (
                    <PaletteTile key={entry.id} entry={entry} />
                  ))}
                </div>
              ) : null}
            </section>
          );
        })}
        {!items.length ? (
          <p className="p-4 text-sm text-muted-foreground">
            No matching elements.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function LibraryTile({
  asset,
  onDelete,
}: {
  asset: StockVideoAssetSummary;
  onDelete: () => void;
}) {
  const entry: PaletteElement = {
    id: `library-${asset.id}`,
    name: asset.title,
    category: "media",
    elementType: "broll",
    accepts: ["timeline"],
    preview: "VIDEO",
    durationMs: asset.duration_ms || 3000,
    properties: {
      source: "user_library",
      assetId: asset.id,
      assetUrl: asset.download_url,
      previewUrl: asset.preview_video_url,
      license: asset.license,
    },
  };
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `palette:${entry.id}`,
    data: { kind: "palette", element: entry },
  });
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-neu-md bg-surface shadow-neu-raised-sm",
        isDragging && "opacity-40",
      )}
    >
      <button
        ref={setNodeRef}
        type="button"
        {...attributes}
        {...listeners}
        className="block w-full touch-none text-left"
      >
        <span className="block h-12 bg-ink">
          {asset.preview_video_url ? (
            <video
              src={asset.preview_video_url}
              muted
              preload="metadata"
              className="h-full w-full object-cover"
            />
          ) : null}
        </span>
        <span className="block truncate p-1.5 text-xs font-semibold text-foreground">
          {asset.title}
        </span>
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="absolute right-0 top-0 bg-ink/70 px-1 text-xs text-white"
        aria-label={`Delete ${asset.title}`}
      >
        ×
      </button>
    </div>
  );
}

function probeVideoFile(
  file: File,
): Promise<{ width: number; height: number; duration_ms: number }> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      const result = {
        width: video.videoWidth || 1080,
        height: video.videoHeight || 1920,
        duration_ms: Number.isFinite(video.duration)
          ? Math.round(video.duration * 1000)
          : 0,
      };
      URL.revokeObjectURL(url);
      resolve(result);
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      resolve({ width: 1080, height: 1920, duration_ms: 0 });
    };
    video.src = url;
  });
}

function PaletteTile({ entry }: { entry: PaletteElement }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({
      id: `palette:${entry.id}`,
      data: { kind: "palette", element: entry },
    });
  const background =
    typeof entry.properties?.background === "string"
      ? entry.properties.background
      : undefined;
  return (
    <button
      ref={setNodeRef}
      type="button"
      {...attributes}
      {...listeners}
      className={cn(
        "group relative min-h-16 touch-none overflow-hidden rounded-neu-md bg-surface p-2 text-left shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:shadow-neu-pressed",
        isDragging && "opacity-35",
      )}
      style={{
        transform: transform
          ? `translate3d(${transform.x}px,${transform.y}px,0)`
          : undefined,
      }}
      title={`Drag ${entry.name} to ${entry.accepts.join(", ")}`}
    >
      <span
        className="mb-1 flex h-8 items-center justify-center rounded-neu-sm bg-muted text-xs font-semibold text-foreground shadow-neu-inset-sm"
        style={{ background }}
      >
        {entry.preview || entry.name.slice(0, 2)}
      </span>
      <span className="block truncate text-xs font-semibold text-foreground">
        {entry.name}
      </span>
      <GripVertical className="absolute right-0.5 top-0.5 size-3 text-muted-foreground opacity-0 group-hover:opacity-100" />
    </button>
  );
}
