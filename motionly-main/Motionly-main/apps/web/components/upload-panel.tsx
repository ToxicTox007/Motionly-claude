"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RotateCcw,
  UploadCloud,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type UploadPanelProps = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  hasExistingSource?: boolean;
};

type UploadPhase =
  | "idle"
  | "preflight"
  | "uploading"
  | "processing"
  | "complete"
  | "failed"
  | "canceled";

type Preflight = {
  filename: string;
  extension: string;
  mimeType: string;
  declaredMimeType: string;
  sizeBytes: number;
  blockingWarnings: string[];
  warnings: string[];
};

type BrowserMediaProbe = {
  status: "idle" | "loading" | "ready" | "error";
  durationMs: number | null;
  width: number | null;
  height: number | null;
  warnings: string[];
};

const MAX_UPLOAD_SIZE_BYTES = 536_870_912;
const EXTENSIONS_BY_MIME: Record<string, string[]> = {
  "video/mp4": [".mp4", ".m4v"],
  "video/quicktime": [".mov", ".qt"],
  "video/webm": [".webm"],
};
const MIME_BY_EXTENSION = Object.entries(EXTENSIONS_BY_MIME).reduce<
  Record<string, string>
>((accumulator, [mimeType, extensions]) => {
  for (const extension of extensions) accumulator[extension] = mimeType;
  return accumulator;
}, {});
const ACCEPTED_MIME_TYPES = new Set(Object.keys(EXTENSIONS_BY_MIME));
const ACCEPTED_EXTENSIONS = new Set(Object.values(EXTENSIONS_BY_MIME).flat());
const SERVER_WARNING_MESSAGES: Record<string, string> = {
  landscape_source: "Landscape source will be cropped into vertical export.",
  square_source: "Square source may need cropping for 9:16 export.",
  unknown_aspect_ratio: "Aspect ratio could not be classified.",
  long_video: "Long source may take longer to process and render.",
  very_short_video: "Very short source may not produce enough captioned content.",
  low_resolution: "Low-resolution source may look soft after export.",
  unusual_frame_rate: "Unusual frame rate will be normalized during export.",
  large_file: "Large upload may take longer on slower connections.",
};

const idleProbe: BrowserMediaProbe = {
  status: "idle",
  durationMs: null,
  width: null,
  height: null,
  warnings: [],
};

export function UploadPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  hasExistingSource = false,
}: UploadPanelProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mediaProbe, setMediaProbe] = useState<BrowserMediaProbe>(idleProbe);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [videoId, setVideoId] = useState<string | null>(null);

  const preflight = useMemo<Preflight | null>(() => {
    if (!file) return null;
    const extension = getFileExtension(file.name);
    const mimeType = resolveUploadMimeType(file);
    const blockingWarnings: string[] = [];

    if (!extension) {
      blockingWarnings.push(
        "Add a supported video extension before uploading.",
      );
    } else if (!ACCEPTED_EXTENSIONS.has(extension)) {
      blockingWarnings.push("Choose an MP4, MOV, or WebM video file.");
    }

    if (!ACCEPTED_MIME_TYPES.has(mimeType)) {
      blockingWarnings.push(
        "The browser did not detect a supported video type.",
      );
    } else if (!extensionMatchesMime(extension, mimeType)) {
      blockingWarnings.push(
        "The file extension does not match the detected video type.",
      );
    }

    if (file.size === 0) {
      blockingWarnings.push("The selected file is empty.");
    }
    if (file.size > MAX_UPLOAD_SIZE_BYTES) {
      blockingWarnings.push(
        `This file is larger than the ${formatBytes(MAX_UPLOAD_SIZE_BYTES)} starter limit.`,
      );
    }

    return {
      filename: file.name,
      extension,
      mimeType,
      declaredMimeType: file.type || "Not reported by browser",
      sizeBytes: file.size,
      blockingWarnings,
      warnings: [...blockingWarnings, ...mediaProbe.warnings],
    };
  }, [file, mediaProbe.warnings]);

  const hasBlockingPreflightIssue = Boolean(preflight?.blockingWarnings.length);
  const isCheckingMedia = mediaProbe.status === "loading";
  const isWorking = phase === "uploading" || phase === "processing";
  const uploadButtonLabel = hasExistingSource ? "Upload replacement" : "Upload";

  useEffect(() => {
    if (!file) {
      setMediaProbe(idleProbe);
      return;
    }

    const extension = getFileExtension(file.name);
    const mimeType = resolveUploadMimeType(file);
    if (
      !ACCEPTED_MIME_TYPES.has(mimeType) ||
      !extensionMatchesMime(extension, mimeType) ||
      file.size === 0 ||
      file.size > MAX_UPLOAD_SIZE_BYTES
    ) {
      setMediaProbe(idleProbe);
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    const video = document.createElement("video");
    let settled = false;
    setMediaProbe({
      status: "loading",
      durationMs: null,
      width: null,
      height: null,
      warnings: [],
    });

    video.preload = "metadata";
    video.muted = true;
    video.onloadedmetadata = () => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(objectUrl);
      const durationMs = Number.isFinite(video.duration)
        ? Math.round(video.duration * 1000)
        : null;
      const width = video.videoWidth || null;
      const height = video.videoHeight || null;
      setMediaProbe({
        status: "ready",
        durationMs,
        width,
        height,
        warnings: mediaProbeWarnings({ durationMs, width, height }),
      });
    };
    video.onerror = () => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(objectUrl);
      setMediaProbe({
        status: "error",
        durationMs: null,
        width: null,
        height: null,
        warnings: [
          "Browser metadata could not be read. Server validation will verify codec, audio, and decode after upload.",
        ],
      });
    };
    video.src = objectUrl;

    return () => {
      settled = true;
      URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  function selectFile(nextFile: File | null) {
    if (phase === "uploading") abortRef.current?.abort();
    setFile(nextFile);
    setPhase(nextFile ? "preflight" : "idle");
    setProgress(0);
    setMessage(null);
    setVideoId(null);
    if (!nextFile && inputRef.current) inputRef.current.value = "";
  }

  async function startUpload() {
    if (!file || !preflight) return;
    if (hasBlockingPreflightIssue || isCheckingMedia) {
      setPhase("preflight");
      setMessage(
        isCheckingMedia
          ? "Metadata checks are still running."
          : "Choose a supported, non-empty video within the upload limit.",
      );
      return;
    }

    setPhase("uploading");
    setProgress(4);
    setMessage("Registering upload...");
    const controller = new AbortController();
    abortRef.current = controller;
    let registeredVideoId: string | null = null;
    let requestHeaders: Record<string, string> | null = null;

    try {
      const headers = await buildApiHeaders({
        workspaceId,
        userId,
        accessToken: apiAccessToken,
      });
      requestHeaders = headers;
      const initResponse = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/uploads/init`,
        {
          method: "POST",
          headers: { ...headers, "content-type": "application/json" },
          body: JSON.stringify({
            filename: preflight.filename,
            mime_type: preflight.mimeType,
            file_size_bytes: preflight.sizeBytes,
          }),
          signal: controller.signal,
        },
      );
      const initPayload = await readJson(initResponse);
      if (!initResponse.ok)
        throw new Error(
          initPayload.error?.message ?? "Upload could not be initialized.",
        );

      registeredVideoId = initPayload.video_id;
      setVideoId(initPayload.video_id);
      setProgress(12);
      setMessage("Uploading source video...");

      await uploadToSignedUrl(
        initPayload.signed_upload_url,
        file,
        initPayload.upload_headers,
        controller.signal,
        (loaded, total) => {
          if (total > 0)
            setProgress(Math.min(78, Math.round(12 + (loaded / total) * 66)));
        },
      );
      setProgress(80);
      setMessage("Confirming upload...");

      const completeResponse = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/uploads/${initPayload.video_id}/complete`,
        {
          method: "POST",
          headers: { ...headers, "content-type": "application/json" },
          body: JSON.stringify({ observed_file_size_bytes: file.size }),
          signal: controller.signal,
        },
      );
      const completePayload = await readJson(completeResponse);
      if (!completeResponse.ok)
        throw new Error(
          completePayload.error?.message ?? "Upload could not be completed.",
        );

      setPhase("processing");
      setProgress(86);
      setMessage("Running server media validation...");
      let readyUpload = null;
      if (completePayload.status !== "ready") {
        readyUpload = await waitForUploadReady(
          projectId,
          initPayload.video_id,
          headers,
          controller.signal,
          (status) => {
            setProgress(status === "ready" ? 100 : 90);
            setMessage(
              status === "ready"
                ? "Server validation passed."
                : statusMessage(status),
            );
          },
        );
      } else {
        readyUpload = await fetchUpload(
          projectId,
          initPayload.video_id,
          headers,
          controller.signal,
        );
      }
      setProgress(100);
      setPhase("complete");
      setMessage(serverValidationMessage(readyUpload?.metadata?.warnings));
      router.refresh();
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        if (registeredVideoId && requestHeaders) {
          await cancelUploadRegistration(
            projectId,
            registeredVideoId,
            requestHeaders,
          ).catch(() => undefined);
        }
        setPhase("canceled");
        setProgress(0);
        setVideoId(null);
        setMessage(
          "Upload canceled. The incomplete source was not attached to this project.",
        );
        return;
      }
      setPhase("failed");
      setMessage(friendlyUploadError((error as Error).message));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }

  function cancelUpload() {
    setMessage("Canceling upload...");
    abortRef.current?.abort();
  }

  return (
    <section className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
      <div
        className={cn(
          "flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface p-6 text-center",
          "focus-within:ring-2 focus-within:ring-primary",
          isWorking && "opacity-80",
        )}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          selectFile(event.dataTransfer.files.item(0));
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/webm"
          className="sr-only"
          onChange={(event) => selectFile(event.target.files?.item(0) ?? null)}
        />
        <UploadCloud className="size-11 text-primary" aria-hidden="true" />
        <h2 className="mt-4 text-xl font-bold">
          {hasExistingSource ? "Replace source video" : "Upload source video"}
        </h2>
        <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
          {hasExistingSource
            ? "Choose a new MP4, MOV, or WebM file. It will become the active source after preprocessing finishes."
            : "Drag in an MP4, MOV, or WebM file. Preprocessing verifies duration, resolution, audio, and thumbnail."}
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-3">
          <Button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={isWorking}
          >
            {hasExistingSource ? "Choose replacement" : "Choose video"}
          </Button>
          {file ? (
            <Button
              type="button"
              variant="secondary"
              onClick={() => selectFile(null)}
              disabled={isWorking}
            >
              <X className="mr-2 size-4" aria-hidden="true" />
              Clear
            </Button>
          ) : null}
        </div>
      </div>

      <aside className="rounded-lg border border-border bg-surface p-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-bold uppercase text-muted-foreground">
            New File Preflight
          </h3>
          <StatusBadge
            phase={phase}
            hasFile={Boolean(file)}
            hasBlockingIssue={hasBlockingPreflightIssue}
            hasWarnings={Boolean(preflight?.warnings.length)}
            isCheckingMedia={isCheckingMedia}
          />
        </div>
        {preflight ? (
          <div className="mt-5 space-y-4">
            <MetadataRow label="Filename" value={preflight.filename} />
            <div className="grid gap-3 sm:grid-cols-2">
              <MetadataRow
                label="Size"
                value={formatBytes(preflight.sizeBytes)}
              />
              <MetadataRow label="Type" value={preflight.mimeType} />
              <MetadataRow
                label="Duration"
                value={formatDuration(mediaProbe.durationMs)}
              />
              <MetadataRow
                label="Resolution"
                value={formatResolution(mediaProbe.width, mediaProbe.height)}
              />
              <MetadataRow
                label="Aspect ratio"
                value={formatAspectRatio(mediaProbe.width, mediaProbe.height)}
              />
              <MetadataRow
                label="Browser type"
                value={preflight.declaredMimeType}
              />
              <MetadataRow
                label="Frame rate"
                value={phase === "complete" ? "Server checked" : "After upload"}
              />
              <MetadataRow
                label="Audio"
                value={phase === "complete" ? "Server checked" : "After upload"}
              />
              <MetadataRow
                label="Codec"
                value={phase === "complete" ? "Server checked" : "After upload"}
              />
              <MetadataRow
                label="Decode"
                value={phase === "complete" ? "Server checked" : "After upload"}
              />
              <MetadataRow
                label="Render readiness"
                value={phase === "complete" ? "Passed" : "After upload"}
              />
            </div>
            {isCheckingMedia ? (
              <div className="flex items-center gap-2 rounded-lg border border-border bg-muted p-3 text-sm font-semibold text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Checking browser metadata...
              </div>
            ) : null}
            {preflight.warnings.length ? (
              <div
                className={cn(
                  "rounded-lg border p-3 text-sm",
                  preflight.blockingWarnings.length
                    ? "border-red-200 bg-red-50 text-red-800"
                    : "border-amber-200 bg-amber-50 text-amber-900",
                )}
              >
                <div className="flex items-center gap-2 font-semibold">
                  <AlertTriangle className="size-4" aria-hidden="true" />
                  {preflight.blockingWarnings.length
                    ? "Fix before upload"
                    : "Preflight notes"}
                </div>
                <ul className="mt-2 list-inside list-disc space-y-1">
                  {preflight.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : !isCheckingMedia ? (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm font-semibold text-emerald-900">
                <CheckCircle2 className="size-4" aria-hidden="true" />
                Browser preflight passed. Upload will run server validation for
                codec, audio, frame rate, decode, and render readiness.
              </div>
            ) : null}
            <div>
              <div
                className="h-2 rounded-full bg-muted"
                aria-label="Upload progress"
              >
                <div
                  className="h-2 rounded-full bg-primary transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              {progress > 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {progress}%
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-3">
              <Button
                type="button"
                onClick={startUpload}
                disabled={
                  hasBlockingPreflightIssue || isCheckingMedia || isWorking
                }
              >
                {isWorking ? (
                  <Loader2
                    className="mr-2 size-4 animate-spin"
                    aria-hidden="true"
                  />
                ) : null}
                {phase === "uploading"
                  ? "Uploading"
                  : phase === "processing"
                    ? "Processing"
                    : uploadButtonLabel}
              </Button>
              {phase === "uploading" ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={cancelUpload}
                >
                  Cancel
                </Button>
              ) : null}
              {phase === "failed" || phase === "canceled" ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={startUpload}
                  disabled={hasBlockingPreflightIssue || isCheckingMedia}
                >
                  <RotateCcw className="mr-2 size-4" aria-hidden="true" />
                  Retry
                </Button>
              ) : null}
            </div>
            {videoId ? (
              <p className="text-xs text-muted-foreground">
                Registered upload {videoId.slice(0, 8)}.
              </p>
            ) : null}
            {message ? (
              <p
                className={cn(
                  "rounded-lg border p-3 text-sm",
                  phase === "failed"
                    ? "border-red-200 bg-red-50 text-red-800"
                    : phase === "complete"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                      : "border-border bg-muted text-muted-foreground",
                )}
              >
                {message}
              </p>
            ) : null}
          </div>
        ) : (
          <p className="mt-5 text-sm leading-6 text-muted-foreground">
            No file selected. Choose a source video to run upload preflight.
          </p>
        )}
      </aside>
    </section>
  );
}

function StatusBadge({
  phase,
  hasFile,
  hasBlockingIssue,
  hasWarnings,
  isCheckingMedia,
}: {
  phase: UploadPhase;
  hasFile: boolean;
  hasBlockingIssue: boolean;
  hasWarnings: boolean;
  isCheckingMedia: boolean;
}) {
  const label =
    phase === "preflight" && isCheckingMedia
      ? "validating"
      : phase === "preflight" && hasBlockingIssue
        ? "blocked"
        : phase === "preflight" && hasWarnings
          ? "warnings"
          : phase === "preflight" && hasFile
            ? "ready"
            : phase;
  const tone =
    label === "blocked" || phase === "failed"
      ? "bg-red-50 text-red-700"
      : label === "ready" || phase === "complete"
        ? "bg-emerald-50 text-emerald-700"
        : label === "warnings"
          ? "bg-amber-50 text-amber-700"
          : phase === "uploading" || phase === "processing" || isCheckingMedia
            ? "bg-primary/10 text-primary"
            : "bg-muted text-muted-foreground";
  return (
    <span
      className={cn(
        "rounded-full px-2.5 py-1 text-xs font-semibold capitalize",
        tone,
      )}
    >
      {label}
    </span>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 break-words text-sm font-semibold">{value}</p>
    </div>
  );
}

async function readJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function uploadToSignedUrl(
  url: string,
  file: File,
  headers: Record<string, string>,
  signal: AbortSignal,
  onProgress: (loaded: number, total: number) => void,
) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    for (const [key, value] of Object.entries(headers))
      xhr.setRequestHeader(key, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
        return;
      }
      reject(
        new Error(storageUploadErrorMessage(xhr.status, xhr.responseText)),
      );
    };
    xhr.onerror = () =>
      reject(
        new Error(
          "Storage upload failed. Check your connection and try again.",
        ),
      );
    xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(file);
  });
}

async function cancelUploadRegistration(
  projectId: string,
  videoId: string,
  headers: Record<string, string>,
) {
  await fetch(
    `${env.apiBaseUrl}/projects/${projectId}/uploads/${videoId}/cancel`,
    {
      method: "POST",
      headers,
    },
  );
}

async function waitForUploadReady(
  projectId: string,
  videoId: string,
  headers: Record<string, string>,
  signal: AbortSignal,
  onStatus: (status: string) => void,
) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await sleep(2500, signal);
    const uploadResponse = await fetchUpload(
      projectId,
      videoId,
      headers,
      signal,
    );
    const status = String(uploadResponse.status);
    onStatus(status);
    if (status === "ready") return uploadResponse;
    if (status === "failed") {
      throw new Error(
        uploadResponse.error_message ?? "Video preprocessing failed.",
      );
    }
  }
  throw new Error(
    "Video preprocessing is still running. Refresh the page in a moment.",
  );
}

async function fetchUpload(
  projectId: string,
  videoId: string,
  headers: Record<string, string>,
  signal: AbortSignal,
) {
  const response = await fetch(
    `${env.apiBaseUrl}/projects/${projectId}/uploads/${videoId}`,
    {
      headers,
      signal,
    },
  );
  const payload = await readJson(response);
  if (!response.ok)
    throw new Error(
      payload.error?.message ?? "Upload status could not be refreshed.",
    );
  return payload;
}

function sleep(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

function resolveUploadMimeType(file: File) {
  if (file.type && ACCEPTED_MIME_TYPES.has(file.type)) return file.type;
  const extension = getFileExtension(file.name);
  if (!file.type && extension)
    return MIME_BY_EXTENSION[extension] ?? "application/octet-stream";
  return file.type || "application/octet-stream";
}

function getFileExtension(filename: string) {
  const index = filename.lastIndexOf(".");
  return index >= 0 ? filename.slice(index).toLowerCase() : "";
}

function extensionMatchesMime(extension: string, mimeType: string) {
  return Boolean(
    extension && EXTENSIONS_BY_MIME[mimeType]?.includes(extension),
  );
}

function formatBytes(bytes: number) {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function mediaProbeWarnings({
  durationMs,
  width,
  height,
}: {
  durationMs: number | null;
  width: number | null;
  height: number | null;
}) {
  const warnings: string[] = [];
  if (durationMs === null) {
    warnings.push("Duration could not be read before upload.");
  } else if (durationMs < 3000) {
    warnings.push(
      "Video is very short; reels usually need at least a few seconds.",
    );
  } else if (durationMs > 180000) {
    warnings.push("Long videos may take longer to process and render.");
  }

  if (!width || !height) {
    warnings.push("Resolution could not be read before upload.");
  } else if (width > height) {
    warnings.push(
      "Landscape video will be cropped into a vertical 9:16 export.",
    );
  } else {
    const aspect = width / height;
    const targetAspect = 9 / 16;
    if (Math.abs(aspect - targetAspect) > 0.12) {
      warnings.push(
        "Non-9:16 video may need cropping for short-form platforms.",
      );
    }
  }

  return warnings;
}

function formatResolution(width: number | null, height: number | null) {
  return width && height ? `${width} x ${height}` : "Pending";
}

function formatAspectRatio(width: number | null, height: number | null) {
  if (!width || !height) return "Pending";
  const divisor = gcd(width, height);
  return `${Math.round(width / divisor)}:${Math.round(height / divisor)}`;
}

function formatDuration(durationMs: number | null) {
  if (!durationMs) return "Pending";
  const seconds = Math.round(durationMs / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function gcd(left: number, right: number): number {
  let a = Math.abs(left);
  let b = Math.abs(right);
  while (b) {
    const next = a % b;
    a = b;
    b = next;
  }
  return a || 1;
}

function statusMessage(status: string) {
  if (status === "processing") return "Extracting metadata and thumbnail...";
  if (status === "uploaded")
    return "Upload confirmed. Waiting for preprocessing...";
  return `Processing: ${status}`;
}

function storageUploadErrorMessage(status: number, responseText: string) {
  const details = responseText ? ` ${responseText.slice(0, 160)}` : "";
  return `Storage rejected the upload (${status}).${details ? ` ${details}` : ""}`;
}

function friendlyUploadError(message: string) {
  if (/file exceeds|too large/i.test(message))
    return "This file exceeds the upload limit for the current plan.";
  if (/mime|unsupported|extension/i.test(message))
    return "Choose an MP4, MOV, or WebM file with a matching extension.";
  if (/audio track|audio availability/i.test(message))
    return "Server validation failed: no audio track was detected. Upload a video with audio.";
  if (/duration|resolution|frame rate|codec|render ready/i.test(message))
    return `Server validation failed: ${message}`;
  if (/probe|inspect|decode|thumbnail/i.test(message))
    return "Server validation failed: the video could not be decoded reliably. Try re-exporting as H.264 MP4.";
  if (/size did not match/i.test(message))
    return "The uploaded object size changed during upload. Try again.";
  if (/storage/i.test(message)) return message;
  return message || "Upload failed. Try again with a supported video file.";
}

function serverValidationMessage(warnings: unknown) {
  const warningCodes = Array.isArray(warnings)
    ? warnings.filter((warning): warning is string => typeof warning === "string")
    : [];
  if (!warningCodes.length) {
    return "Server validation passed. Source video, metadata, thumbnail, and render readiness are confirmed.";
  }
  const warningText = warningCodes
    .map((warning) => SERVER_WARNING_MESSAGES[warning] ?? warning)
    .join(" ");
  return `Server validation passed with warnings: ${warningText}`;
}
