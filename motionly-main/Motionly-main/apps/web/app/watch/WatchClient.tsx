"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { env } from "@/lib/env";
import { readTokenFromHash } from "@/components/editor/export-sharing.mjs";

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
      // The token lives in the fragment, read client-side only.
      const token = readTokenFromHash(window.location.hash);
      if (!token) {
        setMessage("This link is missing its access token.");
        setLoading(false);
        return;
      }
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
          throw new Error(
            "This link is invalid, expired, or has been revoked.",
          );
        }
        const payload = (await response.json()) as PublicExport;
        if (!cancelled) setData(payload);
      } catch (error) {
        if (!cancelled) setMessage((error as Error).message);
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
          {message ?? "This link is invalid, expired, or has been revoked."}
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
