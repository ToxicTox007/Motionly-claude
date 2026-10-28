import { vi } from "vitest";

// Shared stand-ins for next/navigation and next/headers. A test opts in with
//   vi.mock("next/navigation", () => navigationMock);
//   vi.mock("next/headers", () => headersMock);
// and reads or resets the spies below. redirect() and notFound() throw a
// tagged error, as Next does, so code after them never runs.

export class RedirectError extends Error {
  constructor(public readonly location: string) {
    super(`NEXT_REDIRECT:${location}`);
    this.name = "RedirectError";
  }
}

export class NotFoundError extends Error {
  constructor() {
    super("NEXT_NOT_FOUND");
    this.name = "NotFoundError";
  }
}

export const router = {
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  prefetch: vi.fn(),
};

export const navigationState = {
  pathname: "/dashboard",
  searchParams: new URLSearchParams(),
};

export const navigationMock = {
  useRouter: () => router,
  usePathname: () => navigationState.pathname,
  useSearchParams: () => navigationState.searchParams,
  redirect: vi.fn((location: string) => {
    throw new RedirectError(location);
  }),
  notFound: vi.fn(() => {
    throw new NotFoundError();
  }),
  unstable_rethrow: vi.fn((error: unknown) => {
    if (error instanceof RedirectError || error instanceof NotFoundError) {
      throw error;
    }
  }),
};

type CookieEntry = { name: string; value: string };

export const cookieJar = new Map<string, string>();
export const requestHeaders = new Headers();

export const cookieStore = {
  get: vi.fn((name: string): CookieEntry | undefined =>
    cookieJar.has(name) ? { name, value: cookieJar.get(name)! } : undefined,
  ),
  getAll: vi.fn((): CookieEntry[] =>
    [...cookieJar.entries()].map(([name, value]) => ({ name, value })),
  ),
  set: vi.fn((name: string, value: string) => {
    cookieJar.set(name, value);
  }),
  delete: vi.fn((name: string) => {
    cookieJar.delete(name);
  }),
};

export const headersMock = {
  cookies: vi.fn(async () => cookieStore),
  headers: vi.fn(async () => requestHeaders),
};

/** Resets every shared spy and store between tests. */
export function resetNextMocks() {
  for (const spy of Object.values(router)) spy.mockClear();
  navigationMock.redirect.mockClear();
  navigationMock.notFound.mockClear();
  navigationMock.unstable_rethrow.mockClear();
  cookieJar.clear();
  for (const key of [...requestHeaders.keys()]) requestHeaders.delete(key);
  navigationState.pathname = "/dashboard";
  navigationState.searchParams = new URLSearchParams();
}
