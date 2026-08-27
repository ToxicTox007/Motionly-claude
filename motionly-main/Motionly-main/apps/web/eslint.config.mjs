import tsParser from "@typescript-eslint/parser";
import nextConfig from "eslint-config-next";

const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  ...nextConfig,
  {
    files: ["**/*.{ts,tsx,mts}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: "./tsconfig.json",
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "no-unused-vars": "off",
    },
  },
];

export default eslintConfig;
