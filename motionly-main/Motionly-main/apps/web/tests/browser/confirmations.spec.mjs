// Irreversible actions ask first, and backing out changes nothing (FE-12).
// Each test confirms only after proving that Escape or Cancel left the data
// as it was, reloaded from the server.
import { randomBytes } from "node:crypto";
import { expect, signIn, test } from "./support/fixtures.mjs";
import {
  grantAgencyPlan,
  ownedWorkspaceId,
  setSubscription,
} from "./support/workspace.mjs";

test("deleting a project asks first; Escape and Cancel keep it", async ({
  page,
  user,
  gates,
}) => {
  const title = `Keep me ${randomBytes(3).toString("hex")}`;
  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill(title);
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);

  await page.goto("/projects");
  const card = page
    .getByRole("article")
    .filter({ has: page.locator(`input[value="${title}"]`) });
  const trigger = card.getByRole("button", { name: `Delete ${title}` });
  const dialog = page.getByRole("dialog", { name: "Delete project?" });

  await trigger.click();
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(title);
  await expect(dialog).toContainText(/cannot be undone/i);
  await gates.checkAccessibility(page);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  await trigger.click();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();

  await page.reload();
  await expect(card).toBeVisible();

  await trigger.click();
  await dialog
    .getByRole("button", { name: "Delete project", exact: true })
    .click();
  await expect(card).toHaveCount(0);
  await page.reload();
  await expect(card).toHaveCount(0);
});

test("revoking an API key asks first; Cancel keeps the key working", async ({
  page,
  request,
  user,
  stack,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await grantAgencyPlan(stack, workspaceId);
  const name = `confirm-${randomBytes(3).toString("hex")}`;

  await signIn(page, user, "/account/api-keys");
  await page.getByLabel(/Create API key/i).fill(name);
  await page.getByRole("button", { name: /Create key/i }).click();
  const apiKey = (
    await page.getByTestId("api-key-secret").textContent()
  )?.trim();
  expect(apiKey?.startsWith("mtly_")).toBeTruthy();

  const row = page.getByRole("row").filter({ hasText: name });
  await row.getByRole("button", { name: `Revoke API key ${name}` }).click();
  const dialog = page.getByRole("dialog", { name: "Revoke API key?" });
  await expect(dialog).toContainText(/stops working immediately/i);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();

  await page.reload();
  await expect(
    page.getByRole("row").filter({ hasText: name }).getByText("Active"),
  ).toBeVisible();
  const stillWorks = await request.get("/api/projects", {
    headers: {
      authorization: `Bearer ${apiKey}`,
      "x-motionly-workspace-id": workspaceId,
    },
  });
  expect(stillWorks.status(), await stillWorks.text()).toBe(200);
});

test("cancelling a plan asks first; backing out keeps it active", async ({
  page,
  user,
  stack,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await setSubscription(stack, workspaceId, {
    plan: "agency",
    status: "active",
  });

  await signIn(page, user, "/billing");
  const cancel = page.getByRole("button", { name: /Cancel at period end/i });
  await cancel.click();
  const dialog = page.getByRole("dialog", {
    name: "Cancel your subscription?",
  });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();

  await page.reload();
  await expect(cancel).toBeVisible();
  await expect(page.getByText(/Cancels at period end/i)).toHaveCount(0);
});
