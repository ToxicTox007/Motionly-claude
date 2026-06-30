import { expect, test } from "@playwright/test";

test("Can login with demo credentials", async ({ page }) => {
  console.log("Navigating to login page...");
  await page.goto("/login");

  console.log("Filling credentials...");
  await page.getByLabel("Email").fill("demo@motionly.test");
  await page.getByLabel("Password").fill("demo1234");

  console.log("Submitting login form...");
  await page.getByRole("button", { name: "Log in" }).click();

  console.log("Waiting for redirection...");
  await expect(page).toHaveURL(/\/dashboard/);
  console.log("Login successful! Redirected to:", page.url());

  // Take a screenshot of the dashboard
  const screenshotPath = "/home/codespace/.gemini/antigravity-cli/brain/ffc8064e-bac4-44d0-ab19-0b27296f4685/dashboard_login.png";
  await page.screenshot({ path: screenshotPath });
  console.log("Dashboard screenshot saved to:", screenshotPath);
});
