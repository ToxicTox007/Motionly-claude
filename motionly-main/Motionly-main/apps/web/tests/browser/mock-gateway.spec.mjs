// Task 22: the demo payment gateway's checkout pages at /pay/* render for a
// signed-out viewer, pass the accessibility gates, and complete each payment
// method end to end. Every page states that no money is charged. The gateway
// runs behind the `demo-billing` Compose profile and the ingress routes /pay/*
// to it (infra/Caddyfile.routes); these specs are written now and run later at
// the phase checkpoint (they are not run in this task).
import { expect, test } from "./support/fixtures.mjs";

const DEMO_NOTE = "Demo payment — no money is charged";

// Start a fresh demo order through the public self-service landing page.
async function startDemoOrder(page, rupees = "499") {
  const landing = await page.goto("/pay/");
  expect(landing?.status()).toBe(200);
  await expect(page.getByText(DEMO_NOTE)).toBeVisible();
  await page.getByLabel(/Amount/i).fill(rupees);
  await page.getByRole("button", { name: /Create demo order/i }).click();
  // The checkout page offers both UPI and card.
  await expect(page.getByRole("button", { name: /Pay by UPI/i })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Pay by card/i }),
  ).toBeVisible();
}

test("the demo landing page renders for a signed-out viewer", async ({
  page,
  gates,
}) => {
  const response = await page.goto("/pay/");

  expect(response?.status()).toBe(200);
  await expect(page.locator("h1").first()).toBeVisible();
  await expect(page.getByText(DEMO_NOTE)).toBeVisible();
  await gates.checkAccessibility(page);
});

test("UPI approve captures the demo payment", async ({ page, gates }) => {
  await startDemoOrder(page);
  await gates.checkAccessibility(page);

  await page.getByLabel(/UPI ID/i).fill("success@upi");
  await page.getByLabel(/Outcome/i).selectOption("approve");
  await page.getByRole("button", { name: /Pay by UPI/i }).click();

  await expect(
    page.getByRole("heading", { name: /Payment successful/i }),
  ).toBeVisible();
  await expect(page.getByText(DEMO_NOTE)).toBeVisible();
  await gates.checkAccessibility(page);
});

test("UPI decline does not complete the demo payment", async ({ page }) => {
  await startDemoOrder(page);

  await page.getByLabel(/UPI ID/i).fill("failure@upi");
  await page.getByLabel(/Outcome/i).selectOption("decline");
  await page.getByRole("button", { name: /Pay by UPI/i }).click();

  await expect(
    page.getByRole("heading", { name: /Payment not completed/i }),
  ).toBeVisible();
});

test("a card success stores only brand and last four", async ({
  page,
  gates,
}) => {
  await startDemoOrder(page);

  await page.getByLabel(/Card number/i).fill("4111111111111111");
  await page.getByRole("button", { name: /Pay by card/i }).click();

  await expect(
    page.getByRole("heading", { name: /Payment successful/i }),
  ).toBeVisible();
  // The full PAN is never echoed back into the result page.
  await expect(page.locator("body")).not.toContainText("4111111111111111");
  await gates.checkAccessibility(page);
});

test("a card that needs an OTP completes after 123456", async ({
  page,
  gates,
}) => {
  await startDemoOrder(page);

  await page.getByLabel(/Card number/i).fill("4000000000003220");
  await page.getByRole("button", { name: /Pay by card/i }).click();

  // The OTP step is its own accessible page.
  await expect(
    page.getByRole("heading", { name: /one-time password/i }),
  ).toBeVisible();
  await gates.checkAccessibility(page);

  await page.getByLabel(/One-time password/i).fill("123456");
  await page.getByRole("button", { name: /Confirm payment/i }).click();

  await expect(
    page.getByRole("heading", { name: /Payment successful/i }),
  ).toBeVisible();
  await expect(page.getByText(DEMO_NOTE)).toBeVisible();
});

test("a declined card reports failure", async ({ page }) => {
  await startDemoOrder(page);

  await page.getByLabel(/Card number/i).fill("4000000000000002");
  await page.getByRole("button", { name: /Pay by card/i }).click();

  await expect(
    page.getByRole("heading", { name: /Payment not completed/i }),
  ).toBeVisible();
});
