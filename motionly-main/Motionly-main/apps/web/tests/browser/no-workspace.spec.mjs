// An account that is in no active workspace (FE-01): it sees how to get back
// in, never a redirect loop between /dashboard and the workspace check. The
// state is created deterministically: the account's only membership is
// disabled through PostgREST with the service role.
import { expect, signIn, test } from "./support/fixtures.mjs";

const NO_WORKSPACE = "You are not in a workspace";

async function disableEveryMembership(stack, userId) {
  const response = await stack.admin.patch(
    `/supabase/rest/v1/workspace_members?user_id=eq.${userId}`,
    {
      headers: { prefer: "return=representation" },
      data: { status: "disabled" },
    },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const rows = await response.json();
  expect(rows.length, "the account had a membership to disable").toBe(1);
}

test("the dashboard explains a missing workspace and stays put", async ({
  page,
  user,
  stack,
  gates,
}) => {
  await disableEveryMembership(stack, user.id);
  const navigations = [];
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) navigations.push(frame.url());
  });

  await signIn(page, user);

  await expect(page.getByRole("heading", { name: NO_WORKSPACE })).toBeVisible();
  await expect(page.getByText(/ask a workspace owner/i)).toBeVisible();
  // Settled: no further navigation, and no workspace error was needed.
  const settled = navigations.length;
  await page.waitForTimeout(2_000);
  expect(navigations.slice(settled)).toEqual([]);
  expect(new URL(page.url()).pathname).toBe("/dashboard");
  expect(new URL(page.url()).searchParams.get("error")).toBeNull();
  await gates.checkAccessibility(page);
});

test("a workspace page leads to the same explanation, not a loop", async ({
  page,
  user,
  stack,
}) => {
  await disableEveryMembership(stack, user.id);
  await signIn(page, user);

  await page.goto("/projects");
  await expect(page).toHaveURL((url) => url.pathname === "/dashboard");
  await expect(page.getByRole("heading", { name: NO_WORKSPACE })).toBeVisible();
});

test("signing out from the no-workspace state ends the session", async ({
  page,
  user,
  stack,
}) => {
  await disableEveryMembership(stack, user.id);
  await signIn(page, user);

  await page
    .getByRole("main")
    .getByRole("button", { name: "Sign out", exact: true })
    .click();
  await expect(page).toHaveURL((url) => url.pathname === "/login");
  await page.goto("/dashboard");
  await expect(page).toHaveURL((url) => url.pathname === "/login");
});
