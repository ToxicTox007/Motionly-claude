import { NextRequest, NextResponse } from "next/server";

import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const title = String(formData.get("title") ?? "Untitled reel").trim() || "Untitled reel";

  const context = await getWorkspaceApiContext();
  if (!context) {
    return NextResponse.redirect(new URL("/login?next=/projects/new", request.url));
  }

  const response = await fetch(`${env.apiBaseUrl}/projects`, {
    method: "POST",
    headers: context.headers,
    body: JSON.stringify({ title }),
    cache: "no-store",
  });
  const project = await response.json();

  if (!response.ok || !project.id) {
    return NextResponse.redirect(
      new URL(
        `/projects/new?error=${encodeURIComponent(project?.error?.code ?? "create_failed")}`,
        request.url,
      ),
    );
  }

  return NextResponse.redirect(new URL(`/projects/${project.id}`, request.url));
}
