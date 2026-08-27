"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, Send, ThumbsDown } from "lucide-react";
import { env } from "@/lib/env";

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
    const response = await fetch(
      `${env.apiBaseUrl}/public/reviews/${shareId}/resolve`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: secret }),
        cache: "no-store",
      },
    );
    if (!response.ok)
      throw new Error(
        "This review link is invalid, expired, or has been revoked.",
      );
    const payload = (await response.json()) as PublicReview;
    setReview(payload);
  };

  useEffect(() => {
    void Promise.resolve().then(async () => {
      const secret = decodeURIComponent(window.location.hash.slice(1));
      if (!secret) {
        setMessage("This review link is missing its access token.");
        return;
      }
      setToken(secret);
      try {
        await resolve(secret);
      } catch (error) {
        setMessage((error as Error).message);
      }
    });
    // The fragment is intentionally client-only and never leaves the browser in a request URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shareId]);

  async function submitComment() {
    if (!token || !comment.trim()) return;
    setPending(true);
    setMessage(null);
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/public/reviews/${shareId}/comments`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            token,
            body: comment.trim(),
            author_name: name || undefined,
            timecode_ms: attachTimestamp ? playheadMs : null,
          }),
        },
      );
      if (!response.ok) throw new Error("Your comment could not be added.");
      setComment("");
      await resolve(token);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function decide(decision: "approved" | "rejected") {
    if (!token || !review) return;
    setPending(true);
    setMessage(null);
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/public/reviews/${shareId}/decision`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            token,
            decision,
            expected_status_revision: review.status_revision,
            idempotency_key: `public-review-${review.id}-${review.status_revision}-${decision}`,
            decider_name: name || undefined,
          }),
        },
      );
      if (!response.ok)
        throw new Error("The review decision could not be recorded.");
      await resolve(token);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (!review && !message) {
    return (
      <Loader2
        className="size-8 animate-spin text-primary"
        aria-label="Loading review"
      />
    );
  }

  if (!review) {
    return (
      <p className="border border-border bg-surface p-4 text-sm font-semibold">
        {message}
      </p>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl space-y-5 p-4 sm:p-8">
      <header className="border-2 border-foreground bg-surface p-5 shadow-brutal">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-black uppercase tracking-widest text-primary">
              Motionly client review
            </p>
            <h1 className="mt-1 text-2xl font-black">Revision review</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              This link expires {new Date(review.expires_at).toLocaleString()}.
            </p>
          </div>
          <span className="border-2 border-foreground bg-muted px-3 py-1 text-xs font-black uppercase">
            {review.status}
          </span>
        </div>
      </header>

      {review.media_url ? (
        <video
          ref={videoRef}
          className="w-full border-2 border-foreground bg-black"
          controls
          playsInline
          src={review.media_url}
          onLoadedMetadata={capturePlaybackTime}
          onTimeUpdate={capturePlaybackTime}
        />
      ) : (
        <p className="border border-border bg-muted p-3 text-sm text-muted-foreground">
          The source preview is no longer available, but this review’s comments
          and decision remain visible.
        </p>
      )}

      {review.status === "open" && review.can_decide ? (
        <section className="flex flex-wrap gap-2 border-2 border-foreground bg-surface p-4">
          <button
            type="button"
            disabled={pending}
            onClick={() => void decide("approved")}
            className="inline-flex min-h-10 items-center gap-2 border-2 border-foreground bg-primary px-4 py-2 text-sm font-black text-primary-foreground"
          >
            <Check className="size-4" /> Approve revision
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => void decide("rejected")}
            className="inline-flex min-h-10 items-center gap-2 border-2 border-foreground bg-danger px-4 py-2 text-sm font-black text-danger-foreground"
          >
            <ThumbsDown className="size-4" /> Request changes
          </button>
        </section>
      ) : null}

      <section className="space-y-3 border-2 border-foreground bg-surface p-4 shadow-brutal">
        <h2 className="text-sm font-black uppercase tracking-wide">Feedback</h2>
        {review.comments.length ? (
          review.comments.map((item) => (
            <article
              key={item.id}
              className="border border-border bg-muted/40 p-3"
            >
              <p className="text-[10px] font-black uppercase text-primary">
                {timecode(item.timecode_ms)}
              </p>
              <p className="mt-1 text-sm font-semibold">{item.body}</p>
              <p className="mt-1 text-[10px] text-muted-foreground">
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
        <section className="space-y-3 border-2 border-foreground bg-surface p-4 shadow-brutal">
          <h2 className="text-sm font-black uppercase tracking-wide">
            Add feedback
          </h2>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Your name (optional)"
            className="min-h-10 w-full border-2 border-foreground bg-surface-elevated px-3 text-sm font-semibold"
          />
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Describe a change or leave approval feedback…"
            className="min-h-28 w-full border-2 border-foreground bg-surface-elevated p-3 text-sm font-semibold"
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
                className="border border-foreground px-2 py-1 font-black"
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
            className="inline-flex min-h-10 items-center gap-2 border-2 border-foreground bg-primary px-4 py-2 text-sm font-black text-primary-foreground disabled:opacity-60"
          >
            <Send className="size-4" /> Send feedback
          </button>
        </section>
      ) : null}
      {message ? (
        <p className="border border-border bg-muted p-3 text-sm font-semibold">
          {message}
        </p>
      ) : null}
    </main>
  );
}
