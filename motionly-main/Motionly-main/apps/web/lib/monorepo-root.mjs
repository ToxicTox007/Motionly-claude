// The monorepo root (this file is apps/web/lib/monorepo-root.mjs). Next.js
// traces the standalone server's files from here (next.config.ts), so the
// workspace package @motionly/shared and the hoisted node_modules are part of
// the image. Kept in an ES module: next.config.ts may be loaded as CommonJS,
// where import.meta is unavailable.
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const MONOREPO_ROOT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
);
