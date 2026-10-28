import { serverApiFetch } from "@/lib/server-api";
import { storageGauge } from "@/lib/storage-gauge.mjs";

export type DashboardProject = {
  id: string;
  title: string;
  updated_at: string;
  uploaded_videos: Array<{
    status: string;
    original_filename: string;
    duration_ms: number | null;
    metadata: { warnings?: string[] } | null;
  }>;
};

type ProjectsResponse = {
  projects?: Array<{
    id: string;
    title: string;
    updated_at: string;
    active_video_id?: string | null;
    latest_upload?: {
      id: string;
      status: string;
      original_filename: string;
      duration_ms?: number | null;
      warnings?: string[];
    } | null;
  }>;
  total?: number;
};

type QuotaResponse = {
  used_bytes?: number;
  max_bytes?: number;
  percentage?: number;
};

export type DashboardData = {
  projects: DashboardProject[];
  /** The projects request failed; the list is unknown, not empty. */
  loadError: boolean;
  storage: ReturnType<typeof storageGauge>;
};

export const DASHBOARD_PROJECT_LIMIT = 12;

/**
 * The dashboard's data in two parallel API calls. B29: GET /projects embeds
 * each project's latest upload, so there is no per-project /uploads request.
 * B23: the storage gauge shows the real quota; a failed quota degrades to
 * "not metered" rather than a fabricated bar.
 */
export async function loadDashboardData(
  headers: Record<string, string>,
): Promise<DashboardData> {
  const [projectsResult, quotaResult] = await Promise.all([
    serverApiFetch<ProjectsResponse>("/projects", { headers }),
    serverApiFetch<QuotaResponse>("/storage/quota", { headers }),
  ]);
  const storage = storageGauge(quotaResult.ok ? quotaResult.data : null);
  if (!projectsResult.ok || !Array.isArray(projectsResult.data?.projects)) {
    return { projects: [], loadError: true, storage };
  }
  const projects = projectsResult.data.projects
    .slice(0, DASHBOARD_PROJECT_LIMIT)
    .map((project) => {
      const upload = project.latest_upload ?? null;
      return {
        id: project.id,
        title: project.title,
        updated_at: project.updated_at,
        uploaded_videos: upload
          ? [
              {
                status: upload.status,
                original_filename: upload.original_filename,
                duration_ms: upload.duration_ms ?? null,
                metadata: { warnings: upload.warnings ?? [] },
              },
            ]
          : [],
      };
    });
  return { projects, loadError: false, storage };
}
