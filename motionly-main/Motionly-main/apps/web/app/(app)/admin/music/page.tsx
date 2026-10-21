import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

type CatalogTrack = {
  provider_track_id: string;
  title: string;
  license: string;
  license_version: string | null;
  rights_status: string;
  composition_rights_status: string | null;
  recording_rights_status: string | null;
  rights_holder: string | null;
  verified_by: string | null;
  verified_at: string | null;
  source_page_url: string | null;
  duration_ms: number | null;
  bpm: number | null;
  energy: number | null;
  mood_tags: string[];
  is_test_track: boolean;
  preview_url: string | null;
};

type CatalogResponse = {
  tracks: CatalogTrack[];
  allow_list_approved: boolean;
};

async function fetchCatalog(
  headers: Record<string, string>,
): Promise<CatalogResponse> {
  const response = await fetch(`${env.apiBaseUrl}/admin/music/tracks`, {
    cache: "no-store",
    headers,
  });
  if (!response.ok) return { tracks: [], allow_list_approved: false };
  return (await response.json()) as CatalogResponse;
}

function formatDuration(ms: number | null): string {
  if (!ms || ms <= 0) return "—";
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function bothSidesCleared(track: CatalogTrack): boolean {
  return (
    track.composition_rights_status === "cleared" &&
    track.recording_rights_status === "cleared"
  );
}

export default async function AdminMusicPage() {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_access_denied");

  const { tracks, allow_list_approved } = await fetchCatalog(context.headers);

  return (
    <AppShell title="Music catalog">
      {!allow_list_approved && (
        <Alert tone="warning" className="mb-5">
          The music licence allow-list is not marked approved in
          music-licenses.json (decision D3). The importer still enforces it; no
          real tracks have been imported.
        </Alert>
      )}

      <p className="mb-6 max-w-2xl text-sm font-medium text-muted-foreground leading-relaxed">
        Every track here carries a complete, verified rights record. The
        importer refuses any track without a source page, an archived-licence
        hash, a licence on the allow-list, and separately cleared composition
        and recording rights. Previews use short-lived signed links from the
        private catalog bucket.
      </p>

      {tracks.length === 0 ? (
        <p className="text-sm font-medium text-muted-foreground">
          No catalog tracks yet.
        </p>
      ) : (
        <div className="overflow-hidden rounded-neu-lg bg-surface shadow-neu-raised">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Licensed music catalog with rights status and previews
            </caption>
            <thead>
              <tr className="border-b border-hairline text-xs font-semibold text-muted-foreground">
                <th scope="col" className="px-4 py-3">
                  Title
                </th>
                <th scope="col" className="px-4 py-3">
                  Licence
                </th>
                <th scope="col" className="px-4 py-3">
                  Rights
                </th>
                <th scope="col" className="px-4 py-3">
                  Duration
                </th>
                <th scope="col" className="px-4 py-3">
                  Preview
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {tracks.map((track) => (
                <tr key={track.provider_track_id} className="align-top">
                  <th
                    scope="row"
                    className="px-4 py-3 font-semibold text-foreground"
                  >
                    {track.title}
                    {track.is_test_track && (
                      <span className="ml-2 inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold text-muted-foreground shadow-neu-inset-sm">
                        Test track
                      </span>
                    )}
                    {track.rights_holder && (
                      <span className="block font-mono text-xs font-normal text-muted-foreground">
                        {track.rights_holder}
                      </span>
                    )}
                    {track.mood_tags.length > 0 && (
                      <span className="block text-xs font-normal text-muted-foreground">
                        {track.mood_tags.join(", ")}
                      </span>
                    )}
                  </th>
                  <td className="px-4 py-3">
                    {track.source_page_url ? (
                      <a
                        href={track.source_page_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary underline"
                      >
                        {track.license}
                        {track.license_version
                          ? ` ${track.license_version}`
                          : ""}
                      </a>
                    ) : (
                      <>
                        {track.license}
                        {track.license_version
                          ? ` ${track.license_version}`
                          : ""}
                      </>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={
                        bothSidesCleared(track)
                          ? "font-semibold text-success"
                          : "font-semibold text-danger"
                      }
                    >
                      {bothSidesCleared(track) ? "Cleared" : "Incomplete"}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      composition:{" "}
                      {track.composition_rights_status ?? "unknown"}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      recording: {track.recording_rights_status ?? "unknown"}
                    </span>
                    {track.verified_by && (
                      <span className="block font-mono text-xs text-muted-foreground">
                        verified by {track.verified_by}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono tabular-nums">
                    {formatDuration(track.duration_ms)}
                    {track.bpm ? (
                      <span className="block text-xs text-muted-foreground">
                        {track.bpm} BPM
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    {track.preview_url ? (
                      <audio
                        controls
                        preload="none"
                        src={track.preview_url}
                        aria-label={`Preview ${track.title}`}
                      >
                        Your browser does not support audio previews.
                      </audio>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        No preview
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
