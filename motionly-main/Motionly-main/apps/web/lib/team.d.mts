export const INVITE_TOKEN_STORAGE_KEY: string;

export type SeatUsage = {
  used: number;
  limit: number;
  remaining: number;
  atLimit: boolean;
  label: string;
};

export type UpgradePrompt = {
  canUpgrade: boolean;
  message: string;
  actionLabel: string | null;
  actionHref: string | null;
};

export function inviteTokenFromHash(hash: unknown): string | null;

export function seatUsage(input: {
  activeMembers?: number;
  pendingInvites?: number;
  limit?: number;
}): SeatUsage;

export function canInvite(
  role: unknown,
  usage: SeatUsage | null | undefined,
): boolean;

export function upgradePrompt(role: unknown): UpgradePrompt;

export function invitationIsPending(
  invitation:
    | {
        revoked_at?: string | null;
        accepted_at?: string | null;
        accepted_by?: string | null;
        expires_at?: string | null;
      }
    | null
    | undefined,
): boolean;
