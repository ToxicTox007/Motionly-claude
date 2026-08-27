import { NextRequest, NextResponse } from "next/server";

import {
  DEFAULT_MAX_ENVELOPE_BYTES,
  ForwardingGate,
  TunnelHttpError,
  boundedPositiveInteger,
  envelopeUrlForDsn,
  forwardEnvelope,
  isEnvelopeContentType,
  readBoundedEnvelope,
  validateEnvelope,
} from "./tunnel";

export const runtime = "nodejs";

const forwardingGate = new ForwardingGate(
  boundedPositiveInteger(process.env.GLITCHTIP_TUNNEL_MAX_CONCURRENT, 8, 1, 64),
);

export async function POST(request: NextRequest) {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.SENTRY_DSN;
  if (!dsn) {
    return new NextResponse(null, { status: 204 });
  }

  const maxBytes = boundedPositiveInteger(
    process.env.GLITCHTIP_TUNNEL_MAX_ENVELOPE_BYTES,
    DEFAULT_MAX_ENVELOPE_BYTES,
    16_384,
    1_048_576,
  );
  const timeoutMs = boundedPositiveInteger(
    process.env.GLITCHTIP_TUNNEL_FORWARD_TIMEOUT_MS,
    3_000,
    100,
    10_000,
  );

  try {
    if (!isEnvelopeContentType(request.headers.get("content-type"))) {
      throw new TunnelHttpError(415, "Unsupported envelope content type.");
    }
    const envelopeUrl = envelopeUrlForDsn(dsn);
    const envelope = await readBoundedEnvelope(request, maxBytes);
    validateEnvelope(envelope, envelopeUrl);

    const release = forwardingGate.tryAcquire();
    if (!release) {
      return new NextResponse(null, {
        status: 503,
        headers: { "retry-after": "1" },
      });
    }
    try {
      const status = await forwardEnvelope({
        fetcher: fetch,
        envelope,
        envelopeUrl,
        contentType: request.headers.get("content-type") ?? "application/x-sentry-envelope",
        timeoutMs,
      });
      return new NextResponse(null, { status });
    } finally {
      release();
    }
  } catch (error) {
    if (error instanceof TunnelHttpError) {
      return new NextResponse(null, { status: error.status });
    }
    return new NextResponse(null, { status: 400 });
  }
}
