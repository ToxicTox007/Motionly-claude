export function createNonce(): string;
export function buildContentSecurityPolicy(options: {
  nonce: string;
  publicOrigin: string;
  development: boolean;
}): string;
