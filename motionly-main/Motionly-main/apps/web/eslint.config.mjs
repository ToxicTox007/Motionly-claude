import tsParser from "@typescript-eslint/parser";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// Next's Core Web Vitals and TypeScript presets (FE-32). `pnpm lint` runs with
// --max-warnings 0, so a warning (a missing hook dependency, an unused
// variable) fails the build as surely as an error.
const unusedVars = [
  "error",
  {
    argsIgnorePattern: "^_",
    varsIgnorePattern: "^_",
    caughtErrorsIgnorePattern: "^_",
    destructuredArrayIgnorePattern: "^_",
    ignoreRestSiblings: true,
  },
];

const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "**/*.d.mts"],
  },
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": unusedVars,
    },
  },
  {
    files: ["**/*.{ts,tsx,mts}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: "./tsconfig.json",
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    // Playwright fixtures receive a `use` callback; it is not React's use().
    files: ["tests/browser/**/*.mjs", "tests/e2e/**/*.mjs"],
    rules: {
      "react-hooks/rules-of-hooks": "off",
    },
  },
];

export default eslintConfig;
