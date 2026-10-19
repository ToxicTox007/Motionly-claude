export const RESUMABLE_UPLOAD_VERSION = 1 as const;

export type MultipartSessionStatus =
  | "active"
  | "completing"
  | "completed"
  | "aborted"
  | "expired"
  | "failed";

export type MultipartPartUrl = {
  part_number: number;
  upload_url: string;
  upload_headers: Record<string, string>;
  expires_in_seconds: number;
};

export type MultipartUploadInitResponse = {
  video_id: string;
  session_id: string;
  status: MultipartSessionStatus;
  total_parts: number;
  part_size_bytes: number;
  part_urls: MultipartPartUrl[];
  expires_at: string;
  warnings: string[];
};

export type MultipartUploadSessionResponse = {
  session_id: string;
  video_id: string;
  status: MultipartSessionStatus;
  total_parts: number;
  completed_parts: number;
  uploaded_bytes: number;
  file_size_bytes: number;
  part_size_bytes: number;
  pending_part_urls: MultipartPartUrl[];
  expires_at: string;
};

export type MultipartPartCompleteResponse = {
  session_id: string;
  part_number: number;
  status: string;
  verified_size_bytes: number;
  completed_parts: number;
  uploaded_bytes: number;
};

export type MultipartUploadCompleteResponse = {
  session_id: string;
  video_id: string;
  status: MultipartSessionStatus;
  source_reference_id: string;
  verified_size_bytes: number;
  verified_sha256: string;
  job_id: string;
};

export type UploadFileFingerprint = {
  name: string;
  size: number;
  type: string;
  lastModified: number;
};

/**
 * The durable record deliberately excludes credentials and signed URLs. The
 * server remains authoritative for completed parts and uploaded byte counts.
 */
export type PersistedUploadSession = {
  version: typeof RESUMABLE_UPLOAD_VERSION;
  sessionId: string;
  videoId: string;
  expiresAt: string;
  totalParts: number;
  partSizeBytes: number;
  file: UploadFileFingerprint;
};

export type PartByteRange = {
  /** Inclusive Blob.slice start offset. */
  start: number;
  /** Exclusive Blob.slice end offset. */
  end: number;
  size: number;
};

export type AuthoritativeUploadProgress = {
  uploadedBytes: number;
  percent: number;
};

export type PutBlobResult = {
  status: number;
  etag: string | null;
};

export class SignedUploadError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly responseText: string,
  ) {
    super(message);
    this.name = "SignedUploadError";
  }
}

export function fileFingerprint(
  file: Pick<File, "name" | "size" | "type" | "lastModified">,
): UploadFileFingerprint {
  return {
    name: file.name,
    size: file.size,
    type: file.type,
    lastModified: file.lastModified,
  };
}

export function sameFile(
  file: Pick<File, "name" | "size" | "type" | "lastModified">,
  fingerprint: UploadFileFingerprint,
): boolean {
  return (
    file.name === fingerprint.name &&
    file.size === fingerprint.size &&
    file.type === fingerprint.type &&
    file.lastModified === fingerprint.lastModified
  );
}

export function uploadSessionStorageKey(
  workspaceId: string,
  projectId: string,
): string {
  return `motionly:multipart-upload:v${RESUMABLE_UPLOAD_VERSION}:${encodeURIComponent(workspaceId)}:${encodeURIComponent(projectId)}`;
}

/** Serialize through an explicit allowlist so future response fields cannot leak. */
export function serializeUploadSession(
  session: PersistedUploadSession,
): string {
  return JSON.stringify({
    version: RESUMABLE_UPLOAD_VERSION,
    sessionId: session.sessionId,
    videoId: session.videoId,
    expiresAt: session.expiresAt,
    totalParts: session.totalParts,
    partSizeBytes: session.partSizeBytes,
    file: {
      name: session.file.name,
      size: session.file.size,
      type: session.file.type,
      lastModified: session.file.lastModified,
    },
  });
}

export function parseUploadSession(
  serialized: string | null | undefined,
): PersistedUploadSession | null {
  if (!serialized) return null;
  try {
    const candidate = JSON.parse(serialized) as unknown;
    if (!isRecord(candidate) || !isRecord(candidate.file)) return null;
    if (candidate.version !== RESUMABLE_UPLOAD_VERSION) return null;
    if (
      !isNonEmptyString(candidate.sessionId) ||
      !isNonEmptyString(candidate.videoId) ||
      !isNonEmptyString(candidate.expiresAt) ||
      !Number.isFinite(Date.parse(candidate.expiresAt)) ||
      !isPositiveInteger(candidate.totalParts) ||
      !isPositiveInteger(candidate.partSizeBytes) ||
      !isNonEmptyString(candidate.file.name) ||
      typeof candidate.file.type !== "string" ||
      !isNonNegativeInteger(candidate.file.size) ||
      !isNonNegativeInteger(candidate.file.lastModified)
    ) {
      return null;
    }
    return {
      version: RESUMABLE_UPLOAD_VERSION,
      sessionId: candidate.sessionId,
      videoId: candidate.videoId,
      expiresAt: candidate.expiresAt,
      totalParts: candidate.totalParts,
      partSizeBytes: candidate.partSizeBytes,
      file: {
        name: candidate.file.name,
        size: candidate.file.size,
        type: candidate.file.type,
        lastModified: candidate.file.lastModified,
      },
    };
  } catch {
    return null;
  }
}

export function partByteRange(
  partNumber: number,
  partSizeBytes: number,
  fileSizeBytes: number,
): PartByteRange {
  if (!isPositiveInteger(partNumber))
    throw new RangeError("partNumber must be a positive integer.");
  if (!isPositiveInteger(partSizeBytes))
    throw new RangeError("partSizeBytes must be a positive integer.");
  if (!isPositiveInteger(fileSizeBytes))
    throw new RangeError("fileSizeBytes must be a positive integer.");
  const start = (partNumber - 1) * partSizeBytes;
  if (!Number.isSafeInteger(start) || start >= fileSizeBytes)
    throw new RangeError("partNumber is outside the file.");
  const end = Math.min(start + partSizeBytes, fileSizeBytes);
  return { start, end, size: end - start };
}

/**
 * Progress starts from server-confirmed bytes and may include only the current
 * XHR's transient bytes. It never trusts a locally persisted part count.
 */
export function authoritativeProgress(
  serverUploadedBytes: number,
  fileSizeBytes: number,
  inFlightPartBytes = 0,
): AuthoritativeUploadProgress {
  const total = Math.max(0, Number.isFinite(fileSizeBytes) ? fileSizeBytes : 0);
  const bytes = Math.min(
    total,
    Math.max(
      0,
      (Number.isFinite(serverUploadedBytes) ? serverUploadedBytes : 0) +
        (Number.isFinite(inFlightPartBytes) ? inFlightPartBytes : 0),
    ),
  );
  return {
    uploadedBytes: bytes,
    percent: total > 0 ? Math.floor((bytes / total) * 100) : 0,
  };
}

export function putBlobWithProgress(
  url: string,
  blob: Blob,
  headers: Record<string, string>,
  signal: AbortSignal,
  onProgress: (loadedBytes: number, totalBytes: number) => void,
): Promise<PutBlobResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback();
    };
    const onAbort = () => xhr.abort();

    xhr.open("PUT", url);
    for (const [key, value] of Object.entries(headers)) {
      xhr.setRequestHeader(key, value);
    }
    xhr.upload.onprogress = (event) => {
      onProgress(
        event.loaded,
        event.lengthComputable ? event.total : blob.size,
      );
    };
    xhr.onload = () =>
      finish(() => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve({ status: xhr.status, etag: xhr.getResponseHeader("etag") });
          return;
        }
        reject(
          new SignedUploadError(
            `Storage rejected upload part (${xhr.status}).`,
            xhr.status,
            xhr.responseText,
          ),
        );
      });
    xhr.onerror = () =>
      finish(() =>
        reject(
          new SignedUploadError(
            "Storage upload failed. Check your connection and try again.",
            0,
            xhr.responseText,
          ),
        ),
      );
    xhr.onabort = () =>
      finish(() => reject(new DOMException("Aborted", "AbortError")));

    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
    else xhr.send(blob);
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}
