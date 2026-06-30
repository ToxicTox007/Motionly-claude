import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.SENTRY_DSN;
  if (!dsn) {
    return NextResponse.json({ status: "disabled" }, { status: 204 });
  }

  const parsed = new URL(dsn);
  const projectId = parsed.pathname.replace("/", "");
  const envelopeUrl = `${parsed.protocol}//${parsed.host}/api/${projectId}/envelope/`;

  const response = await fetch(envelopeUrl, {
    method: "POST",
    headers: { "content-type": "text/plain;charset=UTF-8" },
    body: await request.text(),
  });

  return NextResponse.json({ status: response.ok ? "ok" : "error" }, { status: 200 });
}
