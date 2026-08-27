"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RotateCcw,
  UploadCloud,
  X,
  XCircle,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env, resolveProxiedUrl } from "@/lib/env";
import { shouldToast } from "@/lib/notification-taxonomy";
import {
  SignedUploadError,
  authoritativeProgress,
  fileFingerprint,
  parseUploadSession,
  partByteRange,
  putBlobWithProgress,
  sameFile,
  serializeUploadSession,
  uploadSessionStorageKey,
  type MultipartPartCompleteResponse,
  type MultipartPartUrl,
  type MultipartUploadCompleteResponse,
  type MultipartUploadInitResponse,
  type MultipartUploadSessionResponse,
  type PersistedUploadSession,
} from "@/lib/resumable-upload";
import { uploadStatusMessage } from "@/lib/product-flow-status";
import { cn } from "@/lib/utils";
import { useToast } from "./toast";

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
  very_short_video:
    "Very short source may not produce enough captioned content.",
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
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const cancelRequestedRef = useRef(false);
  const operationIdRef = useRef(0);
  const storageKey = uploadSessionStorageKey(workspaceId, projectId);
  const [persistedSession, setPersistedSession] =
    useState<PersistedUploadSession | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mediaProbe, setMediaProbe] = useState<BrowserMediaProbe>(idleProbe);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [videoId, setVideoId] = useState<string | null>(null);

  useEffect(() => {
    const saved = parseUploadSession(window.localStorage.getItem(storageKey));
    if (saved && Date.parse(saved.expiresAt) > Date.now()) {
      // File bytes cannot be persisted; the user must reselect this exact file.
      // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage is browser-only discovery state
      setPersistedSession(saved);
      setVideoId(saved.videoId);
      setMessage(
        `An interrupted upload for ${saved.file.name} is available. Reselect the same file to resume.`,
      );
    } else if (saved) {
      window.localStorage.removeItem(storageKey);
    }

    return () => {
      operationIdRef.current += 1;
      // Unmount only interrupts the browser request. It must not abort the
      // durable server session, which remains resumable after navigation.
      abortRef.current?.abort();
    };
  }, [storageKey]);

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

    if (persistedSession && !sameFile(file, persistedSession.file)) {
      blockingWarnings.push(
        `This does not match the interrupted upload (${persistedSession.file.name}). Reselect that file or cancel the saved upload first.`,
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
  }, [file, mediaProbe.warnings, persistedSession]);

  const hasBlockingPreflightIssue = Boolean(preflight?.blockingWarnings.length);
  const hasPersistedMismatch = Boolean(
    file && persistedSession && !sameFile(file, persistedSession.file),
  );
  const isCheckingMedia = mediaProbe.status === "loading";
  const isWorking = phase === "uploading" || phase === "processing";
  const uploadButtonLabel =
    persistedSession && file && sameFile(file, persistedSession.file)
      ? "Resume upload"
      : hasExistingSource
        ? "Upload replacement"
        : "Upload";

  useEffect(() => {
    if (!file) return;

    const extension = getFileExtension(file.name);
    const mimeType = resolveUploadMimeType(file);
    if (
      !ACCEPTED_MIME_TYPES.has(mimeType) ||
      !extensionMatchesMime(extension, mimeType) ||
      file.size === 0 ||
      file.size > MAX_UPLOAD_SIZE_BYTES
    ) {
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    const video = document.createElement("video");
    let settled = false;

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
    if (isWorking) return;
    operationIdRef.current += 1;
    abortRef.current?.abort();
    setFile(nextFile);
    setMediaProbe(
      nextFile && canProbeBrowserMedia(nextFile)
        ? {
            status: "loading",
            durationMs: null,
            width: null,
            height: null,
            warnings: [],
          }
        : idleProbe,
    );
    setPhase(nextFile ? "preflight" : "idle");
    setProgress(0);
    setMessage(
      nextFile && persistedSession
        ? sameFile(nextFile, persistedSession.file)
          ? "File matched. Upload will resume from server-confirmed progress."
          : `This file does not match ${persistedSession.file.name}. Reselect it or cancel the saved upload.`
        : persistedSession
          ? `Reselect ${persistedSession.file.name} to resume. File bytes are never stored in this browser.`
          : null,
    );
    setVideoId(persistedSession?.videoId ?? null);
    if (!nextFile && inputRef.current) inputRef.current.value = "";
  }

  function rememberSession(session: PersistedUploadSession) {
    window.localStorage.setItem(storageKey, serializeUploadSession(session));
    setPersistedSession(session);
  }

  function clearRememberedSession() {
    window.localStorage.removeItem(storageKey);
    setPersistedSession(null);
  }

  async function startUpload() {
    if (!file || !preflight || abortRef.current) return;
    if (hasBlockingPreflightIssue || hasPersistedMismatch || isCheckingMedia) {
      setPhase("preflight");
      setMessage(
        isCheckingMedia
          ? "Metadata checks are still running."
          : hasPersistedMismatch
            ? "Reselect the original file or cancel the saved upload before starting another."
            : "Choose a supported, non-empty video within the upload limit.",
      );
      return;
    }

    const operationId = ++operationIdRef.current;
    const isCurrentOperation = () => operationIdRef.current === operationId;
    cancelRequestedRef.current = false;
    setPhase("uploading");
    setProgress(0);
    setMessage(persistedSession ? "Checking saved upload..." : "Registering upload...");
    const controller = new AbortController();
    abortRef.current = controller;
    let sessionRecord = persistedSession;
    let requestHeaders: Record<string, string> | null = null;

    try {
      const headers = await buildApiHeaders({
        workspaceId,
        userId,
        accessToken: apiAccessToken,
      });
      requestHeaders = headers;

      let totalParts: number;
      let partSizeBytes: number;
      let completedParts: number;
      let serverUploadedBytes: number;
      let partUrls: MultipartPartUrl[];
      let activeVideoId: string;
      let activeSessionId: string;

      if (sessionRecord) {
        if (!sameFile(file, sessionRecord.file)) {
          throw new Error("The selected file does not match the saved upload session.");
        }
        const resumed = await fetchMultipartSession(
          projectId,
          sessionRecord.sessionId,
          headers,
          controller.signal,
        );
        if (
          resumed.session_id !== sessionRecord.sessionId ||
          resumed.video_id !== sessionRecord.videoId ||
          resumed.file_size_bytes !== file.size ||
          resumed.total_parts !== sessionRecord.totalParts ||
          resumed.part_size_bytes !== sessionRecord.partSizeBytes
        ) {
          throw new Error("The saved upload fingerprint no longer matches the server session.");
        }
        if (["aborted", "expired", "failed"].includes(resumed.status)) {
          clearRememberedSession();
          throw new Error(`The saved upload session is ${resumed.status}. Start a new upload.`);
        }
        activeVideoId = resumed.video_id;
        activeSessionId = resumed.session_id;
        totalParts = resumed.total_parts;
        partSizeBytes = resumed.part_size_bytes;
        completedParts = resumed.completed_parts;
        serverUploadedBytes = resumed.uploaded_bytes;
        partUrls = resumed.pending_part_urls;
        sessionRecord = {
          ...sessionRecord,
          expiresAt: resumed.expires_at,
        };
        rememberSession(sessionRecord);
        setMessage(
          completedParts > 0
            ? `Resuming after ${completedParts} confirmed part${completedParts === 1 ? "" : "s"}...`
            : "Resuming saved upload...",
        );
      } else {
        const initResponse = await fetch(
          `${env.apiBaseUrl}/projects/${projectId}/uploads/multipart/init`,
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
        const initialized = (await readJson(initResponse)) as MultipartUploadInitResponse;
        if (!initResponse.ok) {
          throw new Error(apiErrorMessage(initialized, "Upload could not be initialized."));
        }
        activeVideoId = initialized.video_id;
        activeSessionId = initialized.session_id;
        totalParts = initialized.total_parts;
        partSizeBytes = initialized.part_size_bytes;
        completedParts = 0;
        serverUploadedBytes = 0;
        partUrls = initialized.part_urls;
        sessionRecord = {
          version: 1,
          sessionId: initialized.session_id,
          videoId: initialized.video_id,
          expiresAt: initialized.expires_at,
          totalParts: initialized.total_parts,
          partSizeBytes: initialized.part_size_bytes,
          file: fileFingerprint(file),
        };
        // Persist immediately after init, before any signed URL is used.
        rememberSession(sessionRecord);
      }

      setVideoId(activeVideoId);
      setProgress(
        authoritativeProgress(serverUploadedBytes, file.size).percent,
      );

      while (completedParts < totalParts) {
        controller.signal.throwIfAborted();
        if (!partUrls.length) {
          const refreshed = await fetchMultipartSession(
            projectId,
            activeSessionId,
            headers,
            controller.signal,
          );
          if (["aborted", "expired", "failed"].includes(refreshed.status)) {
            throw new Error(`Upload session is ${refreshed.status}.`);
          }
          completedParts = refreshed.completed_parts;
          serverUploadedBytes = refreshed.uploaded_bytes;
          partUrls = refreshed.pending_part_urls;
          setProgress(
            authoritativeProgress(serverUploadedBytes, file.size).percent,
          );
          if (completedParts >= totalParts) break;
          if (!partUrls.length) {
            throw new Error("The server did not return the next pending upload part.");
          }
        }

        for (const initialPartUrl of partUrls) {
          controller.signal.throwIfAborted();
          const range = partByteRange(
            initialPartUrl.part_number,
            partSizeBytes,
            file.size,
          );
          const partBlob = file.slice(range.start, range.end);
          let partUrl = initialPartUrl;
          setMessage(
            `Uploading part ${partUrl.part_number} of ${totalParts}...`,
          );

          const uploadPart = () =>
            putBlobWithProgress(
              resolveProxiedUrl(partUrl.upload_url) ?? partUrl.upload_url,
              partBlob,
              partUrl.upload_headers,
              controller.signal,
              (loadedBytes) => {
                if (!isCurrentOperation()) return;
                setProgress(
                  authoritativeProgress(
                    serverUploadedBytes,
                    file.size,
                    loadedBytes,
                  ).percent,
                );
              },
            );

          let uploadedPart;
          try {
            uploadedPart = await uploadPart();
          } catch (error) {
            if (
              error instanceof SignedUploadError &&
              (error.status === 401 || error.status === 403)
            ) {
              setMessage(`Refreshing upload URL for part ${partUrl.part_number}...`);
              const refreshed = await fetchMultipartSession(
                projectId,
                activeSessionId,
                headers,
                controller.signal,
              );
              const replacement = refreshed.pending_part_urls.find(
                (candidate) => candidate.part_number === partUrl.part_number,
              );
              if (!replacement) {
                throw new Error("The server did not refresh the expired upload URL.");
              }
              partUrl = replacement;
              // A signed URL is refreshed and retried at most once per part.
              uploadedPart = await uploadPart();
            } else {
              throw error;
            }
          }

          setMessage(`Confirming part ${partUrl.part_number} of ${totalParts}...`);
          const confirmResponse = await fetch(
            `${env.apiBaseUrl}/projects/${projectId}/uploads/multipart/${activeSessionId}/parts/${partUrl.part_number}/complete`,
            {
              method: "POST",
              headers: { ...headers, "content-type": "application/json" },
              body: JSON.stringify(
                uploadedPart.etag ? { etag: uploadedPart.etag } : {},
              ),
              signal: controller.signal,
            },
          );
          const confirmed = (await readJson(confirmResponse)) as MultipartPartCompleteResponse;
          if (!confirmResponse.ok) {
            throw new Error(apiErrorMessage(confirmed, `Part ${partUrl.part_number} could not be confirmed.`));
          }
          completedParts = confirmed.completed_parts;
          serverUploadedBytes = confirmed.uploaded_bytes;
          setProgress(
            authoritativeProgress(serverUploadedBytes, file.size).percent,
          );
        }

        // The server returns at most five URLs, so fetch its next authoritative
        // pending window rather than deriving completion from local state.
        partUrls = [];
      }

      setProgress(100);
      setMessage("Completing multipart upload...");
      const completeResponse = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/uploads/multipart/${activeSessionId}/complete`,
        {
          method: "POST",
          headers: { ...headers, "content-type": "application/json" },
          body: JSON.stringify({}),
          signal: controller.signal,
        },
      );
      const completePayload = (await readJson(completeResponse)) as MultipartUploadCompleteResponse;
      if (!completeResponse.ok) {
        throw new Error(apiErrorMessage(completePayload, "Upload could not be completed."));
      }

      // Completion is durable and idempotent. The processing page owns the
      // upload from here, so no resumable browser record is needed.
      clearRememberedSession();
      setPhase("processing");
      setMessage("Running server media validation...");
      const readyUpload = await waitForUploadReady(
        projectId,
        completePayload.video_id,
        headers,
        controller.signal,
        (status) => {
          if (!isCurrentOperation()) return;
          setProgress(100);
          setMessage(
            status === "ready"
              ? "Server validation passed."
              : statusMessage(status),
          );
        },
      );
      if (!isCurrentOperation()) return;
      setPhase("complete");
      setMessage(serverValidationMessage(readyUpload?.metadata?.warnings));
      router.refresh();
    } catch (error) {
      if (!isCurrentOperation()) return;
      if ((error as Error).name === "AbortError") {
        if (!cancelRequestedRef.current) return;
        const sessionId = sessionRecord?.sessionId;
        try {
          const headers =
            requestHeaders ??
            (await buildApiHeaders({
              workspaceId,
              userId,
              accessToken: apiAccessToken,
            }));
          if (sessionId) {
            await cancelUploadRegistration(projectId, sessionId, headers);
          }
        } catch {
          setPhase("failed");
          setProgress(0);
          setMessage(
            "Cancellation could not be confirmed. Your upload session was kept so you can retry cancellation.",
          );
          return;
        }
        clearRememberedSession();
        setPhase("canceled");
        setProgress(0);
        setVideoId(null);
        setMessage("Upload canceled. The multipart session was aborted.");
        return;
      }
      setPhase("failed");
      const failMsg = friendlyUploadError((error as Error).message);
      setMessage(
        sessionRecord
          ? `${failMsg} Your confirmed progress is saved; retry with the same file.`
          : failMsg,
      );
      if (shouldToast("short_action_feedback")) {
        toast({
          title: "Upload Failed",
          description: failMsg,
          variant: "error",
        });
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      cancelRequestedRef.current = false;
    }
  }

  async function cancelUpload() {
    setMessage("Canceling upload...");
    cancelRequestedRef.current = true;
    if (abortRef.current) {
      abortRef.current.abort();
      return;
    }
    const sessionId = persistedSession?.sessionId;
    try {
      if (sessionId) {
        const headers = await buildApiHeaders({
          workspaceId,
          userId,
          accessToken: apiAccessToken,
        });
        await cancelUploadRegistration(projectId, sessionId, headers);
      }
      clearRememberedSession();
      setPhase("canceled");
      setProgress(0);
      setVideoId(null);
      setMessage("Upload canceled. The multipart session was aborted.");
    } catch {
      setPhase("failed");
      setMessage(
        "Cancellation could not be confirmed. Your upload session was kept so you can retry cancellation.",
      );
    } finally {
      cancelRequestedRef.current = false;
    }
  }

  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1.05fr)_minmax(320px,0.95fr)]">
      {/* ── Drop zone ── */}
      <div
        className={cn(
          "border-2 border-dashed border-foreground bg-surface rounded-md hover:bg-surface-elevated transition-colors text-center p-10 cursor-pointer flex flex-col items-center justify-center gap-4 min-h-[280px]",
          "focus-within:ring-2 focus-within:ring-primary",
          isWorking && "opacity-80",
        )}
        aria-disabled={isWorking}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = isWorking ? "none" : "copy";
          if (isWorking) return;
          event.currentTarget.classList.add("border-primary", "bg-primary/10");
        }}
        onDragLeave={(event) => {
          event.currentTarget.classList.remove("border-primary", "bg-primary/10");
        }}
        onDrop={(event) => {
          event.preventDefault();
          event.currentTarget.classList.remove("border-primary", "bg-primary/10");
          if (isWorking) return;
          selectFile(event.dataTransfer.files.item(0));
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/webm"
          disabled={isWorking}
          className="sr-only"
          onChange={(event) => selectFile(event.target.files?.item(0) ?? null)}
        />

        {/* Phase: complete */}
        {phase === "complete" && (
          <div className="border-2 border-foreground bg-card p-6 rounded-md shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] flex flex-col items-center gap-3 w-full max-w-sm">
            <div className="size-14 rounded-md border-2 border-foreground bg-primary text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] flex items-center justify-center mx-auto mb-4">
              <CheckCircle2 className="size-7" aria-hidden="true" />
            </div>
            <h2 className="text-lg font-black uppercase text-foreground">Upload complete</h2>
            <p className="text-sm text-muted-foreground">Your video was uploaded and validated successfully.</p>
          </div>
        )}

        {/* Phase: failed */}
        {phase === "failed" && (
          <div className="border-2 border-foreground bg-card p-6 rounded-md shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] flex flex-col items-center gap-3 w-full max-w-sm">
            <div className="size-14 rounded-md border-2 border-foreground bg-danger text-white shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] flex items-center justify-center mx-auto">
              <XCircle className="size-7" aria-hidden="true" />
            </div>
            <h2 className="text-lg font-black uppercase text-foreground">Upload failed</h2>
            <p className="text-sm text-muted-foreground">Something went wrong. Review the details and retry.</p>
          </div>
        )}

        {/* Phase: uploading */}
        {phase === "uploading" && (
          <div className="flex w-full max-w-xs flex-col gap-3">
            <p className="text-lg font-black uppercase text-foreground">{message ?? "Uploading…"}</p>
            <div className="h-3 border-2 border-foreground bg-muted rounded-full overflow-hidden" aria-label="Upload progress">
              <div
                className="bg-primary h-full transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-sm font-mono font-bold text-primary">{progress}%</p>
          </div>
        )}

        {/* Phase: processing */}
        {phase === "processing" && (
          <div className="flex flex-col items-center gap-3 bg-card border-2 border-foreground p-6 rounded-md shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] w-full max-w-sm">
            <div className="flex gap-1">
              <span className="size-3.5 rounded-md border border-foreground bg-primary animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="size-3.5 rounded-md border border-foreground bg-primary animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="size-3.5 rounded-md border border-foreground bg-primary animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
            <p className="text-lg font-black uppercase text-foreground">Processing…</p>
            <p className="text-sm font-mono text-muted-foreground">{message ?? "Running server media validation…"}</p>
          </div>
        )}

        {/* Phase: idle / preflight / canceled */}
        {(phase === "idle" || phase === "preflight" || phase === "canceled") && (
          <>
            <div className="size-14 rounded-md border-2 border-foreground bg-primary text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] flex items-center justify-center mx-auto mb-4">
              <UploadCloud className="size-7 text-primary-foreground" aria-hidden="true" />
            </div>
            <div>
              <h2 className="text-lg font-black uppercase text-foreground">
                {hasExistingSource ? "Replace source video" : "Upload source video"}
              </h2>
              <p className="mt-1 max-w-md text-sm text-muted-foreground">
                {hasExistingSource
                  ? "Choose a new MP4, MOV, or WebM file. It will become the active source after preprocessing finishes."
                  : "Drag in an MP4, MOV, or WebM file. Preprocessing verifies duration, resolution, audio, and thumbnail."}
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              {["MP4", "MOV", "WebM"].map((fmt) => (
                <span key={fmt} className="rounded-md border-2 border-foreground bg-muted px-3 py-1 text-xs font-mono font-bold text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                  {fmt}
                </span>
              ))}
            </div>
            {persistedSession ? (
              <p className="max-w-md border-2 border-foreground bg-primary/10 p-3 text-sm font-mono font-bold text-foreground rounded-md">
                Reselect <span className="break-all text-primary">{persistedSession.file.name}</span> to resume from server-confirmed progress. File bytes are not stored locally.
              </p>
            ) : null}
            <div className="flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={isWorking}
                className="inline-flex h-10 items-center justify-center bg-primary text-primary-foreground border-2 border-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-4 text-sm font-bold uppercase tracking-wider disabled:opacity-50"
              >
                {hasExistingSource ? "Choose replacement" : "Choose video"}
              </button>
              {file ? (
                <button
                  type="button"
                  onClick={() => selectFile(null)}
                  disabled={isWorking}
                  className="inline-flex h-10 items-center justify-center bg-background text-foreground border-2 border-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-4 text-sm font-bold uppercase tracking-wider disabled:opacity-50"
                >
                  <X className="mr-2 size-4" aria-hidden="true" strokeWidth={2.5} />
                  Clear
                </button>
              ) : null}
              {persistedSession && !isWorking ? (
                <button
                  type="button"
                  onClick={() => void cancelUpload()}
                  className="inline-flex h-10 items-center justify-center bg-background text-danger border-2 border-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] px-4 text-sm font-bold uppercase tracking-wider"
                >
                  Cancel saved upload
                </button>
              ) : null}
            </div>
          </>
        )}
      </div>

      {/* ── Preflight aside ── */}
      <aside className="border-2 border-foreground bg-surface rounded-md shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] p-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-black uppercase text-foreground font-mono">
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
            {/* File info card */}
            <div className="bg-card border-2 border-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] p-4 flex items-start gap-3 mb-4">
              <div className="min-w-0 flex-1">
                <MetadataRow label="Filename" value={preflight.filename} />
              </div>
            </div>
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
              <div className="flex items-center gap-2 border-2 border-foreground bg-muted p-3 text-sm font-mono font-bold text-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Checking browser metadata...
              </div>
            ) : null}
            {/* Blocking errors */}
            {preflight.blockingWarnings.map((warning) => (
              <div
                key={warning}
                className="border-2 border-foreground bg-danger/10 px-3 py-2.5 text-sm text-danger rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] flex gap-2 items-start mb-2 font-mono font-bold"
              >
                <AlertTriangle className="size-4 mt-0.5 shrink-0 text-danger" aria-hidden="true" />
                <span>{warning}</span>
              </div>
            ))}
            {/* Non-blocking warnings */}
            {preflight.warnings
              .filter((w) => !preflight.blockingWarnings.includes(w))
              .map((warning) => (
                <div
                  key={warning}
                  className="border-2 border-foreground bg-amber-500/10 px-3 py-2.5 text-sm text-amber-600 dark:text-amber-400 rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] flex gap-2 items-start mb-2 font-mono font-bold"
                >
                  <AlertTriangle className="size-4 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
                  <span>{warning}</span>
                </div>
              ))}
            {!preflight.warnings.length && !isCheckingMedia ? (
              <div className="flex items-center gap-2 border-2 border-foreground bg-emerald-500/10 px-3 py-2.5 text-sm font-mono font-bold text-emerald-600 dark:text-emerald-400 rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                <span>
                  Browser preflight passed. Upload will run server validation for
                  codec, audio, frame rate, decode, and render readiness.
                </span>
              </div>
            ) : null}
            <div>
              <div className="h-3 border-2 border-foreground bg-muted rounded-full overflow-hidden" aria-label="Upload progress">
                <div
                  className="bg-primary h-full transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              {progress > 0 ? (
                <p className="mt-1 text-sm font-mono font-bold text-primary">
                  {progress}%
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                className="w-full h-11 bg-primary text-primary-foreground border-2 border-foreground rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] font-bold uppercase tracking-wider disabled:opacity-50 mt-4 inline-flex items-center justify-center"
                onClick={startUpload}
                disabled={
                  hasBlockingPreflightIssue ||
                  hasPersistedMismatch ||
                  isCheckingMedia ||
                  isWorking
                }
              >
                {isWorking ? (
                  <Loader2
                    className="mr-2 size-4 animate-spin text-primary-foreground"
                    aria-hidden="true"
                  />
                ) : null}
                {phase === "uploading"
                  ? "Uploading"
                  : phase === "processing"
                    ? "Processing"
                    : uploadButtonLabel}
              </button>
              {phase === "uploading" ? (
                <button
                  type="button"
                  onClick={cancelUpload}
                  className="bg-background text-foreground border-2 border-foreground rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] h-11 px-4 font-bold uppercase tracking-wider w-full text-center"
                >
                  Cancel
                </button>
              ) : null}
              {phase === "failed" || phase === "canceled" ? (
                <button
                  type="button"
                  onClick={startUpload}
                  disabled={hasBlockingPreflightIssue || isCheckingMedia}
                  className="bg-primary text-primary-foreground border-2 border-foreground rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] h-11 px-4 font-bold uppercase tracking-wider w-full flex items-center justify-center disabled:opacity-50"
                >
                  <RotateCcw className="mr-2 size-4" aria-hidden="true" />
                  Retry
                </button>
              ) : null}
            </div>
            {videoId ? (
              <p className="text-xs font-mono text-muted-foreground mt-2">
                Registered upload {videoId.slice(0, 8)}.
              </p>
            ) : null}
            {message ? (
              <p
                className={cn(
                  "border-2 border-foreground p-3 text-sm rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] font-mono font-bold mt-2",
                  phase === "failed"
                    ? "bg-danger/10 text-danger"
                    : phase === "complete"
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {message}
              </p>
            ) : null}
          </div>
        ) : (
          <p className="mt-5 text-sm font-mono leading-6 text-muted-foreground">
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
      ? "bg-danger text-white"
      : label === "ready" || phase === "complete"
        ? "bg-emerald-500 text-white"
        : label === "warnings"
          ? "bg-amber-500 text-black"
          : phase === "uploading" || phase === "processing" || isCheckingMedia
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground";
  return (
    <span
      className={cn(
        "px-2.5 py-0.5 font-bold font-mono border-2 border-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] text-xs capitalize inline-flex items-center",
        tone,
      )}
    >
      {label}
    </span>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-2 border-foreground bg-muted p-2 rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
      <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground font-mono">
        {label}
      </p>
      <p className="mt-0.5 break-all text-xs font-mono font-bold text-foreground">{value}</p>
    </div>
  );
}

async function readJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

async function fetchMultipartSession(
  projectId: string,
  sessionId: string,
  headers: Record<string, string>,
  signal: AbortSignal,
): Promise<MultipartUploadSessionResponse> {
  const response = await fetch(
    `${env.apiBaseUrl}/projects/${projectId}/uploads/multipart/${sessionId}`,
    { headers, signal, cache: "no-store" },
  );
  const payload = (await readJson(response)) as MultipartUploadSessionResponse;
  if (!response.ok) {
    throw new Error(apiErrorMessage(payload, "Saved upload could not be resumed."));
  }
  return payload;
}

async function cancelUploadRegistration(
  projectId: string,
  sessionId: string,
  headers: Record<string, string>,
) {
  const response = await fetch(
    `${env.apiBaseUrl}/projects/${projectId}/uploads/multipart/${sessionId}/abort`,
    { method: "POST", headers },
  );
  if (!response.ok) {
    const payload = await readJson(response);
    throw new Error(apiErrorMessage(payload, "Upload session could not be aborted."));
  }
}

function apiErrorMessage(payload: unknown, fallback: string): string {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    payload.error &&
    typeof payload.error === "object" &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }
  return fallback;
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
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
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

function canProbeBrowserMedia(file: File) {
  const extension = getFileExtension(file.name);
  const mimeType = resolveUploadMimeType(file);
  return (
    ACCEPTED_MIME_TYPES.has(mimeType) &&
    extensionMatchesMime(extension, mimeType) &&
    file.size > 0 &&
    file.size <= MAX_UPLOAD_SIZE_BYTES
  );
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
  return uploadStatusMessage(status);
}

function friendlyUploadError(message: string) {
  if (/file exceeds|too large/i.test(message))
    return "This file exceeds the upload limit for your current workspace plan. Please upgrade your plan or upload a shorter/compressed video.";
  if (/mime|unsupported|extension/i.test(message))
    return "Unsupported format. Please select a valid MP4, MOV, or WebM video file and try again.";
  if (/audio track|audio availability/i.test(message))
    return "No audio track detected. The system requires an audio track to generate captions. Please upload a video containing audio.";
  if (/duration|resolution|frame rate|codec|render ready/i.test(message))
    return `Server validation failed: ${message}`;
  if (/probe|inspect|decode|thumbnail/i.test(message))
    return "Video decoding failed. The uploaded file appears corrupt or encoded in an unsupported format. Try re-exporting your video as a standard H.264 MP4 before re-uploading.";
  if (/size did not match/i.test(message))
    return "Upload connection interrupted (size mismatch). Please check your internet connection and try uploading again.";
  if (/storage/i.test(message))
    return `Storage upload error: ${message}. Check your local storage quota or retry.`;
  return (
    message ||
    "Upload failed due to a network connection issue. Check your internet connection and try again."
  );
}

function serverValidationMessage(warnings: unknown) {
  const warningCodes = Array.isArray(warnings)
    ? warnings.filter(
        (warning): warning is string => typeof warning === "string",
      )
    : [];
  if (!warningCodes.length) {
    return "Server validation passed. Source video, metadata, thumbnail, and render readiness are confirmed.";
  }
  const warningText = warningCodes
    .map((warning) => SERVER_WARNING_MESSAGES[warning] ?? warning)
    .join(" ");
  return `Server validation passed with warnings: ${warningText}`;
}
