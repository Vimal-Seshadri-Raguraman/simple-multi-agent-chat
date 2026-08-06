/**
 * The SMAC web UI's ROLE journey (SMAC-92 Task 6): the same "real spawned
 * server, real browser, no mocks" discipline `journey.spec.ts` established
 * (see that file's module docstring), extended to the roles-and-agent-
 * invites surface Tasks 1-5 shipped -- role promotion, agent invite codes,
 * an unauthenticated agent redemption, live agent-styled message delivery,
 * capability-gated palette entries, and member removal.
 *
 * Runs in the SAME `npm run e2e` invocation as `journey.spec.ts` -- one
 * spawned server (`e2e/global-setup.ts`), one throwaway temp database, for
 * the whole run -- so every account/workspace name here is disjoint from
 * that file's (`alice@example.com`/`bob@example.com`/"Acme Research") to
 * avoid colliding in the shared DB.
 *
 * One long serial journey (same rationale as `journey.spec.ts`'s own
 * docstring: each step's state is exactly what the previous step left
 * behind) rather than isolated tests, covering the task-6 brief's five
 * numbered arms in order: (1) founder + human-code join lands on "member"
 * with no admin tabs, (2) promotion to Agent Admin gained live via a
 * focus-refresh (no reload), (3) an agent invite code redeemed OUTSIDE the
 * browser and its first message arriving LIVE with agent styling in BOTH
 * open tabs, (4) capability-gated negative arms (palette dimming + direct
 * API 403/404), (5) member removal landing the removed member's own tab
 * back at login and 4xx-ing their old token.
 */

import { type APIRequestContext, type Page, expect, test } from "@playwright/test";
import {
  backToRoom,
  baseURL,
  createWorkspace,
  gotoApp,
  joinByCode,
  mintInviteCode,
  readWorkspaceSession,
  registerAccount,
} from "./helpers";

const ALICE_EMAIL = "ivy@example.com";
const BOB_EMAIL = "remy@example.com";
const CAROL_EMAIL = "cora@example.com";
// Same deterministic-handle derivation `journey.spec.ts` documents
// (`generate_unique_handle` off `f"{first_name[0]}{last_name}"`, slugified).
const ALICE_HANDLE = "iirwin";
const BOB_HANDLE = "rross";
const CAROL_HANDLE = "ccole";
const WORKSPACE_NAME = "Roles Test Co";

type ChannelSummary = { channel_id: string; channel_name: string };
type ErrorEnvelope = { error: { code: string; message: string } };
type AgentJoinResponse = {
  account_id: string;
  member_id: string;
  handle: string;
  api_key: string;
  workspace: { workspace_id: string; workspace_name: string; visibility: string };
};

async function openSettings(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Settings", exact: true }).click();
}

function settingsTabs(page: Page) {
  return page.locator('nav[aria-label="Settings sections"]');
}

/** Mints an invite code from an ALREADY-OPEN Invites tab (as opposed to
 * `helpers.ts`'s `mintInviteCode`, which reaches Invites from the Room via
 * the composer's `/invite` hand-off) -- used for Bob's mint below, since
 * that step's whole point is minting from the tab he just gained without
 * ever having left Settings to reach a composer. */
async function mintFromOpenInvitesTab(page: Page, kind: "human" | "agent"): Promise<string> {
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

async function fetchGeneralChannelId(
  request: APIRequestContext,
  opts: { workspaceId: string; token: string }
): Promise<string> {
  const resp = await request.get(`${baseURL()}/workspaces/${opts.workspaceId}/channels`, {
    headers: { Authorization: `Bearer ${opts.token}` },
  });
  if (!resp.ok()) {
    throw new Error(`GET channels failed: ${resp.status()} ${await resp.text()}`);
  }
  const channels = (await resp.json()) as ChannelSummary[];
  const general = channels.find((c) => c.channel_name === "general");
  if (!general) {
    throw new Error(`#general not found among ${JSON.stringify(channels)}`);
  }
  return general.channel_id;
}

test.describe.serial("SMAC web UI role journey: promotion, agent invites, gating, removal", () => {
  test("founder promotes a member, an agent joins by code and posts live, gated actions refuse, removal 4xxes the removed member", async ({
    browser,
    request,
  }) => {
    test.slow(); // real network round trips, a redemption outside the browser, and cross-tab live delivery

    const aliceContext = await browser.newContext();
    const bobContext = await browser.newContext();
    const alice = await aliceContext.newPage();
    const bob = await bobContext.newPage();

    try {
      // ---------------------------------------------------------------
      // Arm 1: Alice founds the workspace (admin); Bob joins by a human
      // invite code and lands as a plain "member" -- no admin tabs at all.
      // ---------------------------------------------------------------
      await gotoApp(alice);
      await registerAccount(alice, ALICE_EMAIL);
      await createWorkspace(alice, {
        workspaceName: WORKSPACE_NAME,
        firstName: "Ivy",
        lastName: "Irwin",
      });

      const aliceSession = await readWorkspaceSession(alice);
      const generalChannelId = await fetchGeneralChannelId(request, {
        workspaceId: aliceSession.workspaceId,
        token: aliceSession.token,
      });

      const humanCodeForBob = await mintInviteCode(alice, { kind: "human" });
      await backToRoom(alice);
      await expect(alice.locator(".room__title")).toHaveText("#general");

      await gotoApp(bob);
      await registerAccount(bob, BOB_EMAIL);
      await joinByCode(bob, { firstName: "Remy", lastName: "Ross", code: humanCodeForBob });

      // Bob opens Settings as a fresh "member": Agents is the only tab --
      // no Invites/Members/Workspace admin surface reachable at all
      // (`Settings.tsx`'s capability-driven `sections` list; task-6 brief's
      // "Bob is a member (no Invites/Workspace tabs — assert)").
      await openSettings(bob);
      await expect(settingsTabs(bob).getByRole("button", { name: "Agents" })).toBeVisible();
      await expect(settingsTabs(bob).getByRole("button", { name: "Invites" })).toHaveCount(0);
      await expect(settingsTabs(bob).getByRole("button", { name: "Workspace" })).toHaveCount(0);
      await expect(settingsTabs(bob).getByRole("button", { name: "Members" })).toHaveCount(0);
      // Bob's tab stays right here (on Settings, Agents tab) through the
      // next step -- the point of Arm 2 is proving this SAME tab gains
      // Invites live, not that a freshly-opened Settings screen has it.

      // ---------------------------------------------------------------
      // Arm 2: Alice promotes Bob to Agent Admin via the Members role
      // dropdown; Bob's already-open tab gains the Invites tab via a
      // window-focus dispatch -- NO reload.
      // ---------------------------------------------------------------
      // Alice's own member directory was fetched on her page's initial
      // load, before Bob existed -- same documented gap `journey.spec.ts`
      // reloads around for its own member-list read (`refreshMembers` is
      // never invoked on focus/socket-gap, only from explicit mutation
      // call sites -- task-4/5 reports). A reload here is Alice picking up
      // Bob so she can act on him; it has no bearing on the LIVE-refresh
      // assertion below, which is entirely on Bob's tab and is never
      // reloaded.
      await alice.reload();
      await expect(alice.locator(".room__title")).toHaveText("#general");
      await openSettings(alice);
      await settingsTabs(alice).getByRole("button", { name: "Members", exact: true }).click();

      // Alice's own row carries the "(you)" marker every other member's
      // row lacks (`MembersAdminPanel.tsx`'s `isSelf` check) -- self-
      // demotion via the role select IS allowed (only self-REMOVAL is
      // blocked), so this is the one self-identifying difference to check.
      const aliceRow = alice.locator("li.members-admin__row").filter({
        hasText: `@${ALICE_HANDLE}`,
      });
      await expect(aliceRow.locator(".members-admin__you-mark")).toBeVisible();

      const bobRoleSelect = alice.getByLabel(`Role for @${BOB_HANDLE}`);
      await expect(bobRoleSelect).toBeVisible();
      await bobRoleSelect.selectOption("agent_admin");
      await expect(bobRoleSelect).toHaveValue("agent_admin");

      // Bob's tab: never reloaded or re-navigated since it landed on
      // Settings above. A window "focus" dispatch is the same live seam
      // `AuthedShell.tsx`'s own `onFocus` handler uses to call
      // `refreshWhoami()` (task-4 report) -- this proves the CAPABILITY
      // refresh is live, not that a page load happens to show it.
      await bob.evaluate(() => window.dispatchEvent(new Event("focus")));
      const bobInvitesTab = settingsTabs(bob).getByRole("button", { name: "Invites" });
      await expect(bobInvitesTab).toBeVisible({ timeout: 20_000 });
      await bobInvitesTab.click();

      // ---------------------------------------------------------------
      // Arm 3: Bob (now Agent Admin) mints an agent invite code from the
      // tab he just gained; the test redeems it OUTSIDE the browser via
      // the request context, then posts as that agent via REST; BOTH
      // open browser tabs see the message arrive LIVE with agent styling.
      // ---------------------------------------------------------------
      const agentCode = await mintFromOpenInvitesTab(bob, "agent");
      await backToRoom(bob);
      await expect(bob.locator(".room__title")).toHaveText("#general");

      const joinResp = await request.post(`${baseURL()}/agents/join`, {
        data: { code: agentCode, name: "Probe Bot" },
      });
      expect(joinResp.status()).toBe(201);
      const probeBot = (await joinResp.json()) as AgentJoinResponse;
      expect(probeBot.workspace.workspace_id).toBe(aliceSession.workspaceId);

      // `POST /agents/join` mints the agent's ACCOUNT + membership only --
      // it does NOT add the new member to any channel (`_register_member`
      // is the same bare-membership helper the authed `/members/agents`
      // door uses; `join_as_agent`'s docstring only promises identity +
      // key). Posting to #general still requires channel membership
      // (`authorize_post_message`), so Probe Bot is added the same
      // sanctioned way `journey.spec.ts`'s `addChannelMembers` works
      // around the identical "create/register doesn't imply channel
      // membership" gap, via an admin's own workspace-tier token.
      const addToChannelResp = await request.post(
        `${baseURL()}/workspaces/${aliceSession.workspaceId}/channels/${generalChannelId}/members`,
        {
          headers: { Authorization: `Bearer ${aliceSession.token}` },
          data: { member_id: probeBot.member_id },
        }
      );
      expect(addToChannelResp.ok()).toBe(true);

      // Alice's and Bob's tabs both loaded their member directory before
      // Probe Bot existed (same one-shot-fetch gap as Arm 2's member list,
      // task-4/5 reports: no "member joined" broadcast, no focus/interval
      // refetch for it) -- `MessageLine.tsx`'s agent styling reads THAT
      // directory (`memberById[sender.member_id].member_type`), not the
      // message payload itself. Reloaded here, BEFORE Probe Bot's message
      // is posted below -- the live-delivery assertion that follows is
      // never preceded by a reload of its own, so it stays honest: the
      // MESSAGE itself, and its styling, both arrive over the open socket
      // with no further page action in between.
      await alice.reload();
      await expect(alice.locator(".room__title")).toHaveText("#general");
      await bob.reload();
      await expect(bob.locator(".room__title")).toHaveText("#general");

      const probeMessageText = "Reporting for duty as Probe Bot.";
      const postAsAgentResp = await request.post(
        `${baseURL()}/workspaces/${aliceSession.workspaceId}/channels/${generalChannelId}/messages`,
        {
          headers: { "X-API-Key": probeBot.api_key },
          data: { message_text: probeMessageText },
        }
      );
      expect(postAsAgentResp.ok()).toBe(true);

      for (const page of [alice, bob]) {
        const line = page
          .locator('[data-testid="message-line"]')
          .filter({ hasText: probeMessageText });
        await expect(line).toBeVisible({ timeout: 20_000 });
        await expect(line.locator(".message-line__avatar")).toHaveClass(
          /message-line__avatar--agent/
        );
      }

      // ---------------------------------------------------------------
      // Arm 4: negative/gated arms. Bob (agent_admin) can run `/invite`
      // (he holds `mint_agent_invites`) but `/workspace delete` stays
      // dimmed (he lacks `manage_workspace`); direct API checks pin the
      // same wall server-side.
      // ---------------------------------------------------------------
      await bob.keyboard.press("Control+k");
      await expect(bob.getByRole("dialog", { name: "Command palette dialog" })).toBeVisible();
      const inviteItem = bob.locator(".palette__item").filter({ hasText: "/invite" });
      await expect(inviteItem).not.toHaveClass(/palette__item--gated/);
      const workspaceDeleteItem = bob
        .locator(".palette__item")
        .filter({ hasText: "/workspace delete" });
      await expect(workspaceDeleteItem).toHaveClass(/palette__item--gated/);
      await expect(workspaceDeleteItem).toContainText("requires Workspace Admin");
      await bob.keyboard.press("Escape");

      const bobSession = await readWorkspaceSession(bob);
      const humanMintByBobResp = await request.post(
        `${baseURL()}/workspaces/${bobSession.workspaceId}/invites`,
        {
          headers: { Authorization: `Bearer ${bobSession.token}` },
          data: { invite_type: "code" },
        }
      );
      expect(humanMintByBobResp.status()).toBe(403);
      const humanMintByBobBody = (await humanMintByBobResp.json()) as ErrorEnvelope;
      expect(humanMintByBobBody.error.code).toBe("forbidden");

      // The agent code Bob minted above was already burnt by Probe Bot's
      // single-use redemption -- reusing it hits the SAME uniform 404
      // every invalid/expired/already-redeemed code hits
      // (`join_as_agent`'s docstring: no distinguishing signal).
      const reuseResp = await request.post(`${baseURL()}/agents/join`, {
        data: { code: agentCode, name: "Should Not Work" },
      });
      expect(reuseResp.status()).toBe(404);
      const reuseBody = (await reuseResp.json()) as ErrorEnvelope;
      expect(reuseBody.error.code).toBe("invalid_invite");

      // ---------------------------------------------------------------
      // Arm 5: removal. Carol joins (another human code Alice mints);
      // Alice removes her via typed confirmation. Carol's own next UI
      // action lands her back at the login screen, and her captured old
      // workspace token 4xxes against the real API.
      // ---------------------------------------------------------------
      const carolContext = await browser.newContext();
      const carol = await carolContext.newPage();
      try {
        const humanCodeForCarol = await mintInviteCode(alice, { kind: "human" });
        await backToRoom(alice);

        await gotoApp(carol);
        await registerAccount(carol, CAROL_EMAIL);
        await joinByCode(carol, { firstName: "Cora", lastName: "Cole", code: humanCodeForCarol });
        const carolSession = await readWorkspaceSession(carol);

        // Same one-shot member-list gap as Arms 2/3: Alice's directory
        // predates Carol's join.
        await alice.reload();
        await expect(alice.locator(".room__title")).toHaveText("#general");
        await openSettings(alice);
        await settingsTabs(alice).getByRole("button", { name: "Members", exact: true }).click();

        const carolRow = alice.locator("li.members-admin__row").filter({
          hasText: `@${CAROL_HANDLE}`,
        });
        await expect(carolRow).toBeVisible();
        await carolRow.getByRole("button", { name: "Remove", exact: true }).click();
        // Typed-confirmation form (`MembersAdminPanel.tsx`): the handle
        // field wants the BARE handle (no "@" -- only the label's copy
        // shows the "@" prefix), then the literal word "remove".
        const confirmInputs = carolRow.locator("input");
        await confirmInputs.nth(0).fill(CAROL_HANDLE);
        await confirmInputs.nth(1).fill("remove");
        await carolRow
          .getByRole("button", { name: `Remove @${CAROL_HANDLE}`, exact: true })
          .click();
        await expect(carolRow).toHaveCount(0);

        // Carol's tab was never touched by the removal above (it happened
        // entirely on Alice's tab via the real UI) -- her NEXT UI action
        // is what surfaces it: sending a message hits the workspace-tier
        // API, 401s (`get_current_member` resolves membership live, no
        // caching), exhausts `lib/api.ts`'s refresh-then-remint recovery
        // chain (her refresh token was deleted by the removal cascade,
        // and re-minting a fresh workspace token 404s with no membership
        // left to find), and raises `SessionExpired` -- which
        // `state/auth.tsx`'s globally-registered handler turns into
        // landing on the "login" screen, no reload/navigation from this
        // test at all.
        const carolComposer = carol.locator('textarea[aria-label="Message"]');
        await carolComposer.click();
        await carolComposer.pressSequentially("still here?");
        await carolComposer.press("Enter");
        await expect(carol.getByRole("heading", { name: "Log in" })).toBeVisible({
          timeout: 20_000,
        });

        // Direct API confirmation of the same wall: Carol's own captured
        // (pre-removal) workspace token, presented fresh against the real
        // server, 4xxes -- not something the client's own recovery/UI
        // logic could be hiding.
        const staleTokenResp = await request.get(
          `${baseURL()}/workspaces/${carolSession.workspaceId}/channels`,
          { headers: { Authorization: `Bearer ${carolSession.token}` } }
        );
        expect(staleTokenResp.status()).toBe(401);
        const staleTokenBody = (await staleTokenResp.json()) as ErrorEnvelope;
        expect(staleTokenBody.error.code).toBe("invalid_token");
      } finally {
        await carolContext.close();
      }
    } finally {
      await aliceContext.close();
      await bobContext.close();
    }
  });
});
