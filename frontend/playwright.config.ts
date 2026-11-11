import { defineConfig } from "@playwright/test";

// End-to-end tests of the real website against the real API.
// Start Stockora first (start.ps1, or uvicorn on :8000 and `npm run dev` on :3000), then: npx playwright test
// Uses your installed Google Chrome. To use Playwright's own browser instead: npx playwright install chromium,
// then set PW_BROWSER=chromium. PW_CHROME_PATH points at a specific Chrome executable.
const exe = process.env.PW_CHROME_PATH;
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.STOCKORA_URL ?? "http://localhost:3000",
    ...(exe ? { launchOptions: { executablePath: exe } } : process.env.PW_BROWSER === "chromium" ? {} : { channel: "chrome" }),
    screenshot: "only-on-failure",
  },
  outputDir: "e2e/.results",
});
