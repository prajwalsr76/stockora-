import { expect, test } from "@playwright/test";

// The welcome page, the English/Hindi switch, and the first-run wizard (import an item list
// from a billing app export, fix a column the app could not recognise, then go back to the demo).
const SHOTS = "e2e/screenshots";

test("welcome page shows on the first visit and opens the demo", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Know what");
  await expect(page.getByText("Not yet validated on Indian data", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: /Try the demo shop/ }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  // Second visit goes straight into the app
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
});

test("the whole app switches between English and Hindi", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("stockora_seen", "1"));
  await page.goto("/#stock");
  await expect(page.getByRole("heading", { name: "Inventory" })).toBeVisible();
  await page.getByRole("button", { name: "हिन्दी में देखें" }).first().click();
  await expect(page.getByRole("heading", { name: "स्टॉक" })).toBeVisible();
  await page.goto("/#counter");
  await expect(page.getByRole("heading", { name: "नया बिल" })).toBeVisible();
  await expect(page.getByPlaceholder("सामान खोजें, या बारकोड लिखें / स्कैन करें")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/counter-hindi.png` });
  await page.getByRole("button", { name: "View in English" }).first().click();
  await expect(page.getByRole("heading", { name: "New bill" })).toBeVisible();
});

test("set up a new shop by importing an item list, then return to the demo", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("stockora_seen", "1"));
  await page.goto("/#setup");
  const shopName = `Test Kirana ${Date.now() % 100000}`;
  await page.getByPlaceholder("e.g. Patil Kirana Stores").fill(shopName);
  await page.getByPlaceholder("e.g. Pune").fill("Pune");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // The sidebar switches to the new shop straight away
  await expect(page.getByText(shopName).first()).toBeVisible();
  await page.getByRole("button", { name: /Import from my billing app/ }).click();
  await page.getByLabel("Choose an Excel or CSV file").first().setInputFiles("e2e/fixtures/vyapar_items.csv");
  await expect(page.getByText("3 rows found", { exact: false })).toBeVisible();
  // "Particulars" is Tally's word for the item name; the app should have recognised it
  await expect(page.getByLabel("Meaning of column Particulars")).toHaveValue("name");
  // "Qty Left" is not a standard name, so the owner fixes it by hand
  await page.getByLabel("Meaning of column Qty Left").selectOption("stock");
  await page.screenshot({ path: `${SHOTS}/setup-import-preview.png`, fullPage: true });
  await page.getByRole("button", { name: "Import 3 rows" }).click();
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("button", { name: "Go to overview" }).click();

  await page.goto("/#stock");
  await expect(page.getByText("Patil Special Poha 500g").first()).toBeVisible();
  await expect(page.getByText("Parle-G", { exact: false })).toHaveCount(0);

  // Back to the demo shop through the shop switcher
  await page.goto("/#setup");
  await page.getByRole("button", { name: "Stay in the demo shop" }).click();
  await page.goto("/#stock");
  await expect(page.getByText("Parle-G", { exact: false }).first()).toBeVisible();
});
