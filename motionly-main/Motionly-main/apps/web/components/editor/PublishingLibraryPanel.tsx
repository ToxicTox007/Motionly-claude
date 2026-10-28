"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Download,
  Eye,
  FolderPlus,
  Heart,
  Languages,
  Loader2,
  RefreshCw,
  Repeat2,
} from "lucide-react";
import { Button } from "../button";
import { Input, Select } from "./ui";
import { apiFetch, editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";

const languages = [
  ["en", "English"],
  ["hi", "Hindi"],
  ["es", "Spanish"],
  ["pt", "Portuguese"],
  ["fr", "French"],
  ["de", "German"],
  ["ar", "Arabic"],
] as const;

type Package = {
  id: string;
  language: string;
  review_status: "needs_review" | "approved";
  caption_track_id: string;
  overlay_translations: { source_element_id: string; content: string }[];
  title_suggestions: string[];
  description_suggestions: string[];
  selected_title?: string | null;
  selected_description?: string | null;
};
type Asset = {
  id: string;
  kind: string;
  name: string;
  description: string;
  is_favorite: boolean;
  source_project_id?: string | null;
};
type Collection = { id: string; name: string; asset_ids: string[] };

export function PublishingLibraryPanel({
  projectId,
  workspaceId,
  captionTrackId,
  activePlanId,
}: {
  projectId: string;
  workspaceId: string;
  captionTrackId: string | null;
  activePlanId: string | null;
}) {
  const auth = useMemo(() => ({ workspaceId }), [workspaceId]);
  const [language, setLanguage] = useState("es");
  const [packages, setPackages] = useState<Package[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [query, setQuery] = useState("");
  const [collectionName, setCollectionName] = useState("");
  const [targetProjectId, setTargetProjectId] = useState(projectId);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [previewAsset, setPreviewAsset] = useState<{
    id: string;
    name: string;
    kind: string;
    url: string;
  } | null>(null);

  const load = useCallback(async () => {
    const [localized, library, collectionResponse] = await Promise.all([
      editorApiRequest<{ packages: Package[] }>(
        `${env.apiBaseUrl}/projects/${projectId}/localized-publish-packages`,
        {
          ...auth,
          friendlyError: "Localized publish packages could not be loaded.",
        },
      ),
      editorApiRequest<{ items: Asset[] }>(
        `${env.apiBaseUrl}/asset-library/items${query.trim() ? `?query=${encodeURIComponent(query.trim())}` : ""}`,
        {
          ...auth,
          friendlyError: "Workspace asset library could not be loaded.",
        },
      ),
      editorApiRequest<{ collections: Collection[] }>(
        `${env.apiBaseUrl}/asset-library/collections`,
        { ...auth, friendlyError: "Asset collections could not be loaded." },
      ),
    ]);
    setPackages(localized.packages ?? []);
    setAssets(library.items ?? []);
    setCollections(collectionResponse.collections ?? []);
  }, [auth, projectId, query]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void load().catch((error) => {
        if (!cancelled) setMessage((error as Error).message);
      });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [load]);

  async function preparePackage() {
    if (!captionTrackId) {
      setMessage("Generate captions before preparing another language.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await editorApiRequest<Package>(
        `${env.apiBaseUrl}/projects/${projectId}/localized-publish-packages`,
        {
          ...auth,
          method: "POST",
          friendlyError: "The localized publish package could not be prepared.",
          body: {
            language,
            source_caption_track_id: captionTrackId,
            edit_plan_id: activePlanId,
          },
        },
      );
      setPackages((current) => [
        response,
        ...current.filter((item) => item.id !== response.id),
      ]);
      setMessage(
        "Translation package prepared. Review text and approve before rendering.",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function approvePackage(item: Package) {
    setBusy(true);
    setMessage(null);
    try {
      const response = await editorApiRequest<Package>(
        `${env.apiBaseUrl}/projects/${projectId}/localized-publish-packages/${item.id}/review`,
        {
          ...auth,
          method: "PATCH",
          friendlyError: "The localized package could not be approved.",
          body: {
            review_status: "approved",
            selected_title:
              item.selected_title ?? item.title_suggestions[0] ?? null,
            selected_description:
              item.selected_description ??
              item.description_suggestions[0] ??
              null,
          },
        },
      );
      setPackages((current) =>
        current.map((value) => (value.id === response.id ? response : value)),
      );
      setMessage("Localized package approved and ready to render.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function downloadSubtitles(item: Package, format: "srt" | "vtt") {
    setBusy(true);
    setMessage(null);
    try {
      const response = await apiFetch(
        `${env.apiBaseUrl}/projects/${projectId}/localized-publish-packages/${item.id}/subtitles?format=${format}`,
        {},
        auth,
      );
      if (!response.ok)
        throw new Error("Subtitle export could not be downloaded.");
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `subtitles-${item.language}.${format}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function indexAssets() {
    setBusy(true);
    setMessage(null);
    try {
      await editorApiRequest(
        `${env.apiBaseUrl}/projects/${projectId}/asset-library/index`,
        {
          ...auth,
          method: "POST",
          body: {},
          friendlyError: "Project assets could not be indexed.",
        },
      );
      await load();
      setMessage(
        "Project B-roll, music, source clips, exports, and style were added to the workspace library.",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function setFavorite(item: Asset) {
    try {
      const response = await editorApiRequest<Asset>(
        `${env.apiBaseUrl}/asset-library/items/${item.id}/favorite`,
        {
          ...auth,
          method: "PATCH",
          body: { is_favorite: !item.is_favorite },
          friendlyError: "Favorite could not be updated.",
        },
      );
      setAssets((current) =>
        current.map((value) => (value.id === response.id ? response : value)),
      );
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function reuseAsset(item: Asset) {
    if (!targetProjectId.trim()) {
      setMessage("Enter the target project ID first.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await editorApiRequest<{ message: string }>(
        `${env.apiBaseUrl}/asset-library/items/${item.id}/reuse/projects/${targetProjectId.trim()}`,
        {
          ...auth,
          method: "POST",
          body: {},
          friendlyError: "Asset could not be reused in that project.",
        },
      );
      setMessage(response.message);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function createCollection() {
    if (!collectionName.trim()) return;
    try {
      const response = await editorApiRequest<Collection>(
        `${env.apiBaseUrl}/asset-library/collections`,
        {
          ...auth,
          method: "POST",
          body: { name: collectionName.trim() },
          friendlyError: "Collection could not be created.",
        },
      );
      setCollections((current) => [response, ...current]);
      setCollectionName("");
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function addToFirstCollection(item: Asset) {
    const collection = collections[0];
    if (!collection) {
      setMessage("Create a collection first.");
      return;
    }
    try {
      const response = await editorApiRequest<Collection>(
        `${env.apiBaseUrl}/asset-library/collections/${collection.id}/items`,
        {
          ...auth,
          method: "POST",
          body: { asset_id: item.id },
          friendlyError: "Asset could not be added to the collection.",
        },
      );
      setCollections((current) =>
        current.map((value) => (value.id === response.id ? response : value)),
      );
      setMessage(`Added to ${response.name}.`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function assetMediaUrl(item: Asset): Promise<string> {
    const response = await editorApiRequest<{ url: string }>(
      `${env.apiBaseUrl}/asset-library/items/${item.id}/media-url`,
      {
        ...auth,
        friendlyError: "A media link for this asset is not available.",
      },
    );
    return response.url;
  }

  async function previewAssetItem(item: Asset) {
    setBusy(true);
    setMessage(null);
    try {
      const url = await assetMediaUrl(item);
      setPreviewAsset({ id: item.id, name: item.name, kind: item.kind, url });
    } catch (error) {
      setPreviewAsset(null);
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function downloadAssetItem(item: Asset) {
    setBusy(true);
    setMessage(null);
    try {
      const url = await assetMediaUrl(item);
      const response = await apiFetch(url, {}, auth);
      if (!response.ok) throw new Error("The asset could not be downloaded.");
      const objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = item.name;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
      setMessage(`Downloaded ${item.name}.`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4" aria-live="polite">
      <section className="rounded-neu-lg bg-surface p-4 shadow-neu-raised">
        <div className="mb-3 flex items-center gap-2">
          <Languages className="size-4 text-primary" aria-hidden="true" />
          <h3 className="text-sm font-bold text-foreground">Translations</h3>
        </div>
        <div className="flex gap-2">
          <Select
            label="Language"
            value={language}
            onChange={setLanguage}
            options={languages.map(([value, label]) => ({ value, label }))}
          />
          <Button
            onClick={() => void preparePackage()}
            disabled={busy || !captionTrackId}
          >
            <Loader2 className={busy ? "size-4 animate-spin" : "hidden"} />
            Prepare
          </Button>
        </div>
        <p className="mt-2 text-xxs font-medium text-muted-foreground">
          Each language gets independent captions, translated text overlays,
          metadata suggestions, and a required review step. Voice dubbing is
          intentionally not included.
        </p>
        <div className="mt-3 space-y-2">
          {packages.length ? (
            packages.map((item) => (
              <div
                key={item.id}
                className="rounded-neu-md bg-surface p-2 text-xs shadow-neu-raised-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <strong className="uppercase text-foreground">
                    {item.language}
                  </strong>
                  <span className="text-muted-foreground">
                    {item.review_status === "approved"
                      ? "Approved"
                      : "Needs review"}
                  </span>
                </div>
                <p className="mt-1 line-clamp-1 text-foreground">
                  {item.title_suggestions[0] ?? "No title suggestion"}
                </p>
                <p className="line-clamp-2 text-muted-foreground">
                  {item.description_suggestions[0] ??
                    "No description suggestion"}
                </p>
                <p className="mt-1 text-2xs text-muted-foreground">
                  {item.overlay_translations.length} localized overlay
                  {item.overlay_translations.length === 1 ? "" : "s"}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {item.review_status !== "approved" ? (
                    <Button
                      size="sm"
                      onClick={() => void approvePackage(item)}
                      disabled={busy}
                    >
                      Approve
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void downloadSubtitles(item, "srt")}
                    disabled={busy}
                  >
                    <Download className="size-3" />
                    SRT
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void downloadSubtitles(item, "vtt")}
                    disabled={busy}
                  >
                    VTT
                  </Button>
                </div>
              </div>
            ))
          ) : (
            <p className="text-xs font-medium text-muted-foreground">
              Prepare a language package to translate captions, editable
              overlays, and title and description suggestions.
            </p>
          )}
        </div>
      </section>
      <section className="rounded-neu-lg bg-surface p-4 shadow-neu-raised">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-bold text-foreground">
              Workspace asset library
            </h3>
            <p className="text-xxs font-medium text-muted-foreground">
              Search past exports and clips; favorite, collect, and reuse assets
              across projects.
            </p>
          </div>
          <Button size="sm" onClick={() => void indexAssets()} disabled={busy}>
            <RefreshCw className="size-3" />
            Index project
          </Button>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search exports, clips, music…"
            aria-label="Search workspace assets"
          />
          <Input
            value={targetProjectId}
            onChange={(event) => setTargetProjectId(event.target.value)}
            placeholder="Target project ID"
            aria-label="Target project ID"
          />
        </div>
        <div className="mt-2 flex gap-2">
          <Input
            value={collectionName}
            onChange={(event) => setCollectionName(event.target.value)}
            placeholder="New collection"
            aria-label="New asset collection"
          />
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void createCollection()}
          >
            <FolderPlus className="size-3" />
            Create
          </Button>
        </div>
        {collections.length ? (
          <p className="mt-2 text-2xs font-medium text-muted-foreground">
            Newest collection: {collections[0].name} (
            {collections[0].asset_ids.length} assets). Add library cards to it.
          </p>
        ) : null}
        <div className="mt-3 space-y-2">
          {assets.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-2 rounded-neu-md bg-surface p-2 text-xs shadow-neu-raised-sm"
            >
              <div className="min-w-0 flex-1">
                <strong className="truncate text-foreground">
                  {item.name}
                </strong>
                <p className="truncate text-2xs text-muted-foreground">
                  {item.kind} · {item.description}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void setFavorite(item)}
                aria-label={`${item.is_favorite ? "Remove favorite" : "Favorite"} ${item.name}`}
                className="p-1"
              >
                <Heart
                  className={`size-4 ${item.is_favorite ? "fill-current text-danger" : "text-muted-foreground"}`}
                />
              </button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => void addToFirstCollection(item)}
              >
                Collect
              </Button>
              {item.kind !== "clip" ? (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    aria-label={`Preview ${item.name}`}
                    onClick={() => void previewAssetItem(item)}
                    disabled={busy}
                  >
                    <Eye className="size-3" />
                    Preview
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    aria-label={`Download ${item.name}`}
                    onClick={() => void downloadAssetItem(item)}
                    disabled={busy}
                  >
                    <Download className="size-3" />
                    Download
                  </Button>
                </>
              ) : null}
              <Button
                size="sm"
                onClick={() => void reuseAsset(item)}
                disabled={busy || item.kind === "clip"}
              >
                <Repeat2 className="size-3" />
                Reuse
              </Button>
            </div>
          ))}
          {previewAsset ? (
            <div
              role="dialog"
              aria-label={`Preview of ${previewAsset.name}`}
              className="rounded-neu-md bg-surface p-3 shadow-neu-raised"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <strong className="truncate text-xs text-foreground">
                  {previewAsset.name}
                </strong>
                <button
                  type="button"
                  onClick={() => setPreviewAsset(null)}
                  aria-label="Close preview"
                  className="text-xs font-semibold text-primary underline"
                >
                  Close
                </button>
              </div>
              {previewAsset.kind === "music" ? (
                <audio
                  controls
                  src={previewAsset.url}
                  aria-label={`Audio preview for ${previewAsset.name}`}
                  className="w-full"
                >
                  <track kind="captions" />
                </audio>
              ) : (
                <video
                  controls
                  src={previewAsset.url}
                  aria-label={`Video preview for ${previewAsset.name}`}
                  className="w-full rounded-neu bg-ink shadow-neu-inset"
                >
                  <track kind="captions" />
                </video>
              )}
            </div>
          ) : null}
          {!assets.length ? (
            <p className="text-xs font-medium text-muted-foreground">
              Index a project to add its B-roll, music, uploads, exports, clips,
              and style recipe.
            </p>
          ) : null}
        </div>
      </section>
      {message ? (
        <p
          role="status"
          className="rounded-neu bg-surface p-2 text-xs font-semibold text-foreground shadow-neu-inset-sm"
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}
