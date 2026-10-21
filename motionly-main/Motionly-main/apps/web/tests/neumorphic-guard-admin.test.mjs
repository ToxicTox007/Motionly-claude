// The admin screens (ops, users, refunds, music catalog, editorial operations)
// are built from the neumorphic design system only: no neo-brutalist leftovers
// (hard offset shadows, dark borders, font-black, uppercase labels, dark:
// variants, legacy pixel-/card/badge/btn classes).
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("admin screen files are free of neo-brutalist legacy tokens", () => {
  assertNeumorphic([
    "app/(app)/admin/ops/page.tsx",
    "app/(app)/admin/ops/AdminJobsList.tsx",
    "app/(app)/admin/users/page.tsx",
    "app/(app)/admin/users/RefundPanel.tsx",
    "app/(app)/admin/users/CopyLink.tsx",
    "app/(app)/admin/music/page.tsx",
    "app/(app)/admin/editorial-learning/page.tsx",
  ]);
});
