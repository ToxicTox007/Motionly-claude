export const DEFAULT_MAX_ENVELOPE_BYTES = 262_144;
export const DEFAULT_MAX_ENVELOPE_ITEMS = 20;

const ALLOWED_CONTENT_TYPES = new Set([
  "application/x-sentry-envelope",
  "application/octet-stream",
  "text/plain",
]);

export class TunnelHttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export class ForwardingGate {
  private active: number;
  private readonly maximum: number;

  constructor(maximum: number) {
    this.maximum = maximum;
    this.active = 0;
  }

  tryAcquire(): (() => void) | null {
    if (this.active >= this.maximum) return null;
    this.active += 1;
    let released = false;
    return () => {
      if (!released) {
        released = true;
        this.active -= 1;
      }
    };
  }
}

export type TunnelFetcher = (input: URL, init: RequestInit) => Promise<Response>;

export function boundedPositiveInteger(
  value: string | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isSafeInteger(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

export function isEnvelopeContentType(value: string | null): boolean {
  if (!value) return true;
  return ALLOWED_CONTENT_TYPES.has(value.split(";", 1)[0].trim().toLowerCase());
}

export async function readBoundedEnvelope(
  request: Pick<Request, "body" | "headers">,
  maxBytes: number,
): Promise<Uint8Array> {
  const contentLength = request.headers.get("content-length");
  if (contentLength) {
    if (!/^\d+$/.test(contentLength)) {
      throw new TunnelHttpError(400, "Invalid Content-Length.");
    }
    const declaredBytes = Number.parseInt(contentLength, 10);
    if (!Number.isSafeInteger(declaredBytes)) {
      throw new TunnelHttpError(400, "Invalid Content-Length.");
    }
    if (declaredBytes > maxBytes) {
      throw new TunnelHttpError(413, "Envelope exceeds the maximum allowed size.");
    }
  }
  if (!request.body) {
    throw new TunnelHttpError(400, "An envelope body is required.");
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel("Envelope exceeds the maximum allowed size.");
        throw new TunnelHttpError(413, "Envelope exceeds the maximum allowed size.");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const envelope = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    envelope.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return envelope;
}

export function envelopeUrlForDsn(dsn: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(dsn);
  } catch {
    throw new TunnelHttpError(500, "The server error-reporting DSN is invalid.");
  }
  const projectParts = parsed.pathname.split("/").filter(Boolean);
  if (
    parsed.protocol !== "https:" ||
    !parsed.hostname ||
    parsed.search ||
    parsed.hash ||
    projectParts.length !== 1 ||
    !/^[A-Za-z0-9_-]{1,120}$/.test(projectParts[0])
  ) {
    throw new TunnelHttpError(500, "The server error-reporting DSN is invalid.");
  }
  return new URL(`/api/${projectParts[0]}/envelope/`, parsed.origin);
}

export function validateEnvelope(
  envelope: Uint8Array,
  targetUrl: URL,
  maxItems = DEFAULT_MAX_ENVELOPE_ITEMS,
): void {
  if (envelope.byteLength === 0) {
    throw new TunnelHttpError(400, "An envelope body is required.");
  }

  let offset = 0;
  const envelopeHeader = parseJsonRecord(readLine(envelope, offset), "envelope header");
  offset = nextLineOffset(envelope, offset);
  if (typeof envelopeHeader.dsn === "string" && envelopeHeader.dsn) {
    const suppliedTarget = envelopeUrlForDsn(envelopeHeader.dsn);
    if (suppliedTarget.href !== targetUrl.href) {
      throw new TunnelHttpError(400, "Envelope DSN does not match this tunnel.");
    }
  } else if (envelopeHeader.dsn !== undefined) {
    throw new TunnelHttpError(400, "Envelope DSN is invalid.");
  }

  let itemCount = 0;
  while (offset < envelope.byteLength) {
    const itemHeader = parseJsonRecord(readLine(envelope, offset), "envelope item header");
    offset = nextLineOffset(envelope, offset);
    itemCount += 1;
    if (itemCount > maxItems) {
      throw new TunnelHttpError(400, "Envelope contains too many items.");
    }
    if (
      typeof itemHeader.type !== "string" ||
      !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(itemHeader.type)
    ) {
      throw new TunnelHttpError(400, "Envelope item type is invalid.");
    }

    const length = itemHeader.length;
    if (length !== undefined) {
      if (typeof length !== "number" || !Number.isSafeInteger(length) || length < 0) {
        throw new TunnelHttpError(400, "Envelope item length is invalid.");
      }
      if (length > envelope.byteLength - offset) {
        throw new TunnelHttpError(400, "Envelope item exceeds its declared length.");
      }
      offset += length;
      if (offset < envelope.byteLength && envelope[offset] === 10) offset += 1;
      continue;
    }

    const payloadEnd = indexOfLineBreak(envelope, offset);
    if (payloadEnd === offset) {
      throw new TunnelHttpError(400, "Envelope item payload is empty.");
    }
    offset = payloadEnd === -1 ? envelope.byteLength : payloadEnd + 1;
  }

  if (itemCount === 0) {
    throw new TunnelHttpError(400, "Envelope must contain at least one item.");
  }
}

export async function forwardEnvelope({
  fetcher,
  envelope,
  envelopeUrl,
  contentType,
  timeoutMs,
}: {
  fetcher: TunnelFetcher;
  envelope: Uint8Array;
  envelopeUrl: URL;
  contentType: string;
  timeoutMs: number;
}): Promise<number> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const upstream = await fetcher(envelopeUrl, {
      method: "POST",
      headers: { "content-type": contentType },
      body: new Uint8Array(envelope).buffer,
      cache: "no-store",
      signal: controller.signal,
    });
    return upstream.ok ? 200 : 502;
  } catch {
    return controller.signal.aborted ? 504 : 502;
  } finally {
    clearTimeout(timeout);
  }
}

function readLine(bytes: Uint8Array, offset: number): string {
  const lineEnd = indexOfLineBreak(bytes, offset);
  const end = lineEnd === -1 ? bytes.byteLength : lineEnd;
  if (end === offset) {
    throw new TunnelHttpError(400, "Envelope contains an empty header line.");
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(offset, end));
  } catch {
    throw new TunnelHttpError(400, "Envelope headers must be UTF-8 JSON.");
  }
}

function nextLineOffset(bytes: Uint8Array, offset: number): number {
  const lineEnd = indexOfLineBreak(bytes, offset);
  if (lineEnd === -1) {
    throw new TunnelHttpError(400, "Envelope header is incomplete.");
  }
  return lineEnd + 1;
}

function indexOfLineBreak(bytes: Uint8Array, offset: number): number {
  for (let index = offset; index < bytes.byteLength; index += 1) {
    if (bytes[index] === 10) return index;
  }
  return -1;
}

function parseJsonRecord(value: string, label: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("not a JSON record");
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new TunnelHttpError(400, `Invalid ${label}.`);
  }
}
