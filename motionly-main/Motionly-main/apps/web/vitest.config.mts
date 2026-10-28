import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component and unit tests that exercise real behaviour (rendered DOM, hooks,
// timers) rather than source text. The node --test suite in tests/*.test.mjs
// stays as it is; both run under `pnpm test` and the `unit` verify stage.
const junitOutput = process.env.VITEST_JUNIT;

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    include: [
      "tests/components/**/*.test.tsx",
      "tests/unit/**/*.test.ts",
      "tests/unit/**/*.test.tsx",
    ],
    setupFiles: ["./tests/setup/vitest.setup.ts"],
    restoreMocks: true,
    reporters: junitOutput ? ["default", "junit"] : ["default"],
    outputFile: junitOutput ? { junit: junitOutput } : undefined,
  },
});
