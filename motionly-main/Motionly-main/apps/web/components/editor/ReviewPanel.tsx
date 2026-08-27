import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Link2,
  MessageSquarePlus,
  Sparkles,
  ThumbsDown,
} from "lucide-react";
import { env } from "@/lib/env";
import { EditorApiError, editorApiRequest } from "@/lib/client-api";
import type { EditPlanRecordSummary } from "@motionly/shared";
import { Button } from "../button";
import { Input, Label, TextArea } from "./ui";

type ReviewStatus = "open" | "approved" | "rejected";

type ReviewComment = {
  id: string;
  body: string;
  timecode_ms: number | null;
  author_name: string | null;
  author_user_id: string | null;
  assignee_user_ids: string[];
  created_at: string;
};

type Review = {
  id: string;
  edit_plan_id: string;
  edit_plan_revision: number;
  uploaded_video_id: string;
  source_media_revision: number;
  source_object_reference_id: string;
  source_sha256: string;
  status: ReviewStatus;
  status_revision: number;
  decision_reason: string | null;
  comments?: ReviewComment[];
  created_at: string;
};

type ReviewListResponse = { reviews: Review[] };
type ReviewDetailResponse = Review & { comments: ReviewComment[] };
type FeedbackProposalResponse = {
  proposal: {
    proposal_id: string;
    proposal_revision: number;
    status_url: string | null;
  };
};

type PendingProposal = {
  proposalId: string;
  proposalRevision: number;
  statusUrl: string | null;
};

function timecode(ms: number | null) {
  if (ms == null) return "General";
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function parseAssignees(value: string) {
  return [
    ...new Set(
      value
        .split(/[\s,]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

export function ReviewPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  activePlan,
  mediaRevision,
  uploadedVideoId,
  sourceObjectReferenceId,
  sourceSha256,
  playheadMs,
  onSeek,
  onProposalCreated,
}: {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  activePlan: EditPlanRecordSummary | null;
  mediaRevision: number | null | undefined;
  uploadedVideoId: string;
  sourceObjectReferenceId: string | null | undefined;
  sourceSha256: string | null | undefined;
  playheadMs: number;
  onSeek: (timecodeMs: number) => void;
  onProposalCreated: (proposal: PendingProposal) => void;
}) {
  const [review, setReview] = useState<ReviewDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [comment, setComment] = useState("");
  const [assignees, setAssignees] = useState("");
  const [pending, setPending] = useState<
    "review" | "comment" | "feedback" | "share" | ReviewStatus | null
  >(null);
  const [message, setMessage] = useState<string | null>(null);

  const request = <T,>(
    path: string,
    options?: { method?: "GET" | "POST"; body?: Record<string, unknown> },
  ) =>
    editorApiRequest<T>(`${env.apiBaseUrl}${path}`, {
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      method: options?.method,
      body: options?.body,
      friendlyError: "The review could not be updated. Please try again.",
    });

  const loadReview = async (reviewId?: string) => {
    const path = reviewId
      ? `/projects/${projectId}/reviews/${reviewId}`
      : `/projects/${projectId}/reviews?limit=1`;
    const payload = await request<ReviewDetailResponse | ReviewListResponse>(
      path,
    );
    if ("reviews" in payload) {
      const latest = payload.reviews[0];
      if (!latest) {
        setReview(null);
        return;
      }
      const detail = await request<ReviewDetailResponse>(
        `/projects/${projectId}/reviews/${latest.id}`,
      );
      setReview(detail);
      return;
    }
    setReview(payload);
  };

  useEffect(() => {
    let canceled = false;
    void Promise.resolve()
      .then(() => loadReview())
      .catch((error) => {
        if (!canceled) setMessage((error as Error).message);
      })
      .finally(() => {
        if (!canceled) setLoading(false);
      });
    return () => {
      canceled = true;
    };
    // Reload only when the authenticated project context changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, workspaceId]);

  const pinnedToCurrentPlan = useMemo(
    () =>
      Boolean(
        review &&
        activePlan &&
        review.edit_plan_id === activePlan.id &&
        review.edit_plan_revision === activePlan.revision &&
        review.uploaded_video_id === uploadedVideoId &&
        review.source_media_revision === (mediaRevision ?? 1) &&
        review.source_object_reference_id === sourceObjectReferenceId &&
        review.source_sha256 === sourceSha256,
      ),
    [
      activePlan,
      mediaRevision,
      review,
      sourceObjectReferenceId,
      sourceSha256,
      uploadedVideoId,
    ],
  );

  async function startReview() {
    if (!activePlan || !sourceObjectReferenceId || !sourceSha256) {
      setMessage(
        "A verified source video and generated edit are required before client review.",
      );
      return;
    }
    setPending("review");
    setMessage(null);
    try {
      const created = await request<Review>(`/projects/${projectId}/reviews`, {
        method: "POST",
        body: {
          edit_plan_id: activePlan.id,
          expected_plan_revision: activePlan.revision,
          expected_media_revision: mediaRevision ?? 1,
          expected_uploaded_video_id: uploadedVideoId,
          expected_source_object_reference_id: sourceObjectReferenceId,
          expected_source_sha256: sourceSha256,
        },
      });
      await loadReview(created.id);
      setMessage(
        "Frozen review created. Comments are pinned to this edit revision.",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function addComment() {
    if (!review || !comment.trim()) return;
    setPending("comment");
    setMessage(null);
    try {
      const created = await request<ReviewComment>(
        `/projects/${projectId}/reviews/${review.id}/comments`,
        {
          method: "POST",
          body: {
            body: comment.trim(),
            timecode_ms: playheadMs,
            assignee_user_ids: parseAssignees(assignees),
          },
        },
      );
      setReview((current) =>
        current
          ? { ...current, comments: [...current.comments, created] }
          : current,
      );
      setComment("");
      setAssignees("");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function decide(decision: "approved" | "rejected") {
    if (!review) return;
    setPending(decision);
    setMessage(null);
    try {
      const updated = await request<Review>(
        `/projects/${projectId}/reviews/${review.id}/decision`,
        {
          method: "POST",
          body: {
            decision,
            expected_status_revision: review.status_revision,
            idempotency_key: `editor-review-${review.id}-${review.status_revision}-${decision}`,
          },
        },
      );
      setReview((current) => (current ? { ...current, ...updated } : current));
      setMessage(
        decision === "approved"
          ? "Client review approved."
          : "Client review rejected.",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function createShareLink() {
    if (!review) return;
    setPending("share");
    setMessage(null);
    try {
      const link = await request<{ url: string; expires_at: string }>(
        `/projects/${projectId}/reviews/${review.id}/share-links`,
        {
          method: "POST",
          body: { expires_in_hours: 168, can_comment: true, can_decide: false },
        },
      );
      const absoluteUrl = new URL(link.url, window.location.origin).toString();
      await navigator.clipboard?.writeText(absoluteUrl);
      setMessage(
        `Review link copied. It expires ${new Date(link.expires_at).toLocaleString()}.`,
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function suggestChanges() {
    if (!review || !review.comments.length || !pinnedToCurrentPlan) return;
    setPending("feedback");
    setMessage(null);
    try {
      const response = await request<FeedbackProposalResponse>(
        `/projects/${projectId}/reviews/${review.id}/feedback-suggestions`,
        { method: "POST", body: {} },
      );
      onProposalCreated({
        proposalId: response.proposal.proposal_id,
        proposalRevision: response.proposal.proposal_revision,
        statusUrl: response.proposal.status_url,
      });
      setMessage(
        "AI feedback proposal queued. Review and approve its actions before applying changes.",
      );
    } catch (error) {
      const detail = error as EditorApiError;
      setMessage(detail.message);
    } finally {
      setPending(null);
    }
  }

  if (loading) {
    return (
      <p className="p-3 text-xs font-bold text-muted-foreground">
        Loading client review…
      </p>
    );
  }

  return (
    <div className="space-y-4" aria-label="Client review">
      <div className="border border-border bg-primary/5 p-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-black uppercase tracking-wide">
              Client review
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Comments and sign-off are pinned to an immutable edit revision.
            </p>
          </div>
          {review ? (
            <span className="border border-border bg-surface px-2 py-1 text-[10px] font-black uppercase">
              {review.status}
            </span>
          ) : null}
        </div>
        {!review || !pinnedToCurrentPlan ? (
          <div className="mt-3 space-y-2">
            {review && !pinnedToCurrentPlan ? (
              <p className="text-xs font-semibold text-amber-700 dark:text-amber-300">
                The current edit changed after this review. Start a new review
                to collect feedback on it.
              </p>
            ) : null}
            <Button
              disabled={!activePlan || pending === "review"}
              onClick={() => void startReview()}
            >
              <MessageSquarePlus className="size-4" />
              {pending === "review"
                ? "Freezing review…"
                : "Start client review"}
            </Button>
          </div>
        ) : null}
      </div>

      {review ? (
        <>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              disabled={pending === "share"}
              onClick={() => void createShareLink()}
            >
              <Link2 className="size-4" />
              {pending === "share" ? "Creating…" : "Copy 7-day link"}
            </Button>
            <Button
              variant="secondary"
              disabled={
                !review.comments.length ||
                !pinnedToCurrentPlan ||
                pending === "feedback"
              }
              onClick={() => void suggestChanges()}
            >
              <Sparkles className="size-4" />
              {pending === "feedback" ? "Preparing…" : "Apply feedback with AI"}
            </Button>
          </div>

          {review.status === "open" ? (
            <div className="grid grid-cols-2 gap-2">
              <Button
                disabled={pending === "approved"}
                onClick={() => void decide("approved")}
              >
                <Check className="size-4" /> Approve
              </Button>
              <Button
                variant="danger"
                disabled={pending === "rejected"}
                onClick={() => void decide("rejected")}
              >
                <ThumbsDown className="size-4" /> Reject
              </Button>
            </div>
          ) : (
            <p className="border border-border bg-surface p-2 text-xs font-semibold">
              {review.status === "approved" ? "Approved" : "Rejected"} review —
              open a new review for further feedback.
            </p>
          )}

          <div className="space-y-2 border border-border bg-surface p-3">
            <Label htmlFor="review-comment">
              Comment at {timecode(playheadMs)}
            </Label>
            <TextArea
              id="review-comment"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Describe the change needed at this moment…"
              disabled={review.status !== "open"}
            />
            <Input
              value={assignees}
              onChange={(event) => setAssignees(event.target.value)}
              placeholder="Optional assignee user IDs, comma-separated"
              disabled={review.status !== "open"}
              aria-label="Optional feedback assignees"
            />
            <Button
              disabled={
                !comment.trim() ||
                review.status !== "open" ||
                pending === "comment"
              }
              onClick={() => void addComment()}
            >
              {pending === "comment" ? "Adding…" : "Add timestamped comment"}
            </Button>
          </div>

          <div className="space-y-2">
            <h4 className="text-xs font-black uppercase tracking-wide">
              Feedback ({review.comments.length})
            </h4>
            {review.comments.length ? (
              review.comments.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="w-full border border-border bg-surface p-3 text-left hover:bg-primary/5"
                  onClick={() =>
                    item.timecode_ms != null && onSeek(item.timecode_ms)
                  }
                >
                  <div className="flex items-center justify-between gap-2 text-[10px] font-black uppercase text-primary">
                    <span>{timecode(item.timecode_ms)}</span>
                    {item.assignee_user_ids.length ? (
                      <span>{item.assignee_user_ids.length} assigned</span>
                    ) : null}
                  </div>
                  <p className="mt-1 text-sm font-semibold text-foreground">
                    {item.body}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {item.author_name || "Workspace reviewer"} ·{" "}
                    {new Date(item.created_at).toLocaleString()}
                  </p>
                </button>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">No comments yet.</p>
            )}
          </div>
        </>
      ) : null}
      {message ? (
        <p className="border border-border bg-muted p-2 text-xs font-semibold">
          {message}
        </p>
      ) : null}
    </div>
  );
}
