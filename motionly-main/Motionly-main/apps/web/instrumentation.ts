// Fail fast on a misconfigured server (FE-42). The image runs the standalone
// server (next.config.ts `output: "standalone"`), which does not evaluate
// next.config.ts when it starts, so the runtime configuration is checked
// here: Next.js calls register() once as the server starts, before it serves
// a request. A container without valid configuration exits non-zero with the
// variable's name instead of failing requests. The check is Node.js only and
// imported only there, so the Edge bundle never sees process.exit.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { exitOnInvalidConfiguration } = await import("./lib/startup-config");
    exitOnInvalidConfiguration();
  }
}
