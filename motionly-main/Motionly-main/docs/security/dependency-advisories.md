# Dependency advisories

`pnpm audit` runs in CI (`.github/workflows/phase-0-safety.yml`). Advisories that
have a fixed release are resolved by exact pins in `pnpm.overrides` in the root
`package.json`; remove a pin once the dependency that pulls the package in ships
the fixed version itself.

## Known advisories without a fixed release

| Package             | Advisory                                                                            | Pulled in by                                      | Exposure                                                                                                                                                                           |
| ------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extract-zip@2.0.1` | GHSA-jmr9-qjv8-65gv, GHSA-7pqw-9j4j-h8q3 (symlink entries write outside the target) | `@remotion/renderer` (`BrowserFetcher`)           | Only used to unpack a Chrome download. The worker images set `BROWSER_EXECUTABLE_PATH` to the system Chromium, so it never runs in production and never sees an untrusted archive. |
| `braces@3.0.3`      | GHSA-vfj7-8cjw-p6xm (stack exhaustion on deeply nested patterns)                    | `eslint-config-next` → `fast-glob` → `micromatch` | Development only (lint); patterns come from the repository's own config. Not in any runtime image.                                                                                 |

No release of either package fixes these; there is nothing to pin. Re-check when
`@remotion/renderer` or `eslint-config-next` is upgraded, and do not let the
renderer download a browser at runtime.

The CI gate runs `pnpm audit --prod --audit-level low`, which reports the two
`extract-zip` advisories until upstream ships a fix.
