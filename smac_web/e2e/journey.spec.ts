/**
 * The SMAC web UI's end-to-end journey (SMAC-85 Task 6, web spec §5's
 * closing bullet: "the Alice→Bob journey in two browser contexts... plus
 * a mobile-viewport pass"). Runs against a REAL spawned server
 * (`e2e/global-setup.ts`) serving the actual committed bundle at
 * `app/static/webui/` -- no mocked `fetch`, no fake WebSocket, exactly
 * the environment `smac-server --start` + a browser gives a real user.
 *
 * Deliberately one long desktop journey (mirrors `tests/test_tui_e2e.py`'s
 * own stated philosophy: the point of an e2e test is that each step's
 * state -- accounts, workspace, channel memberships, read cursors -- is
 * exactly what the PREVIOUS step left behind) rather than many small
 * isolated tests: register → create workspace → mint an invite → a second
 * identity joins by code → a live @mention crosses between two open tabs
 * with NO reload/navigation in between (the thing that actually proves
 * the WebSocket live layer works, not a mocked one) → a second-channel
 * mention rings the bell on a tab sitting somewhere else entirely →
 * clicking through clears the mention badge (mark-read). The mobile pass
 * is a separate `test()` (mobile Bob only needs the account/message state
 * the first test already created, not its open pages), tied to the first
 * via `test.describe.serial` so it never runs against a half-set-up
 * workspace if the desktop journey fails partway through.
 */

import { type APIRequestContext, type Page, expect, test } from "@playwright/test";
import {
  backToRoom,
  baseURL,
  createWorkspace,
  gotoApp,
  joinByCode,
  loginExisting,
  mintInviteCode,
  readWorkspaceSession,
  registerAccount,
} from "./helpers";

const ALICE_EMAIL = "alice@example.com";
const BOB_EMAIL = "bob@example.com";
// Handles are deterministic: app/accounts.py::create_member_account derives
// them via `generate_unique_handle(db, ws_id, f"{first_name[0]}{last_name}")`
// -- slugified, lowercased. First/last names below are chosen to land on
// these exact, readable handles.
const ALICE_HANDLE = "aanders";
const BOB_HANDLE = "bbaker";
const WORKSPACE_NAME = "Acme Research";

/** Types `before` then `@` + the first two characters of `mentionHandle`
 * (enough for the composer's autocomplete filter to find exactly one
 * match in these tests' 2-member workspace), clicks the real popper
 * option -- proving the AUTOCOMPLETE path, not just that "@handle" text
 * happens to parse server-side -- then types `after` and sends. */
async function sendMentioning(
  page: Page,
  opts: { before: string; mentionHandle: string; after: string }
): Promise<void> {
  const composer = page.locator('textarea[aria-label="Message"]');
  await composer.click();
  await composer.pressSequentially(`${opts.before}@${opts.mentionHandle.slice(0, 2)}`);
  await page
    .locator("li.autocomplete__item")
    .filter({ hasText: `@${opts.mentionHandle}` })
    .click();
  if (opts.after) {
    await composer.pressSequentially(opts.after);
  }
  await composer.press("Enter");
}

/**
 * Adds every handle in `memberHandles` to the channel named `channelName`,
 * via the real REST API (an admin's own workspace-tier bearer token --
 * exactly what any other API client would use).
 *
 * Why this can't be done through the UI: `app/routers/channels.py`'s
 * `create_channel` only ever creates the bare `Channel` row -- it does
 * NOT add the creator (or anyone) as a `ChannelMember`, and both reading
 * and posting (`app/authorization.py`'s `authorize_channel_read`/
 * `authorize_post_message`) require channel membership. The web UI has no
 * "add member to channel" control yet (`MembersPanel` is read-only; a
 * settings surface for it is a later-branch backlog item, same gap the
 * TUI has). `tests/test_tui_e2e.py`'s own `_add_channel_member` helper
 * works around the exact same gap for its "mention in another channel"
 * scenario -- this is that same, sanctioned workaround, not a shortcut
 * around something the UI could otherwise do.
 */
async function addChannelMembers(
  request: APIRequestContext,
  opts: {
    workspaceId: string;
    token: string;
    channelName: string;
    memberHandles: string[];
  }
): Promise<void> {
  const auth = { Authorization: `Bearer ${opts.token}` };

  const channelsResp = await request.get(`${baseURL()}/workspaces/${opts.workspaceId}/channels`, {
    headers: auth,
  });
  if (!channelsResp.ok()) {
    throw new Error(`GET channels failed: ${channelsResp.status()} ${await channelsResp.text()}`);
  }
  const channels = (await channelsResp.json()) as { channel_id: string; channel_name: string }[];
  const channel = channels.find((c) => c.channel_name === opts.channelName);
  if (!channel) {
    throw new Error(`channel '${opts.channelName}' not found among ${JSON.stringify(channels)}`);
  }

  const membersResp = await request.get(`${baseURL()}/workspaces/${opts.workspaceId}/members`, {
    headers: auth,
  });
  if (!membersResp.ok()) {
    throw new Error(`GET members failed: ${membersResp.status()} ${await membersResp.text()}`);
  }
  const members = (await membersResp.json()) as { member_id: string; handle: string }[];

  for (const handle of opts.memberHandles) {
    const member = members.find((m) => m.handle === handle);
    if (!member) {
      throw new Error(`member with handle '${handle}' not found among ${JSON.stringify(members)}`);
    }
    const resp = await request.post(
      `${baseURL()}/workspaces/${opts.workspaceId}/channels/${channel.channel_id}/members`,
      { headers: auth, data: { member_id: member.member_id } }
    );
    if (!resp.ok()) {
      throw new Error(
        `failed to add '${handle}' to #${opts.channelName}: ${resp.status()} ${await resp.text()}`
      );
    }
  }
}

test.describe.serial("SMAC web UI journey: Alice → Bob, live mentions, mobile pass", () => {
  test("desktop: register, invite, join, live mention crossing, second-channel bell + mark-read", async ({
    browser,
    request,
  }) => {
    test.slow(); // real network round trips across two live browser contexts

    const aliceContext = await browser.newContext();
    const bobContext = await browser.newContext();
    const alice = await aliceContext.newPage();
    const bob = await bobContext.newPage();

    try {
      // -- Alice: register -> create her own workspace, lands in #general --
      await gotoApp(alice);
      await registerAccount(alice, ALICE_EMAIL);
      await createWorkspace(alice, {
        workspaceName: WORKSPACE_NAME,
        firstName: "Alice",
        lastName: "Anders",
      });

      // -- Alice: Settings -> mint a shareable invite code -----------------
      const inviteCode = await mintInviteCode(alice);
      await backToRoom(alice);
      await expect(alice.locator(".room__title")).toHaveText("#general");

      // -- Bob: register -> join by Alice's code -> lands in #general ------
      await gotoApp(bob);
      await registerAccount(bob, BOB_EMAIL);
      await joinByCode(bob, { firstName: "Bob", lastName: "Baker", code: inviteCode });

      // Alice's tab has had `workspace.members` (the composer @-autocomplete's
      // own source list) loaded since BEFORE Bob existed -- confirmed real
      // gap: `refreshMembers()` (state/workspace.tsx) is only ever invoked
      // from `AgentsPanel`'s own post-create/attach callback; there is no
      // "member joined" broadcast on the live layer (`lib/live.ts` only
      // carries message/mention events) and no focus/interval refetch for
      // the member directory the way `refreshUnreads` gets. A real user in
      // this situation reaches for the browser's own refresh, same as this
      // reload -- it re-mounts `WorkspaceProvider` and re-fetches the
      // member list fresh (now including Bob), which is the only thing
      // this step is proving; it has no bearing on the LIVE-delivery
      // assertion below, which is Bob's page and is never reloaded.
      await alice.reload();
      await expect(alice.locator(".room__title")).toHaveText("#general");

      // -- Alice mentions Bob (via the composer's @ autocomplete); Bob's
      //    tab -- never reloaded or re-navigated -- receives it LIVE ------
      await sendMentioning(alice, {
        before: "hey ",
        mentionHandle: BOB_HANDLE,
        after: " check this out",
      });
      await expect(
        alice.locator('[data-testid="message-line"]').filter({ hasText: "check this out" })
      ).toBeVisible();

      // No navigation/reload happens on Bob's page between the send above
      // and this assertion -- this is the load-bearing "live, not polled"
      // check: `toBeVisible()` only re-queries the existing DOM.
      const bobLiveMessage = bob
        .locator('[data-testid="message-line"]')
        .filter({ hasText: "check this out" });
      await expect(bobLiveMessage).toBeVisible({ timeout: 20_000 });
      await expect(bobLiveMessage).toHaveClass(/message-line--mention/);

      // -- Second-channel case: Alice creates #random -----------------------
      await alice.getByRole("button", { name: "Create channel" }).click();
      await alice.locator("#rail-new-channel-name").fill("random");
      await alice.getByRole("button", { name: "Create", exact: true }).click();
      await expect(alice.locator(".room__title")).toHaveText("#random");

      const aliceSession = await readWorkspaceSession(alice);
      await addChannelMembers(request, {
        workspaceId: aliceSession.workspaceId,
        token: aliceSession.token,
        channelName: "random",
        memberHandles: [ALICE_HANDLE, BOB_HANDLE],
      });

      // ... and mentions Bob there while Bob is still sitting in #general ---
      await sendMentioning(alice, {
        before: "yo ",
        mentionHandle: BOB_HANDLE,
        after: " over here",
      });
      await expect(
        alice.locator('[data-testid="message-line"]').filter({ hasText: "over here" })
      ).toBeVisible();

      // Bob never switched channels -- what follows proves the CROSS-channel
      // bell path (toast + rail badges), not just in-room delivery.
      await expect(bob.locator(".room__title")).toHaveText("#general");
      const bellToast = bob.getByText(/New mention in #random/);
      await expect(bellToast).toBeVisible({ timeout: 20_000 });
      const randomChannelButton = bob.locator("button.rail__channel").filter({ hasText: "#random" });
      await expect(randomChannelButton.locator(".rail__channel-bell")).toBeVisible();
      await expect(randomChannelButton.locator(".rail__channel-badge")).toBeVisible();

      // Clicking the toast lands Bob in #random; viewing it marks read,
      // which clears the plain UNREAD badge (`app/routers/unreads.py::mark_read`
      // advances `last_read_seq`, zeroing `unread_count`). The 🔔 mention
      // badge is a SEPARATE counter (`mention_count`, unacknowledged
      // `Mention` rows) that only clears via `POST /mentions/{id}/ack` --
      // an action neither the TUI nor this web client's human-facing
      // flows ever call (it's the MCP/agent inbox lifecycle,
      // `app/routers/mentions.py`); viewing a channel does not ack its
      // mentions in the current design, so the bell badge deliberately
      // is NOT asserted to clear here.
      await bellToast.click();
      await expect(bob.locator(".room__title")).toHaveText("#random");
      await expect(
        bob.locator('[data-testid="message-line"]').filter({ hasText: "over here" })
      ).toBeVisible();
      await expect(randomChannelButton.locator(".rail__channel-badge")).toHaveCount(0);
    } finally {
      await aliceContext.close();
      await bobContext.close();
    }
  });

  test("mobile viewport (390x844): Bob logs in, opens the drawer, reads, sends", async ({
    browser,
  }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    try {
      await gotoApp(page);
      await loginExisting(page, BOB_EMAIL);
      await expect(page.locator(".room__title")).toHaveText("#general");

      // The rail starts off-screen at this tier; the hamburger opens it.
      await page.getByRole("button", { name: "Open channels" }).click();
      const generalButton = page.locator("button.rail__channel").filter({ hasText: "#general" });
      await expect(generalButton).toBeVisible();
      await generalButton.click(); // tapping a room closes the drawer behind it

      // Read: the earlier live-crossed mention is still right there.
      await expect(
        page.locator('[data-testid="message-line"]').filter({ hasText: "check this out" })
      ).toBeVisible();

      // Send, from the thumb-anchored mobile composer.
      const composer = page.locator('textarea[aria-label="Message"]');
      await composer.click();
      await composer.pressSequentially("on mobile now");
      await composer.press("Enter");
      await expect(
        page.locator('[data-testid="message-line"]').filter({ hasText: "on mobile now" })
      ).toBeVisible();
    } finally {
      await context.close();
    }
  });
});
