export function writeSessionCookies(
  store: { set(name: string, value: string, options?: object): unknown },
  cookiesToSet: Array<{ name: string; value: string; options?: object }>,
): void;
