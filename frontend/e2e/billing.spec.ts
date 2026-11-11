import { expect, test, type Page } from "@playwright/test";

// Runs against the demo shop. Each run adds a few bills to it, which is harmless.
const SHOTS = "e2e/screenshots";

async function addItem(page: Page, search: string, pick: RegExp) {
  await page.getByPlaceholder("Search item, or type / scan a barcode").fill(search);
  await page.getByRole("list", { name: "Matching items" }).getByRole("button", { name: pick }).first().click();
}

test("make a GST bill with a loose item, then return part of it", async ({ page, context }) => {
  await page.goto("/#counter");
  await expect(page.getByRole("heading", { name: "New bill" })).toBeVisible();
  await addItem(page, "tata salt", /Tata Salt 1kg/);
  await page.getByRole("button", { name: "One more" }).click();
  await addItem(page, "sugar", /Sugar \(loose\)/);
  await page.getByRole("button", { name: "500 g" }).click();
  // 2 x 28 + 0.5 kg x 46 = 79
  await expect(page.getByTestId("bill-total")).toHaveText("₹79");
  await page.getByRole("radio", { name: "UPI" }).click();
  await page.screenshot({ path: `${SHOTS}/counter-desktop.png`, fullPage: true });
  await page.getByRole("button", { name: /Save bill/ }).click();
  const saved = page.getByText(/Tax Invoice [A-Z0-9]+\/\d\d-\d\d\/\d{4} saved/);
  await expect(saved).toBeVisible();
  const number = (await saved.textContent())!.match(/[A-Z0-9]+\/\d\d-\d\d\/\d{4}/)![0];
  await expect(page.getByText("UPI ₹79.00")).toBeVisible();

  // The PDF opens in a new tab
  const [pdfTab] = await Promise.all([context.waitForEvent("page"), page.getByRole("button", { name: "80mm" }).click()]);
  await pdfTab.waitForURL(/^blob:/);
  await pdfTab.close();

  // History, detail and a partial return with a credit note
  await page.goto("/#invoices");
  await page.getByPlaceholder("Bill number, customer, phone or item").fill(number);
  await page.getByRole("button", { name: new RegExp(number.replace(/\//g, "\\/")) }).click();
  const dialog = page.getByRole("dialog", { name: "Bill details" });
  await expect(dialog.getByText("Tata Salt 1kg")).toBeVisible();
  await dialog.getByRole("button", { name: "Return items" }).click();
  await dialog.getByRole("textbox", { name: /Quantity of Tata Salt 1kg to return/ }).fill("1");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("pack torn");
  const [cnTab] = await Promise.all([context.waitForEvent("page"), dialog.getByRole("button", { name: "Make credit note" }).click()]);
  await cnTab.close();
  await expect(dialog.getByText(/CN\/\d\d-\d\d\/\d{4}/)).toBeVisible();
  await expect(dialog.getByText("1 returned")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/bill-detail-desktop.png` });
});

test("udhaar bill needs a customer and lands in udhaar", async ({ page }) => {
  const name = `E2E Customer ${Date.now() % 100000}`;
  await page.goto("/#counter");
  await addItem(page, "maggi 70g", /Maggi 2-Minute Masala Noodles 70g/);
  await page.getByRole("radio", { name: "Udhaar" }).click();
  const save = page.getByRole("button", { name: /Save bill/ });
  await expect(save).toBeDisabled();
  await expect(page.getByText("Udhaar needs the customer's name.")).toBeVisible();
  await page.getByRole("textbox", { name: "Customer name" }).fill(name);
  await save.click();
  await expect(page.getByText(new RegExp(`Udhaar ₹15.00 to ${name}`))).toBeVisible();
  await page.goto("/#udhaar");
  await expect(page.getByText(name, { exact: true })).toBeVisible();
});

test("price above MRP is blocked before saving", async ({ page }) => {
  await page.goto("/#counter");
  await addItem(page, "tata salt", /Tata Salt 1kg/);
  const price = page.getByRole("listitem").filter({ hasText: "Tata Salt 1kg" }).getByRole("textbox").nth(1);
  await price.fill("30");
  await expect(page.getByText(/price is above MRP/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Save bill/ })).toBeDisabled();
});

test("counter and bills pages fit a 390 px phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const hash of ["counter", "invoices"]) {
    await page.goto(`/#${hash}`);
    await page.waitForLoadState("networkidle");
    if (hash === "counter") await addItem(page, "sugar", /Sugar \(loose\)/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `${SHOTS}/${hash}-phone.png`, fullPage: true });
  }
});
