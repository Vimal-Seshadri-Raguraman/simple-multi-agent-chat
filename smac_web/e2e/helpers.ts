/**
 * Shared setup helpers for the SMAC web e2e specs (SMAC-92 Task 6):
 * account/workspace bootstrap steps every journey spec needs
 * (register/login/create-workspace/join-by-code/mint-invite/read-session),
 * extracted out of `journey.spec.ts` so `role-journey.spec.ts` doesn't
 * duplicate them. All specs in `e2e/` run against the SAME spawned server
 * (`global-setup.ts` spawns exactly one, for the whole `npm run e2e`
 * invocation) and the SAME throwaway temp database -- every spec must use
 * its own emails/workspace names so the two files' data never collides.
 */

import { type Page, expect } from "@playwright/test";

export const PASSWORD = "correct-horse-battery-staple";

export function baseURL(): string {
  const url = process.env.SMAC_E2E_BASE_URL;
  if (!url) {
    throw new Error(
      "SMAC_E2E_BASE_URL is unset -- e2e/global-setup.ts should have set it " +
        "before any test ran; are you running this file outside `npm run e2e`?"
    );
  }
  return url;
}

export async function gotoApp(page: Page): Promise<void> {
  await page.goto(`${baseURL()}/`);
}

export async function registerAccount(page: Page, email: string): Promise<void> {
  await page.getByRole("button", { name: "Create an account" }).click();
  await page.locator("#register-email").fill(email);
  await page.locator("#register-password").fill(PASSWORD);
  await page.locator("#register-confirm-password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Create or join a workspace" })).toBeVisible();
}

export async function loginExisting(page: Page, email: string): Promise<void> {
  await page.getByRole("button", { name: "Log in" }).click();
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in", exact: true }).click();
}

export async function createWorkspace(
  page: Page,
  opts: { workspaceName: string; firstName: string; lastName: string }
): Promise<void> {
  await page.getByRole("button", { name: "Create your own" }).click();
  await page.locator("#create-workspace-name").fill(opts.workspaceName);
  await page.locator("#create-workspace-first-name").fill(opts.firstName);
  await page.locator("#create-workspace-last-name").fill(opts.lastName);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.locator(".room__title")).toHaveText("#general");
}

export async function joinByCode(
  page: Page,
  opts: { firstName: string; lastName: string; code: string }
): Promise<void> {
  await page.getByRole("button", { name: "Join a workspace" }).click();
  await page.locator("#join-first-name").fill(opts.firstName);
  await page.locator("#join-last-name").fill(opts.lastName);
  await page.locator("#join-code").fill(opts.code);
  await page
    .locator("form", { has: page.locator("#join-code") })
    .getByRole("button", { name: "Join" })
    .click();
  await expect(page.locator(".room__title")).toHaveText("#general");
}

/** Reaches Settings' Invites panel via the composer's leading-`/` ->
 * palette hand-off (web spec §2's composer grammar), mints a fresh code of
 * the given `kind`, and returns it -- the real UI path, not a shortcut
 * through the API.
 *
 * `kind` defaults to "human". The kind selector (`InvitesPanel`'s Human/
 * Agent toggle) only renders for a caller holding BOTH mint capabilities
 * (SMAC-92 Task 5); a caller with only one mint cap goes straight to that
 * kind's section with no selector at all, so this only clicks the toggle
 * when it's actually present. `data-testid` is `invite-code-${kind}`
 * (`InvitesPanel.tsx`'s `MintSection`) -- kind-scoped since Task 5 split
 * the panel into an independent section per kind. */
export async function mintInviteCode(
  page: Page,
  opts: { kind?: "human" | "agent" } = {}
): Promise<string> {
  const kind = opts.kind ?? "human";
  await page.locator('textarea[aria-label="Message"]').fill("/invite");
  // Query "invite" also fuzzy-matches "/join" (its help text mentions
  // "invite code"), so this clicks the specific "/invite" entry by its
  // own name rather than trusting activeIndex 0.
  await page.locator(".palette__item").filter({ hasText: "/invite" }).click();
  if (kind === "agent") {
    const agentToggle = page.getByRole("button", { name: "Agent", exact: true });
    if (await agentToggle.isVisible()) {
      await agentToggle.click();
    }
  }
  const buttonName = kind === "agent" ? "Mint agent invite code" : "Mint invite code";
  await page.getByRole("button", { name: buttonName, exact: true }).click();
  const codeLocator = page.getByTestId(`invite-code-${kind}`);
  await expect(codeLocator).toBeVisible();
  const code = (await codeLocator.textContent())?.trim();
  if (!code) {
    throw new Error("invite code panel rendered but produced no code text");
  }
  return code;
}

export async function backToRoom(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Back to the room" }).click();
}

export async function readWorkspaceSession(
  page: Page
): Promise<{ workspaceId: string; token: string }> {
  const raw = await page.evaluate(() => window.localStorage.getItem("smac.session"));
  if (!raw) {
    throw new Error("no smac.session in localStorage -- is this page logged in yet?");
  }
  const session = JSON.parse(raw) as { workspaceId?: string; workspaceAccess?: string };
  if (!session.workspaceId || !session.workspaceAccess) {
    throw new Error("smac.session has no workspace tier yet");
  }
  return { workspaceId: session.workspaceId, token: session.workspaceAccess };
}
