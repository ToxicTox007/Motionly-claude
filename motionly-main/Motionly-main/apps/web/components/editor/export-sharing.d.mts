export type ShareLinkExpiryOption = {
  value: 1 | 7 | 30;
  label: string;
};

export type ShareLinkLike = {
  allow_download?: boolean;
  expires_at: string;
  revoked_at?: string | null;
} | null;

export const SHARE_LINK_EXPIRY_OPTIONS: readonly ShareLinkExpiryOption[];

export function buildWatchUrl(origin: string, token: string): string;

export function readTokenFromHash(hash: string): string | null;

export function shareStrategy(nav: unknown): "native" | "copy";

export function isShareLinkInactive(link: ShareLinkLike, now?: Date): boolean;

export function shareLinkStatusLabel(link: ShareLinkLike, now?: Date): string;
