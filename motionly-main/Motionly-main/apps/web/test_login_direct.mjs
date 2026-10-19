import { chromium } from "@playwright/test";

async function run() {
  console.log("Launching browser with sandbox disabled...");
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const context = await browser.newContext();
  const page = await context.newPage();

  console.log("Navigating to http://localhost:3000/login...");
  await page.goto("http://localhost:3000/login");

  // Wait for the form/inputs to be present
  console.log("Waiting for email input...");
  await page.waitForSelector("#email", { timeout: 10000 });

  console.log("Filling login credentials...");
  await page.locator("#email").fill("demo@motionly.test");
  await page.locator("#password").fill("demo1234");

  console.log("Clicking 'Log in' button...");
  await page.locator("button[type='submit']").click();

  console.log("Waiting for redirection...");
  try {
    await page.waitForURL((url) => !url.pathname.includes("/login"), {
      timeout: 15000,
    });
  } catch (err) {
    console.log("Timeout waiting for navigation. Current URL:", page.url());
  }

  const currentUrl = page.url();
  console.log("Final URL:", currentUrl);

  const screenshotPath =
    "/home/codespace/.gemini/antigravity-cli/brain/ffc8064e-bac4-44d0-ab19-0b27296f4685/login_result.png";
  await page.screenshot({ path: screenshotPath });
  console.log(`Saved screenshot to ${screenshotPath}`);

  await browser.close();
}

run().catch((err) => {
  console.error("Execution failed:", err);
  process.exit(1);
});
