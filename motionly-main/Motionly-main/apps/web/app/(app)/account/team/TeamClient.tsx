"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Check,
  Copy,
  Crown,
  LogOut,
  Trash2,
  UserMinus,
  UserPlus,
} from "lucide-react";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { Button } from "@/components/button";
import {
  canInvite,
  invitationIsPending,
  seatUsage,
  upgradePrompt,
} from "@/lib/team.mjs";
import {
  changeMemberRole,
  createInvitation,
  leaveWorkspace,
  removeMember,
  revokeInvitation,
  transferOwnership,
  type Invitation,
  type InvitableRole,
} from "./actions";
import type { TeamMember } from "./page";

type ViewerRole = "owner" | "admin" | "member";

function roleLabel(role: string) {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleDateString() : "—";
}

// A newly-minted invite link, shown exactly once with a copy button. The link
// (which carries the raw token in its fragment) lives only in this component's
// state; reloading the page loses it, matching the API's one-time contract.
function RevealedInvite({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div
      className="rounded-neu-lg bg-surface p-4 shadow-neu-raised"
      role="status"
      aria-live="polite"
    >
      <p className="text-sm font-semibold text-primary">
        Copy this invite link now
      </p>
      <p className="mt-1 text-xs font-medium text-muted-foreground">
        This is the only time the link is shown. Send it to the teammate you
        want to add — anyone with the link can join this workspace until it is
        used or revoked.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <code
          className="flex-1 overflow-x-auto rounded-neu bg-surface px-3 py-2 font-mono text-xs text-foreground shadow-neu-inset"
          data-testid="invite-link"
        >
          {url}
        </code>
        <Button
          type="button"
          size="sm"
          onClick={copy}
          aria-label="Copy invite link"
        >
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

// A focus-trapped confirmation dialog for destructive actions (remove member,
// revoke invite). Escape and the Cancel button both close it.
function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onClose,
  pending,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
  pending: boolean;
}) {
  const ref = useFocusTrap(true, onClose);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 p-4 backdrop-blur-sm"
      role="presentation"
    >
      <div
        ref={ref}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        className="w-full max-w-md rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
      >
        <h2 id="confirm-title" className="text-lg font-bold text-foreground">
          {title}
        </h2>
        <p
          id="confirm-body"
          className="mt-2 text-sm font-medium text-muted-foreground"
        >
          {body}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onClose}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            size="sm"
            onClick={onConfirm}
            disabled={pending}
          >
            {pending ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function TeamClient({
  canManage,
  viewerRole,
  members,
  invitations,
  seatUsed,
  seatLimit,
}: {
  canManage: boolean;
  viewerRole: ViewerRole;
  members: TeamMember[];
  invitations: Invitation[];
  seatUsed: number;
  /** null when the plan (and so the seat limit) could not be loaded. */
  seatLimit: number | null;
}) {
  const [memberList, setMemberList] = useState(members);
  const router = useRouter();
  const [inviteList, setInviteList] = useState(
    invitations.filter(invitationIsPending),
  );
  const [role, setRole] = useState<InvitableRole>("member");
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [revealedUrl, setRevealedUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{
    title: string;
    body: string;
    confirmLabel: string;
    run: () => Promise<{ ok: true } | { ok: false; error: string }>;
    done: () => void;
    success: string;
  } | null>(null);
  const [pending, startTransition] = useTransition();

  // An unknown limit never enables invites (the API enforces seats either way).
  const usage =
    seatLimit === null
      ? null
      : seatUsage({
          activeMembers: memberList.length,
          pendingInvites: inviteList.length,
          limit: seatLimit,
        });
  const inviteEnabled = canInvite(viewerRole, usage);
  const prompt = upgradePrompt(viewerRole);

  const report = (message: string) => {
    setError(null);
    setStatus(message);
  };
  const fail = (code: string) => {
    setStatus(null);
    setError(code.replaceAll("_", " "));
  };

  const onCreate = (event: React.FormEvent) => {
    event.preventDefault();
    setStatus(null);
    setError(null);
    setRevealedUrl(null);
    startTransition(async () => {
      const result = await createInvitation(role, expiresInDays);
      if (!result.ok) {
        fail(result.error);
        return;
      }
      setRevealedUrl(result.url);
      setInviteList((current) => [result.invitation, ...current]);
      report("Invite link created. Copy it now — it is shown only once.");
    });
  };

  const onRevoke = (invite: Invitation) => {
    setConfirm({
      title: "Revoke invite",
      body: "This invite link will stop working immediately. Anyone who hasn't used it yet will no longer be able to join.",
      confirmLabel: "Revoke invite",
      success: "Invite revoked.",
      run: () => revokeInvitation(invite.id),
      done: () =>
        setInviteList((current) => current.filter((i) => i.id !== invite.id)),
    });
  };

  const onRemove = (member: TeamMember) => {
    setConfirm({
      title: `Remove ${member.name}`,
      body: "They will lose access to this workspace right away. You can invite them again later.",
      confirmLabel: "Remove member",
      success: `${member.name} was removed.`,
      run: () => removeMember(member.user_id),
      done: () =>
        setMemberList((current) =>
          current.filter((m) => m.user_id !== member.user_id),
        ),
    });
  };

  const onChangeRole = (member: TeamMember, nextRole: InvitableRole) => {
    setStatus(null);
    setError(null);
    startTransition(async () => {
      const result = await changeMemberRole(member.user_id, nextRole);
      if (!result.ok) {
        fail(result.error);
        return;
      }
      setMemberList((current) =>
        current.map((m) =>
          m.user_id === member.user_id ? { ...m, role: nextRole } : m,
        ),
      );
      report(
        `${member.name} is now ${nextRole === "admin" ? "an" : "a"} ${nextRole}.`,
      );
    });
  };

  const runConfirm = () => {
    if (!confirm) return;
    const current = confirm;
    startTransition(async () => {
      const result = await current.run();
      if (!result.ok) {
        fail(result.error);
        setConfirm(null);
        return;
      }
      current.done();
      report(current.success);
      setConfirm(null);
    });
  };

  // Any member can leave the workspace. The API refuses the last owner; we show
  // its message verbatim so the user learns they must transfer ownership first.
  const onLeave = () => {
    setConfirm({
      title: "Leave workspace",
      body: "You will lose access to this workspace and its projects right away. You can be invited back later.",
      confirmLabel: "Leave workspace",
      success: "You have left the workspace.",
      run: async () => {
        const result = await leaveWorkspace();
        if (result.ok) return { ok: true as const };
        // Prefer the API's human message (e.g. the last-owner refusal).
        return { ok: false as const, error: result.message ?? result.error };
      },
      done: () => router.push("/dashboard"),
    });
  };

  // Owner-only: transfer ownership to another member. The dialog names the new
  // owner; on success the current owner becomes an admin.
  const onTransfer = (member: TeamMember) => {
    setConfirm({
      title: "Transfer ownership",
      body: `${member.name} will become the workspace owner, and you will become an admin. This cannot be undone from here — only the new owner can transfer it back.`,
      confirmLabel: `Make ${member.name} owner`,
      success: `${member.name} is now the workspace owner.`,
      run: () => transferOwnership(member.user_id),
      done: () => router.refresh(),
    });
  };

  return (
    <div className="space-y-5">
      {/* Announced status + error region. */}
      <div aria-live="polite" role="status">
        {status ? (
          <p className="rounded-neu-md bg-surface px-4 py-3 text-sm font-semibold text-foreground shadow-neu-raised-sm">
            {status}
          </p>
        ) : null}
      </div>
      {error ? (
        <p
          role="alert"
          className="rounded-neu-md bg-surface px-4 py-3 text-sm font-semibold text-danger shadow-neu-raised-sm ring-1 ring-danger/30"
        >
          {error}
        </p>
      ) : null}

      {/* Seat usage. */}
      <section
        aria-labelledby="seats-heading"
        className="rounded-neu-lg bg-surface p-5 shadow-neu-raised"
      >
        <h2 id="seats-heading" className="text-lg font-bold text-foreground">
          Seats
        </h2>
        <p
          className="mt-1 text-sm font-medium text-muted-foreground"
          data-testid="seat-usage"
        >
          {usage
            ? `${usage.used} of ${usage.limit} seats in use (members plus pending invites).`
            : "Seat usage is unavailable right now because the workspace plan could not be loaded. Invites are paused until it can; refresh to try again."}
        </p>
        {usage?.atLimit && canManage ? (
          <div className="mt-3 rounded-neu-md bg-surface p-3 shadow-neu-inset-sm">
            <p className="text-sm font-semibold text-foreground">
              {prompt.message}
            </p>
            {prompt.canUpgrade && prompt.actionHref ? (
              <Link
                href={prompt.actionHref}
                className="mt-2 inline-flex font-semibold text-primary underline"
              >
                {prompt.actionLabel}
              </Link>
            ) : null}
          </div>
        ) : null}
      </section>

      {/* Invite a teammate (owner/admin only). */}
      {canManage ? (
        <form
          onSubmit={onCreate}
          className="rounded-neu-lg bg-surface p-5 shadow-neu-raised"
        >
          <h2 className="text-lg font-bold text-foreground">
            Invite a teammate
          </h2>
          <p className="mt-1 text-xs font-medium text-muted-foreground">
            Create a one-time invite link, then send it to the person you want
            to add.
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <div>
              <label
                htmlFor="invite-role"
                className="block text-xs font-semibold text-muted-foreground"
              >
                Role
              </label>
              <select
                id="invite-role"
                value={role}
                onChange={(event) =>
                  setRole(event.target.value as InvitableRole)
                }
                className="mt-1 min-h-11 rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="invite-expiry"
                className="block text-xs font-semibold text-muted-foreground"
              >
                Expires in (days)
              </label>
              <input
                id="invite-expiry"
                type="number"
                min={1}
                max={30}
                value={expiresInDays}
                onChange={(event) =>
                  setExpiresInDays(Number(event.target.value))
                }
                className="mt-1 min-h-11 w-28 rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium tabular-nums text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
              />
            </div>
            <Button type="submit" disabled={pending || !inviteEnabled}>
              <UserPlus className="size-4" aria-hidden="true" />
              {pending ? "Creating…" : "Create invite link"}
            </Button>
          </div>
          {!inviteEnabled && usage?.atLimit ? (
            <p className="mt-2 text-xs font-semibold text-muted-foreground">
              No seats left — free one up or upgrade to invite more teammates.
            </p>
          ) : null}
        </form>
      ) : null}

      {revealedUrl ? <RevealedInvite url={revealedUrl} /> : null}

      {/* Pending invites (owner/admin only). */}
      {canManage ? (
        <section
          aria-labelledby="invites-heading"
          className="rounded-neu-lg bg-surface p-5 shadow-neu-raised"
        >
          <h2
            id="invites-heading"
            className="text-lg font-bold text-foreground"
          >
            Pending invites
          </h2>
          {inviteList.length === 0 ? (
            <p className="mt-3 text-sm font-medium text-muted-foreground">
              No pending invites.
            </p>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Pending workspace invites</caption>
                <thead>
                  <tr className="text-xs font-semibold text-muted-foreground">
                    <th scope="col" className="py-2 pr-4">
                      Role
                    </th>
                    <th scope="col" className="py-2 pr-4">
                      Expires
                    </th>
                    <th scope="col" className="py-2">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {inviteList.map((invite) => (
                    <tr key={invite.id}>
                      <td className="py-3 pr-4 font-semibold text-foreground">
                        {roleLabel(invite.role)}
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">
                        {formatDate(invite.expires_at)}
                      </td>
                      <td className="py-3">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => onRevoke(invite)}
                          disabled={pending}
                          aria-label={`Revoke ${invite.role} invite`}
                        >
                          <Trash2 className="size-4" aria-hidden="true" />
                          Revoke
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

      {/* Members. */}
      <section
        aria-labelledby="members-heading"
        className="rounded-neu-lg bg-surface p-5 shadow-neu-raised"
      >
        <h2 id="members-heading" className="text-lg font-bold text-foreground">
          Members
        </h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Workspace members</caption>
            <thead>
              <tr className="text-xs font-semibold text-muted-foreground">
                <th scope="col" className="py-2 pr-4">
                  Name
                </th>
                <th scope="col" className="py-2 pr-4">
                  Role
                </th>
                {canManage ? (
                  <th scope="col" className="py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {memberList.map((member) => {
                // Owners can't be demoted or removed from here (the API also
                // protects the last owner); the viewer can't remove themselves.
                const manageable =
                  canManage && member.role !== "owner" && !member.isSelf;
                return (
                  <tr key={member.user_id}>
                    <td className="py-3 pr-4">
                      <span className="flex items-center gap-3">
                        <span
                          aria-hidden="true"
                          className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground shadow-neu-raised-sm"
                        >
                          {(member.name || "T").charAt(0).toUpperCase()}
                        </span>
                        <span className="font-semibold text-foreground">
                          {member.name}
                          {member.isSelf ? (
                            <span className="ml-2 text-xs font-medium text-muted-foreground">
                              (you)
                            </span>
                          ) : null}
                        </span>
                      </span>
                    </td>
                    <td className="py-3 pr-4">
                      {manageable ? (
                        <>
                          <label
                            htmlFor={`role-${member.user_id}`}
                            className="sr-only"
                          >
                            Role for {member.name}
                          </label>
                          <select
                            id={`role-${member.user_id}`}
                            value={member.role}
                            disabled={pending}
                            onChange={(event) =>
                              onChangeRole(
                                member,
                                event.target.value as InvitableRole,
                              )
                            }
                            className="min-h-9 rounded-neu border border-control bg-surface px-3 text-xs font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
                          >
                            <option value="member">Member</option>
                            <option value="admin">Admin</option>
                          </select>
                        </>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold text-foreground shadow-neu-raised-sm">
                          {roleLabel(member.role)}
                        </span>
                      )}
                    </td>
                    {canManage ? (
                      <td className="py-3">
                        {manageable ? (
                          <div className="flex flex-wrap items-center gap-2">
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => onRemove(member)}
                              disabled={pending}
                              aria-label={`Remove ${member.name}`}
                            >
                              <UserMinus
                                className="size-4"
                                aria-hidden="true"
                              />
                              Remove
                            </Button>
                            {viewerRole === "owner" ? (
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => onTransfer(member)}
                                disabled={pending}
                                aria-label={`Transfer ownership to ${member.name}`}
                              >
                                <Crown className="size-4" aria-hidden="true" />
                                Make owner
                              </Button>
                            ) : null}
                          </div>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Leave workspace (any member, self-service). The last owner is refused
          by the API; its message is shown in the error region above. */}
      <section
        aria-labelledby="leave-heading"
        className="rounded-neu-lg bg-surface p-5 shadow-neu-raised ring-1 ring-danger/30"
      >
        <h2 id="leave-heading" className="text-lg font-bold text-danger">
          Leave workspace
        </h2>
        <p className="mt-1 text-sm font-medium text-muted-foreground">
          Give up your access to this workspace. If you are the only owner, make
          another member the owner first.
        </p>
        <Button
          type="button"
          variant="danger"
          size="sm"
          onClick={onLeave}
          disabled={pending}
          className="mt-3"
        >
          <LogOut className="size-4" aria-hidden="true" />
          Leave workspace
        </Button>
      </section>

      {confirm ? (
        <ConfirmDialog
          title={confirm.title}
          body={confirm.body}
          confirmLabel={confirm.confirmLabel}
          onConfirm={runConfirm}
          onClose={() => setConfirm(null)}
          pending={pending}
        />
      ) : null}
    </div>
  );
}
