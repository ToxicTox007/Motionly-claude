"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, Send, ThumbsDown } from "lucide-react";
import { env } from "@/lib/env";
import {
  PublicLinkError,
  clearUrlFragment,
  publicErrorMessage,
  readFragmentToken,
  recallShareToken,
  rememberShareToken,
} from "@/lib/share-links";

const INVALID_LINK =
  "This review link is invalid, expired, or has been revoked.";
const MISSING_TOKEN = "This review link is missing its access token.";
const COMMENT_FAILED = "Your comment could not be added.";
const DECISION_FAILED = "The review decision could not be recorded.";

type Comment = {
  id: string;
  body: string;
  timecode_ms: number | null;
  author_name: string | null;
  created_at: string;
};

type PublicReview = {
  id: string;
  status: "open" | "approved" | "rejected";
  status_revision: number;
  decision_reason: string | null;
  comments: Comment[];
  can_comment: boolean;
  can_decide: boolean;
  expires_at: string;
  media_url: string | null;
};

function timecode(ms: number | null) {
  if (ms == null) return "General";
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function PublicReviewClient({ shareId }: { shareId: string }) {
  // The share id is a path segment of every API call: always encoded.
  const reviewPath = `${env.apiBaseUrl}/public/reviews/${encodeURIComponent(shareId)}`;
  const [token, setToken] = useState<string | null>(null);
  const [review, setReview] = useState<PublicReview | null>(null);
  const [name, setName] = useState("");
  const [comment, setComment] = useState("");
  const [playheadMs, setPlayheadMs] = useState<number | null>(null);
  const [attachTimestamp, setAttachTimestamp] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  function capturePlaybackTime() {
    const video = videoRef.current;
    if (!video) return;
    setPlayheadMs(Math.max(0, Math.round(video.currentTime * 1000)));
  }

  const resolve = async (secret: string) => {
    const response = await fetch(`${reviewPath}/resolve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: secret }),
      cache: "no-store",
    });
    if (!response.ok) throw new PublicLinkError(INVALID_LINK);
    const payload = (await response.json().catch(() => {
      throw new PublicLinkError(INVALID_LINK);
    })) as PublicReview;
    setReview(payload);
  };

  useEffect(() => {
    let cancelled = false;
    const storageKey = `motionly:review-token:${shareId}`;
    void Promise.resolve().then(async () => {
      // The token is read from the fragment once, kept for this tab so a
      // reload still works, and removed from the address bar and history.
      const fromHash = readFragmentToken(window.location.hash);
      clearUrlFragment();
      if (fromHash.status === "invalid") {
        setMessage(INVALID_LINK);
        return;
      }
      const secret =
        fromHash.status === "ok"
          ? fromHash.token
          : recallShareToken(storageKey);
      if (!secret) {
        setMessage(MISSING_TOKEN);
        return;
      }
      if (fromHash.status === "ok") rememberShareToken(storageKey, secret);
      setToken(secret);
      try {
        await resolve(secret);
      } catch (error) {
        if (!cancelled) setMessage(publicErrorMessage(error, INVALID_LINK));
      }
    });
    return () => {
      cancelled = true;
    };
    // The fragment is intentionally client-only and never leaves the browser in a request URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shareId]);

  async function submitComment() {
    if (!token || !comment.trim()) return;
    setPending(true);
    setMessage(null);
    try {
      const response = await fetch(`${reviewPath}/comments`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          token,
          body: comment.trim(),
          author_name: name || undefined,
          timecode_ms: attachTimestamp ? playheadMs : null,
        }),
      });
      if (!response.ok) throw new PublicLinkError(COMMENT_FAILED);
      setComment("");
      await resolve(token);
    } catch (error) {
      setMessage(publicErrorMessage(error, COMMENT_FAILED));
    } finally {
      setPending(false);
    }
  }

  async function decide(decision: "approved" | "rejected") {
    if (!token || !review) return;
    setPending(true);
    setMessage(null);
    try {
      const response = await fetch(`${reviewPath}/decision`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          token,
          decision,
          expected_status_revision: review.status_revision,
          idempotency_key: `public-review-${review.id}-${review.status_revision}-${decision}`,
          decider_name: name || undefined,
        }),
      });
      if (!response.ok) throw new PublicLinkError(DECISION_FAILED);
      await resolve(token);
    } catch (error) {
      setMessage(publicErrorMessage(error, DECISION_FAILED));
    } finally {
      setPending(false);
    }
  }

  if (!review && !message) {
    return (
      <p role="status" className="flex justify-center">
        <Loader2
          className="size-8 animate-spin text-primary"
          aria-hidden="true"
        />
        <span className="sr-only">Loading review…</span>
      </p>
    );
  }

  if (!review) {
    return (
      <p
        role="alert"
        className="rounded-neu-md bg-surface p-4 text-sm font-semibold text-foreground shadow-neu-raised"
      >
        {message}
      </p>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl space-y-5 p-4 sm:p-8">
      <header className="rounded-neu-lg bg-surface p-5 shadow-neu-raised">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold text-primary">
              Motionly client review
            </p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-foreground">
              Revision review
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              This link expires {new Date(review.expires_at).toLocaleString()}.
            </p>
          </div>
          <span className="inline-flex items-center rounded-full bg-muted px-3 py-1 text-xs font-semibold text-foreground shadow-neu-inset-sm">
            {review.status}
          </span>
        </div>
      </header>

      {review.media_url ? (
        <video
          ref={videoRef}
          className="w-full rounded-neu-lg bg-ink shadow-neu-raised"
          controls
          playsInline
          src={review.media_url}
          onLoadedMetadata={capturePlaybackTime}
          onTimeUpdate={capturePlaybackTime}
        />
      ) : (
        <p className="rounded-neu-md bg-surface p-4 text-sm font-medium text-muted-foreground shadow-neu-inset">
          The source preview is no longer available, but this review’s comments
          and decision remain visible.
        </p>
      )}

      {review.status === "open" && review.can_decide ? (
        <section className="flex flex-wrap gap-3 rounded-neu-lg bg-surface p-4 shadow-neu-raised">
          <button
            type="button"
            disabled={pending}
            onClick={() => void decide("approved")}
            className="inline-flex min-h-11 items-center gap-2 rounded-neu bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
          >
            <Check className="size-4" /> Approve revision
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => void decide("rejected")}
            className="inline-flex min-h-11 items-center gap-2 rounded-neu bg-danger px-4 py-2 text-sm font-semibold text-danger-foreground shadow-neu-raised transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
          >
            <ThumbsDown className="size-4" /> Request changes
          </button>
        </section>
      ) : null}

      <section className="space-y-3 rounded-neu-lg bg-surface p-4 shadow-neu-raised">
        <h2 className="text-sm font-semibold text-foreground">Feedback</h2>
        {review.comments.length ? (
          review.comments.map((item) => (
            <article
              key={item.id}
              className="rounded-neu-md bg-surface p-3 shadow-neu-raised-sm"
            >
              <p className="text-xs font-semibold text-primary">
                {timecode(item.timecode_ms)}
              </p>
              <p className="mt-1 text-sm font-medium text-foreground">
                {item.body}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {item.author_name || "Reviewer"} ·{" "}
                {new Date(item.created_at).toLocaleString()}
              </p>
            </article>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            No feedback has been added yet.
          </p>
        )}
      </section>

      {review.status === "open" && review.can_comment ? (
        <section className="space-y-3 rounded-neu-lg bg-surface p-4 shadow-neu-raised">
          <h2 className="text-sm font-semibold text-foreground">
            Add feedback
          </h2>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Your name (optional)"
            className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Describe a change or leave approval feedback…"
            className="min-h-28 w-full rounded-neu border border-control bg-surface p-3 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
          {review.media_url ? (
            <div className="flex flex-wrap items-center gap-3 text-xs font-semibold">
              <label className="inline-flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={attachTimestamp}
                  onChange={(event) => setAttachTimestamp(event.target.checked)}
                />
                Attach playback time
              </label>
              <button
                type="button"
                onClick={capturePlaybackTime}
                className="rounded-neu bg-surface px-3 py-1.5 font-semibold text-foreground shadow-neu-raised-sm transition-all duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
              >
                Use current time
              </button>
              <span className="text-muted-foreground">
                {attachTimestamp
                  ? `Timestamp: ${timecode(playheadMs)}`
                  : "General feedback"}
              </span>
            </div>
          ) : null}
          <button
            type="button"
            disabled={pending || !comment.trim()}
            onClick={() => void submitComment()}
            className="inline-flex min-h-11 items-center gap-2 rounded-neu bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-60 disabled:shadow-neu-raised-sm"
          >
            <Send className="size-4" /> Send feedback
          </button>
        </section>
      ) : null}
      {message ? (
        <p
          role="alert"
          className="rounded-neu-md bg-surface p-3 text-sm font-medium text-foreground shadow-neu-inset"
        >
          {message}
        </p>
      ) : null}
    </main>
  );
}
