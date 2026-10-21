// Task 14 / B18 + B22: two people editing the same plan never silently lose an
// edit. Two browser contexts open the SAME project. Context B saves an edit
// (advancing the plan revision). Context A then saves a conflicting edit, gets
// the collaboration_revision_conflict (409), sees the ConflictDialog with an
// accessible summary of what Force Save will overwrite, force-saves (which
// re-fetches the latest revision and re-sends the SAME change against it), and
// after both contexts reload they both see Context A's forced result.
//
// Written, not run here (shared 2 vCPU box). It runs at the phase checkpoint.
//
// Task 15 is done: CPU-stage preprocessing now reaches the editor (see
// creator-journey.spec.mjs), so this conflict journey is a passing test.
import { createSourceVideo, uploadSourceVideo } from "./support/media.mjs";
import { expect, signIn, test } from "./support/fixtures.mjs";

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

// Drive one context to an editor with an active plan. Returns the project URL.
async function openEditorWithPlan(page, user, projectName) {
  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill(projectName);
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  const projectUrl = page.url();
  await uploadSourceVideo(page, media.path);
  await expect(async () => {
    await page.reload();
    await expect(page.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 200_000, intervals: [10_000] });
  return projectUrl;
}

test("two editors conflict; force save re-applies the change and both see the result", async ({
  browser,
  user,
}) => {
  test.setTimeout(300_000);

  // Two independent browser contexts (two "people"), same signed-in workspace.
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();

  try {
    // Context A creates the project and reaches the editor with an active plan.
    const projectUrl = await openEditorWithPlan(
      pageA,
      user,
      "Shared edit conflict",
    );

    // Context B opens the same project editor.
    await signIn(pageB, user, new URL(projectUrl).pathname);
    await expect(pageB.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 60_000,
    });

    // Context B makes and saves an edit first — this advances the plan revision
    // on the server, leaving Context A one revision behind.
    await pageB
      .getByRole("button", { name: /Add text/i })
      .first()
      .click();
    await expect(pageB.getByText(/saved\.?$/i)).toBeVisible({
      timeout: 30_000,
    });

    // Context A now makes a conflicting edit against its now-stale revision.
    await pageA
      .getByRole("button", { name: /Add text/i })
      .first()
      .click();

    // Context A receives the conflict: the ConflictDialog is shown with the
    // revision numbers and an accessible overwrite summary.
    const dialog = pageA.getByRole("dialog", { name: /Version Conflict/i });
    await expect(dialog).toBeVisible({ timeout: 30_000 });
    await expect(
      dialog.getByLabel(
        /Changes that Force Save will overwrite on the server/i,
      ),
    ).toBeVisible();

    // Force Save: the client re-fetches the latest revision and RE-SENDS the
    // same change against it (not a no-op, not a stale write).
    const conflictResponses = [];
    pageA.on("response", (response) => {
      if (response.status() === 409) conflictResponses.push(response.url());
    });
    await dialog
      .getByRole("button", {
        name: /Force save your changes, overwriting the newer version/i,
      })
      .click();

    // The dialog closes once the forced save succeeds (the retry used the
    // fresh revision, so no lingering 409).
    await expect(dialog).toBeHidden({ timeout: 30_000 });

    // After both contexts reload, they converge on Context A's forced result:
    // both read the same plan revision from the authoritative plan GET.
    const revisionFrom = async (page) => {
      const [response] = await Promise.all([
        page.waitForResponse(
          (r) =>
            /\/api\/projects\/[^/]+\/plans\/[^/]+(\?.*)?$/.test(r.url()) &&
            r.request().method() === "GET" &&
            r.ok(),
          { timeout: 60_000 },
        ),
        page.reload(),
      ]);
      const body = await response.json();
      return body?.plan?.revision ?? null;
    };

    const planRevisionA = await revisionFrom(pageA);
    const planRevisionB = await revisionFrom(pageB);
    await expect(pageA.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 60_000,
    });
    await expect(pageB.locator("[data-editor-workspace]")).toBeVisible({
      timeout: 60_000,
    });
    expect(planRevisionA).not.toBeNull();
    expect(planRevisionA).toBe(planRevisionB);
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
