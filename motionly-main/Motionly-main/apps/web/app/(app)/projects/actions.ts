"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

export async function createProject(formData: FormData) {
  const title =
    String(formData.get("title") ?? "Untitled reel").trim() || "Untitled reel";

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
    redirect(
      `/projects/new?error=${encodeURIComponent(project?.error?.code ?? "create_failed")}`,
    );
  }

  redirect(`/projects/${project.id}`);
}

export async function updateProject(formData: FormData) {
  const projectId = String(formData.get("projectId") ?? "");
  const titleValue = formData.get("title");
  const statusValue = formData.get("status");
  const title = typeof titleValue === "string" ? titleValue.trim() : "";
  const status = typeof statusValue === "string" ? statusValue : "";

  if (!projectId) redirect("/projects?error=missing_project");

  const payload: { title?: string; status?: string } = {};
  if (title) payload.title = title;
  if (["draft", "ready", "failed"].includes(status)) payload.status = status;

  const context = await getWorkspaceApiContext();
  if (!context) redirect(`/login?next=${encodeURIComponent("/projects")}`);

  const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}`, {
    method: "PATCH",
    headers: context.headers,
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const project = await response.json();

  if (!response.ok || !project.id) {
    redirect(
      `/projects?error=${encodeURIComponent(project?.error?.code ?? "update_failed")}`,
    );
  }

  revalidatePath("/projects");
  revalidatePath("/dashboard");
  revalidatePath(`/projects/${projectId}`);
  redirect("/projects?updated=1");
}

export async function deleteProject(formData: FormData) {
  const projectId = String(formData.get("projectId") ?? "");
  if (!projectId) redirect("/projects?error=missing_project");

  const context = await getWorkspaceApiContext();
  if (!context) redirect(`/login?next=${encodeURIComponent("/projects")}`);

  const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}`, {
    method: "DELETE",
    headers: context.headers,
    cache: "no-store",
  });
  const payload = await response.json();

  if (!response.ok || !payload.deleted) {
    redirect(
      `/projects?error=${encodeURIComponent(payload?.error?.code ?? "delete_failed")}`,
    );
  }

  revalidatePath("/projects");
  revalidatePath("/dashboard");
  redirect("/projects?deleted=1");
}
