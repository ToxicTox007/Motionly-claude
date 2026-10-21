// The auth-public agent's migrated page and component files contain no
// neo-brutalist legacy tokens. (global-error.tsx is self-contained with inline
// styles and no className tokens, so it passes the same guard.)
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("auth-public files are free of neo-brutalist legacy tokens", () => {
  assertNeumorphic([
    "app/(auth)/login/page.tsx",
    "app/(auth)/signup/page.tsx",
    "app/(auth)/forgot-password/page.tsx",
    "app/(auth)/reset-password/page.tsx",
    "app/auth/confirm/page.tsx",
    "app/invite/InviteClient.tsx",
    "app/invite/page.tsx",
    "app/pricing/page.tsx",
    "components/legal-page.tsx",
    "app/privacy/page.tsx",
    "app/terms/page.tsx",
    "app/refund/page.tsx",
    "app/copyright/page.tsx",
    "app/feedback/page.tsx",
    "app/watch/WatchClient.tsx",
    "app/watch/page.tsx",
    "app/review/[shareId]/PublicReviewClient.tsx",
    "app/review/[shareId]/page.tsx",
    "app/global-error.tsx",
    "app/not-found.tsx",
  ]);
});
