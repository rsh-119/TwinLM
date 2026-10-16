import { expect, test, type Page } from "@playwright/test";

// One full journey on a fresh gateway (no users yet). Screenshots go to
// e2e/screenshots for design review.
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "local-admin";
const SHOTS = process.env.SHOTS_DIR || "e2e/screenshots";
const stamp = Date.now().toString(36);
const team = `e2e-${stamp}`;
const member = `dev-${stamp}@acme.io`;

async function shot(page: Page, name: string) {
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

test("admin sets up, builds a team; a member signs in and chats", async ({ browser }) => {
  const admin = await browser.newPage();

  // first run: set up the first admin
  await admin.goto("/app/");
  await expect(admin).toHaveURL(/\/app\/sign-in\//);
  await expect(admin.getByRole("heading", { name: "Set up TwinLM" })).toBeVisible();
  await shot(admin, "01-setup");
  await admin.getByLabel("Admin token").fill(ADMIN_TOKEN);
  await admin.getByLabel("Your name").fill("Ana Rivera");
  await admin.getByLabel("Work email").fill(`ana-${stamp}@acme.io`);
  await admin.getByLabel("Password").fill("ana-password-1");
  await admin.getByRole("button", { name: "Create admin account" }).click();

  // overview
  await expect(admin.getByRole("heading", { name: "Overview" })).toBeVisible();
  await shot(admin, "02-overview");

  // create a team and add a member
  await admin.getByRole("link", { name: "Teams" }).click();
  await admin.getByRole("button", { name: "New team" }).click();
  await admin.getByLabel("Name").fill(team);
  await admin.getByRole("button", { name: "Create team" }).click();
  await admin.getByRole("link", { name: team }).first().click();
  await admin.getByRole("button", { name: "Add member" }).click();
  await admin.getByLabel("Email").fill(member);
  await admin.getByLabel("Name").fill("Jae Mora");
  await admin.getByRole("dialog").getByRole("button", { name: "Add member" }).click();
  const temp = (await admin.getByRole("dialog").locator("code").textContent())!.trim();
  expect(temp.length).toBeGreaterThan(6);
  await shot(admin, "03-member-added");
  await admin.getByRole("button", { name: "Done" }).click();
  await expect(admin.getByText("Jae Mora")).toBeVisible();
  await shot(admin, "04-team-members");

  // providers and models pages render
  await admin.getByRole("link", { name: "Providers" }).click();
  await expect(admin.getByRole("heading", { name: "Providers" })).toBeVisible();
  await shot(admin, "05-providers");
  await admin.getByRole("button", { name: "Add provider" }).click();
  await admin.getByRole("button", { name: /Anthropic/ }).click();
  await shot(admin, "06-add-provider");
  await admin.keyboard.press("Escape");
  await admin.getByRole("link", { name: "Models" }).click();
  await expect(admin.getByRole("heading", { name: "Models" })).toBeVisible();
  await shot(admin, "07-models");

  // member: temporary password -> own password -> playground
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const dev = await ctx.newPage();
  dev.on("pageerror", (e) => console.log("PAGE ERROR:", e.message, (e.stack ?? "").slice(0, 400)));
  dev.on("console", (m) => m.type() === "error" && console.log("CONSOLE:", m.text()));
  await dev.goto("/app/sign-in/");
  await shot(dev, "08-sign-in");
  await dev.getByLabel("Email").fill(member);
  await dev.getByLabel("Password").fill(temp);
  await dev.getByRole("button", { name: "Sign in" }).click();
  await expect(dev.getByRole("heading", { name: "Choose your password" })).toBeVisible();
  await dev.getByLabel("Temporary password").fill(temp);
  await dev.getByLabel("New password", { exact: true }).fill("jae-password-1");
  await dev.getByLabel("Repeat new password").fill("jae-password-1");
  await dev.getByRole("button", { name: "Save and continue" }).click();
  await expect(dev.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(dev.getByRole("link", { name: "Providers" })).toHaveCount(0); // members don't see admin pages

  await dev.getByRole("link", { name: "Playground", exact: true }).click();
  await dev.getByPlaceholder(/Message/).fill("Say hello");
  await dev.keyboard.press("Enter");
  await expect(dev.getByText(/ms$/).first()).toBeVisible({ timeout: 20_000 });
  await shot(dev, "09-playground");

  // mobile layout
  await dev.setViewportSize({ width: 390, height: 844 });
  await dev.getByRole("link", { name: "Playground" }).count();
  await dev.goto("/app/");
  await shot(dev, "10-mobile-overview");
  await ctx.close();
});
