const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
let sentryPromise: Promise<typeof import("@sentry/nextjs")> | null = null;

function getSentry() {
  if (!dsn) return null;
  sentryPromise ??= import("@sentry/nextjs").then((Sentry) => {
    Sentry.init({
      dsn,
      tracesSampleRate: 0.01,
      tunnel: "/api/glitchtip-tunnel",
    });
    return Sentry;
  });
  return sentryPromise;
}

void getSentry();

export function onRouterTransitionStart(...args: unknown[]) {
  void getSentry()?.then((Sentry) => {
    Sentry.captureRouterTransitionStart(
      ...(args as Parameters<typeof Sentry.captureRouterTransitionStart>),
    );
  });
}
