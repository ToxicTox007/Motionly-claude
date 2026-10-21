// Dashboard task: every file owned by the dashboard migration is free of
// neo-brutalist leftover class tokens (hard offset shadows, dark: variants,
// font-black, legacy pixel-/card-/glass-/btn classes, border-foreground).
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("dashboard-owned files use only neumorphic class tokens", () => {
  assertNeumorphic([
    "components/DashboardClient.tsx",
    "components/upload-panel.tsx",
    "components/project-early-phase.tsx",
    "app/(app)/dashboard/page.tsx",
    "app/(app)/projects/page.tsx",
    "app/(app)/projects/new/page.tsx",
    "app/(app)/projects/new/new-project-client.tsx",
    "app/(app)/projects/[projectId]/page.tsx",
    "app/(app)/projects/[projectId]/error.tsx",
    "app/(app)/dashboard/loading.tsx",
    "app/(app)/projects/loading.tsx",
    "app/(app)/projects/[projectId]/loading.tsx",
    "app/(app)/billing/loading.tsx",
    "app/(app)/account/loading.tsx",
    "app/(app)/admin/ops/loading.tsx",
  ]);
});
