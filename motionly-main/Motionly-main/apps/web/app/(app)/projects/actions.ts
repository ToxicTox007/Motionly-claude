"use server";

import { redirect } from "next/navigation";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

export async function createProject(formData: FormData) {
  const title = String(formData.get("title") ?? "Untitled reel").trim() || "Untitled reel";

  const context = await getWorkspaceApiContext();
  if (!context) {
    redirect(`/login?next=${encodeURIComponent("/projects/new")}`);
  }

  const response = await fetch(`${env.apiBaseUrl}/projects`, {
    method: "POST",
    headers: context.headers,
    body: JSON.stringify({ title }),
    cache: "no-store",
  });
  const project = await response.json();

  if (!response.ok || !project.id) {
    redirect(`/projects/new?error=${encodeURIComponent(project?.error?.code ?? "create_failed")}`);
  }

  redirect(`/projects/${project.id}`);
}
