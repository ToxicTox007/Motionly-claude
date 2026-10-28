import { NextResponse } from "next/server";

import { env } from "@/lib/env";

// Public endpoint: report reachability only, never private-network URLs.
// The upstream answer is reused for a few seconds, so a burst of public
// requests (probes, retries) costs at most one API readiness check per window.
const CACHE_MS = 5000;

type Snapshot = { ok: boolean; body: Record<string, unknown> };
let cached: { at: number; snapshot: Snapshot } | null = null;

async function checkApi(): Promise<Snapshot> {
  const apiUrl = `${env.apiBaseUrl.replace(/\/$/, "")}/health/ready`;
  try {
    const response = await fetch(apiUrl, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return {
      ok: response.ok,
      body: {
        status: response.ok ? "ok" : "degraded",
        service: "web",
        api: {
          status: response.ok ? "ok" : "degraded",
          statusCode: response.status,
        },
      },
    };
  } catch {
    return {
      ok: false,
      body: {
        status: "degraded",
        service: "web",
        api: { status: "unreachable" },
      },
    };
  }
}

export async function GET() {
  const now = Date.now();
  if (!cached || now - cached.at >= CACHE_MS) {
    cached = { at: now, snapshot: await checkApi() };
  }
  const { ok, body } = cached.snapshot;
  return NextResponse.json(body, {
    status: ok ? 200 : 503,
    headers: { "cache-control": "no-store" },
  });
}
