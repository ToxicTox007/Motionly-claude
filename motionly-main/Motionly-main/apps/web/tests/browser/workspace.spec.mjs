// A signed-in creator's workspace: dashboard, navigation, and creating and
// deleting a project.
import { expect, navigate, signIn, test } from "./support/fixtures.mjs";

test("the dashboard greets a new creator with the first step", async ({
  page,
  user,
  gates,
}) => {
  await signIn(page, user);

  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await expect(
    page
      .getByRole("link", { name: /create new video|create my first reel/i })
      .first(),
  ).toBeVisible();
  await gates.checkAccessibility(page);
});

for (const [label, path] of [
  ["Projects", "/projects"],
  ["Billing", "/billing"],
  ["Settings", "/account"],
]) {
  test(`navigation reaches ${label}`, async ({ page, user, gates }) => {
    await signIn(page, user);

    await navigate(page, label);
    await expect(page).toHaveURL((url) => url.pathname === path);
    await gates.checkAccessibility(page);
  });
}

test("a creator can create a project, open it, and delete it", async ({
  page,
  user,
  gates,
}) => {
  const title = `Browser suite ${Date.now()}`;
  await signIn(page, user, "/projects/new");

  await page.getByLabel("Project Name").fill(title);
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(
    page.getByRole("heading", { name: "Upload your footage" }),
  ).toBeVisible();
  await gates.checkAccessibility(page);

  await page.goto("/projects");
  // Each project card shows its title in an editable field.
  const card = page
    .getByRole("article")
    .filter({ has: page.locator(`input[value="${title}"]`) });
  await expect(card).toBeVisible();
  await gates.checkAccessibility(page);
  // Deleting is irreversible, so it is confirmed first.
  await card.getByRole("button", { name: `Delete ${title}` }).click();
  await page
    .getByRole("dialog", { name: "Delete project?" })
    .getByRole("button", { name: "Delete project", exact: true })
    .click();
  await expect(card).toHaveCount(0);
});

test("an empty project name is refused before it is sent", async ({
  page,
  user,
}) => {
  await signIn(page, user, "/projects/new");

  await page.getByLabel("Project Name").fill("   ");
  await expect(
    page.getByText("Project name cannot contain only spaces."),
  ).toBeVisible();
  await page.getByRole("button", { name: /Continue to Upload/i }).click();

  await expect(page).toHaveURL(/\/projects\/new$/);
  await expect(page.getByText("Project name cannot be empty.")).toBeVisible();
});

test("a project name filled without input events still creates the project", async ({
  page,
  user,
}) => {
  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").waitFor();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => {
    document.querySelector('[name="title"]').value = "Filled by the browser";
  });
  await page.getByRole("button", { name: /Continue to Upload/i }).click();

  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
});

// WCAG 2.4.2: every page has a title that names it, after sign-in and after
// each client-side navigation.
test("every signed-in page keeps a title that names it", async ({
  page,
  user,
}) => {
  await signIn(page, user);
  await expect(page).toHaveTitle("Dashboard · Motionly");

  for (const [label, title] of [
    ["Projects", "Projects · Motionly"],
    ["Billing", "Billing · Motionly"],
    ["Settings", "Account · Motionly"],
    ["Dashboard", "Dashboard · Motionly"],
  ]) {
    await navigate(page, label);
    await expect(page).toHaveTitle(title);
  }

  await page.goto("/projects/new");
  await expect(page).toHaveTitle("Create New Reel · Motionly");
  await page.getByLabel("Project Name").fill("Titled reel");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(page).toHaveTitle("Titled reel · Motionly");
});
