// Neumorphic guard for the account-billing migration. Fails if any owned file
// still carries neo-brutalist leftovers (shadow-brutal*, border-foreground,
// font-black, dark:*, legacy pixel-/glass/btn classes, #000/#fff offset shadows).
import { test } from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("account-billing screens use only neumorphic tokens", () => {
  assertNeumorphic([
    "app/(app)/account/page.tsx",
    "app/(app)/account/team/TeamClient.tsx",
    "app/(app)/account/team/page.tsx",
    "app/(app)/account/api-keys/ApiKeysClient.tsx",
    "app/(app)/account/api-keys/page.tsx",
    "app/(app)/account/api-reference/page.tsx",
    "app/(app)/billing/page.tsx",
    "app/(app)/billing/PaymentHistory.tsx",
    "components/editorial-learning-settings.tsx",
  ]);
});
