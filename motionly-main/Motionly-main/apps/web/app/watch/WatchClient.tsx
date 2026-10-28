"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { env } from "@/lib/env";
import { readTokenFromHash } from "@/components/editor/export-sharing.mjs";
import {
  PublicLinkError,
  clearUrlFragment,
  publicErrorMessage,
  recallShareToken,
  rememberShareToken,
} from "@/lib/share-links";

const INVALID_LINK = "This link is invalid, expired, or has been revoked.";
const WATCH_TOKEN_KEY = "motionly:watch-token";

type PublicExport = {
  title: string;
  version: number;
  width: number;
  height: number;
  fps: number;
  duration_ms: number | null;
  allow_download: boolean;
  media_url: string;
  media_expires_at: string;
  expires_at: string;
  download_url: string | null;
  download_filename: string | null;
};

export function WatchClient() {
  const [data, setData] = useState<PublicExport | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // The token lives in the fragment, read client-side only. It is kept
      // for this tab (so a reload works) and removed from the address bar.
      const hash = window.location.hash;
      const fromHash = readTokenFromHash(hash);
      clearUrlFragment();
      const token =
        fromHash ?? (hash ? null : recallShareToken(WATCH_TOKEN_KEY));
      if (!token) {
        setMessage("This link is missing its access token.");
        setLoading(false);
        return;
      }
      if (fromHash) rememberShareToken(WATCH_TOKEN_KEY, fromHash);
      try {
        const response = await fetch(
          `${env.apiBaseUrl}/public/exports/resolve`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ token }),
            cache: "no-store",
          },
        );
        if (!response.ok) {
          // All failures look identical, so show one neutral message.
          throw new PublicLinkError(INVALID_LINK);
        }
        const payload = (await response.json().catch(() => {
          throw new PublicLinkError(INVALID_LINK);
        })) as PublicExport;
        if (!cancelled) setData(payload);
      } catch (error) {
        if (!cancelled) setMessage(publicErrorMessage(error, INVALID_LINK));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <main className="mx-auto flex min-h-screen max-w-3xl items-center justify-center p-6">
        <Loader2 className="size-6 animate-spin" aria-hidden="true" />
        <span className="sr-only">Loading video…</span>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="mx-auto max-w-3xl p-6">
        <p
          role="alert"
          className="rounded-neu-md bg-surface p-4 text-sm font-semibold text-foreground shadow-neu-raised"
        >
          {message ?? INVALID_LINK}
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-4 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {data.title}
        </h1>
        <p className="font-mono text-xs text-muted-foreground">
          v{data.version} · {data.width}x{data.height} · {data.fps}fps
        </p>
      </header>

      <video
        ref={videoRef}
        src={data.media_url}
        controls
        playsInline
        tabIndex={0}
        aria-label={`${data.title} video player`}
        className="w-full rounded-neu-lg bg-ink shadow-neu-raised"
      />

      {data.allow_download && data.download_url ? (
        <a
          href={data.download_url}
          download={data.download_filename ?? undefined}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-neu bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
        >
          <Download className="size-4" aria-hidden="true" />
          Download
        </a>
      ) : (
        <p className="font-mono text-xs text-muted-foreground">
          Downloads are disabled for this link.
        </p>
      )}
    </main>
  );
}
