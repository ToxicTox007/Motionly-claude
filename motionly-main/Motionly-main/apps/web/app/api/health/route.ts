import { NextResponse } from "next/server";

import { env } from "@/lib/env";

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
          url: apiUrl,
          statusCode: response.status,
        },
      },
      { status: response.ok ? 200 : 503 },
    );
  } catch (error) {
    return NextResponse.json(
      {
        status: "degraded",
        service: "web",
        api: {
          status: "unreachable",
          url: apiUrl,
          message: error instanceof Error ? error.message : "API health check failed.",
        },
      },
      { status: 503 },
    );
  }
}
