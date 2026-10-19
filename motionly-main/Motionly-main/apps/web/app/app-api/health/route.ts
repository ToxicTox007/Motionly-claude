import { NextResponse } from "next/server";

import { env } from "@/lib/env";

// Public endpoint: report reachability only, never private-network URLs.
export async function GET() {
  const apiUrl = `${env.apiBaseUrl.replace(/\/$/, "")}/health/ready`;
  try {
    const response = await fetch(apiUrl, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return NextResponse.json(
      {
        status: response.ok ? "ok" : "degraded",
        service: "web",
        api: {
          status: response.ok ? "ok" : "degraded",
          statusCode: response.status,
        },
      },
      { status: response.ok ? 200 : 503 },
    );
  } catch {
    return NextResponse.json(
      {
        status: "degraded",
        service: "web",
        api: { status: "unreachable" },
      },
      { status: 503 },
    );
  }
}
