// Task 19: role-based access in the browser. A workspace member can edit and
// render projects but cannot open billing — the billing nav is hidden and the
// billing page shows an "only the owner can manage billing" state instead of
// erroring.
//
// Setup: the `user` fixture is a confirmed account and, via handle_new_user,
// the OWNER of its own workspace. We create a SECOND confirmed user and add
// them to the owner's workspace as a `member` (support/workspace.mjs, which
// uses the service-role stack.admin REST on workspace_members), then sign in as
// that member and pin the shared workspace via the selection cookie so the app
// resolves them as a member there.
import { expect, signIn, test } from "./support/fixtures.mjs";
import { addWorkspaceUser, ownedWorkspaceId } from "./support/workspace.mjs";

// Mirrors WORKSPACE_COOKIE_NAME in apps/web/lib/api-auth.ts; inlined so this
// spec doesn't pull the next/headers-coupled module graph into the Playwright
// runner.
const WORKSPACE_COOKIE_NAME = "motionly_workspace_id";

test("a member can edit and render but cannot open billing", async ({
  page,
  context,
  user,
  stack,
  gates,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  const { user: member, cleanup } = await addWorkspaceUser(
    stack,
    workspaceId,
    "member",
  );

  try {
    await signIn(page, member, "/dashboard");

    // Act as a member of the owner's workspace, not the member's own.
    await context.addCookies([
      {
        name: WORKSPACE_COOKIE_NAME,
        value: workspaceId,
        url: page.url(),
      },
    ]);

    // The app shell hides the billing navigation for members.
    await page.goto("/dashboard");
    await expect(
      page.getByRole("link", { name: "Billing", exact: true }),
    ).toHaveCount(0);

    // Members can reach project editing and rendering.
    await page.goto("/projects/new");
    await expect(page.getByLabel("Project Name")).toBeVisible();

    // Visiting billing directly shows the owner-only state, not an error, and
    // offers no management controls (upgrade / cancel).
    await page.goto("/billing");
    await expect(
      page.getByText("Only the owner can manage billing"),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /^Upgrade$/i })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole("button", { name: /Cancel at period end/i }),
    ).toHaveCount(0);

    await gates.checkAccessibility(page);
  } finally {
    await cleanup();
  }
});
