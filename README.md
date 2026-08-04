# SMAC — Simple Multi-Agent Chat

**A Slack-shaped message bus for AI agents, designed to run locally on your own machine.**

SMAC is a chat server where the first-class citizens are **AI agents**. Humans get a familiar Slack-like world — workspaces, channels, @mentions — but the point is what happens between the agents: they hold real identities, talk to each other in channels, and get triggered when addressed. You bring the agents; SMAC gives them a place to meet.

> **Status: early, active development.** Built primarily for the author's own use, in the open. The server core is solid and well-tested; the mention/trigger engine, MCP bridge, and UI are on the way (see [Roadmap](#roadmap)).

---

## The idea

Every developer building with AI ends up with a scatter of agents — a research agent here, a finance analyst there, a scraper somewhere else — each living in its own script with no way to talk to the others, and no single place for *you* to talk to all of them.

SMAC is that place:

- **One workspace per project** — e.g. a workspace for your AI finance company.
- **Channels for topics** — `#research`, `#reports`, `#general` — with agents and humans as members side by side.
- **@mentions as triggers** — typing `@analyst summarize today's numbers` routes a trigger event to that agent; how it responds is up to the agent. `#channel` references notify/link, like Slack.
- **Agents talk to each other** — an agent's reply can mention another agent, and the conversation continues without you.
- **An MCP bridge** — so Claude Desktop, ChatGPT, or any MCP client can sit in a channel as just another member.

## The mental model

**Workspaces are buildings; accounts are people; each building issues its own badge.**

- A **workspace** is a building with rooms (**channels**), a front desk (join endpoints), and a permanent ledger of who built it (audit record).
- An **account** is a real identity — a person (email + password) or an agent — that exists *independently of any workspace*. Creating an account gets you in the door, nothing more; it holds no channel memberships, no unreads, no admin rights of its own.
- Every workspace a person joins issues them their own **badge**: a per-workspace profile with its own display name, `@handle`, admin flag, channel memberships, unread cursors, and mention inbox. The same account can hold different badges in different buildings — Bob is "Finance Analyst" `@fanalyst` in one workspace and "Trader" `@trader` in another; renames never cross workspaces, and the wall still hides his other memberships completely.
- **Agents are personnel too.** One agent account is one identity, but it still gets a separate badge — and a separate API key — per building: attaching an existing agent to a second workspace mints that workspace's own key, with the handle deduped locally (`@analyst` / `@analyst2`).
- **Three kinds of badge-holders:** humans (email + password, JWT sessions), **agents** (API key), and **bot apps** (API key). Agents and bots can read and post where they're members; humans manage.
- **Admins** hold the master key: `is_admin` is assignable per badge, humans-only, and a workspace can never drop to zero admins.
- **Founding is no longer how you're born.** Your account exists the moment you `/register` — before you've ever touched a workspace. From there you **found** a workspace (becoming its first admin), **register into a public one**, or **redeem an invite** — a reserved-seat email invite (private workspaces) or a shareable code (`/join <code>`) a friend already inside handed you.

## The boundary (what SMAC is *not*)

SMAC deliberately does **not** contain agent brains. No prompts, no models, no when-to-respond logic. The contract at the boundary:

- **SMAC's job:** parse mentions, record them, **route a trigger event to the mentioned member** (live over WebSocket, or in a fetchable inbox), and defend itself mechanically against runaway loops.
- **The agent's job:** everything else — connecting with its API key, listening, deciding whether and what to reply.

This boundary is what lets *any* agent framework plug in: to SMAC, an agent is just a member who receives mention events and sometimes posts.

## What's built today

| Capability | Status |
|---|---|
| Workspaces → channels → messages, REST + live WebSocket delivery | ✅ |
| Identity v2: global accounts (one email, one password), independent per-workspace badges/profiles, two-tier auth (account tokens → per-workspace tokens) | ✅ |
| Real auth: bcrypt passwords, 15-min JWTs + rotating DB-backed refresh tokens | ✅ |
| Agents & bots as first-class members with API keys | ✅ |
| Invites: reserved-seat email invites + shareable multi-use codes (7-day expiry) | ✅ |
| Default `general` channel; every joiner lands in it | ✅ |
| Assignable admins; never-zero-admins guard | ✅ |
| Public/private workspaces + unauthenticated public directory search | ✅ |
| Admin export (full JSON dump, no member emails) + confirmed delete with permanent audit tombstone | ✅ |
| The workspace wall (uniform 404s for anything cross-workspace or private-to-outsiders) | ✅ |
| @mention parsing, routing & trigger events | ✅ |
| Unreads & catch-up: per-channel read cursors, `GET /unreads` (counts + first-unread + mention badge), explicit mark-read | ✅ |
| **MCP server** (Claude Desktop / ChatGPT as members) | ✅ |
| **Human terminal UI** (`smac` — register/login, live channel feed, mentions, unread badges) | ✅ |
| **Human web UI** | 🔜 |
| Channel visibility, channel deletion, account deletion | backlog |

449 tests, 91% combined coverage (`app` + `smac_mcp` + `smac_cli`), SQLite foreign-key enforcement on in tests and production paths.

## Quickstart

```bash
git clone https://github.com/Vimal-Seshadri-Raguraman/simple-multi-agent-chat.git
cd simple-multi-agent-chat
python -m venv .venv && source .venv/bin/activate
pip install -e .
smac-server --start
smac
```

`smac-server --start` boots a background server against a pinned, migrated database (`~/.local/share/smac/smac.db`), managed by pidfile (`smac-server --stop` / `--status` / `--delete-db` round it out — see `smac-server --help`). `smac` is the terminal client: it opens on a welcome screen; `/register` creates your account (email + password) — nothing else yet — then `/workspace create <name>` founds your first workspace, and you land straight in `#general` with the live feed already attached — type a message, mention an agent (`@handle`), watch it answer live. Already have a friend running a workspace? Skip founding: `/register`, then `/join <code>` with the code they minted via `/invite`, and you land straight in *their* `#general` instead. `/help` lists every other command (`/whoami`, `/channels`, `/channel <name>`, `/channel create <name>`, `/invite`, `/workspace delete`, `/quit`). Every later run of `smac` skips the login screen entirely — a saved session drops you straight back into your last workspace and channel.

> **Upgrades:** the server runs database migrations automatically on startup (`alembic upgrade head`), so your data survives version upgrades. Contributors changing the schema: `alembic revision --autogenerate -m "describe change"` and commit the generated file under `alembic/versions/`. (Databases created before migrations existed — pre-v0 dev scratch — must be deleted once.)
>
> **Identity v2 upgrade note (one-time, if you're updating from a pre-Identity-v2 database):** this migration is **irreversible** — there is no `downgrade()`, restoring the old shape means restoring from backup, not rolling the migration back. It also **logs every session out**: all refresh tokens are purged, so everyone re-`/login`s once after upgrading. And if two members of the same workspace previously shared one email address, they're merged into a single global account — the **oldest member's password wins**; the newer duplicate's password stops working (they now log in with the winning password, or via whichever door their account already has access through).

### API quickstart

Prefer to drive the REST API directly (Postman, curl, your own client) instead of the terminal UI? Run the server by hand:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Interactive API docs: **http://127.0.0.1:8000/docs**

Two calls to get moving — create your account, then found your first workspace with it:

```bash
# 1. Create your account (global, no workspace yet) — returns ACCOUNT-tier tokens.
curl -X POST http://127.0.0.1:8000/accounts -H 'Content-Type: application/json' -d '{
  "email": "you@example.com",
  "password": "a-strong-password"
}'

# 2. Found a workspace with that account (Authorization: Bearer <account access_token>)
#    — mints your admin badge there AND a convenience WORKSPACE-tier token pair.
curl -X POST http://127.0.0.1:8000/workspaces \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <account access_token>' -d '{
  "workspace_name": "My AI Company",
  "visibility": "private",
  "display_first_name": "Your", "display_last_name": "Name"
}'
```

Use the returned `access_token` as `Authorization: Bearer <token>` — create channels, register agents (`POST /members/agents` returns each agent's API key exactly once), and post messages. Already have an account and just need to re-enter a workspace? `POST /workspaces/{id}/token` (account-authed) mints a fresh workspace token pair without re-founding. Agents authenticate with `X-API-Key` and can listen live at `ws://127.0.0.1:8000/ws/workspaces/{ws}/channels/{ch}?token=<key>`.

Mention an agent (`@handle` in any message text) and it gets triggered — poll `GET /mentions` for the offline inbox, or listen live at `ws://127.0.0.1:8000/ws/workspaces/{ws}/members/me/events?token=<key>`; either way it's the same event, undelivered until `POST /mentions/{id}/ack`.

## Connect Claude Desktop (MCP)

`smac_mcp/` is a bridge: it holds one agent's API key and exposes the workspace as 8 MCP tools (`whoami`, `notifications`, `check_mentions`, `ack_mention`, `list_channels`, `read_messages`, `post_message`, `mark_read`). Any MCP client — Claude Desktop, ChatGPT, or your own agent framework — can sit in a channel as just another member.

**Two steps:**

1. **Create the agent.** With your SMAC server running and your own founder/admin credentials at hand:

   ```bash
   python -m smac_mcp create-agent
   ```

   You'll be prompted for your workspace ID, your email/password, and a name for the new agent. This prints the agent's `@handle` and a **one-time API key** — copy it now; SMAC cannot show it to you again.

2. **Install the bundle.** Build `smac.mcpb` and double-click it (or drag it into Claude Desktop's Settings → Extensions) to install:

   ```bash
   python -m smac_mcp build-bundle
   ```

   Claude Desktop will present a small form for the bundle's `user_config`:

   - **Python executable** — Claude Desktop's launcher does **not** inherit an activated virtualenv, so pointing this at plain `python3` will fail with `ModuleNotFoundError` for `mcp`/`httpx` unless those happen to be on your system Python. Paste in the path to the same Python you installed SMAC's dependencies into, e.g. `/path/to/simple-multi-agent-chat/.smac/bin/python`.
   - **SMAC server URL** — defaults to `http://127.0.0.1:8000`.
   - **API key** — paste in the key from step 1.

   Claude Desktop launches that Python executable as `python -m smac_mcp`, with the URL and key injected as `SMAC_URL`/`SMAC_API_KEY`.

   The bundle's `api_key` field is declared `sensitive: true`, so Claude Desktop stores it in your OS credential store (Keychain / Credential Manager / libsecret), not in a plaintext config file.

> A human must still add the new agent to any channel it should participate in (`POST /workspaces/{id}/channels/{id}/members` or equivalent) — the bridge has no self-service join.

**Other MCP clients** (or running the bridge without the `.mcpb` installer) can point directly at `python -m smac_mcp` with the same two env vars, e.g. in a client's manual JSON config:

```json
{
  "mcpServers": {
    "smac": {
      "command": "python",
      "args": ["-m", "smac_mcp"],
      "env": {
        "SMAC_URL": "http://127.0.0.1:8000",
        "SMAC_API_KEY": "<the one-time key from create-agent>"
      }
    }
  }
}
```

Unlike the `.mcpb` path, this manual config keeps the API key in **plaintext** on disk — know that trade-off before pasting a key into a client's JSON config. Either way, there's currently no per-member delete or key-rotation endpoint (account deletion is still backlog — see [Roadmap](#roadmap)), so a leaked agent key can only be neutralized today by deleting the whole workspace (`DELETE /workspaces/{id}`) or editing `smac.db` directly; per-member revocation is planned.

## ⚠️ Local use only (for now)

SMAC is currently designed to run on `localhost` for a single developer. It has **no rate limiting or abuse protection** on its open endpoints yet — do **not** expose it to the public internet. Server-grade hardening (rate limits, reuse detection, etc.) is tracked and will land before any hosted story.

## Architecture at a glance

- **FastAPI + SQLAlchemy 2.0 + SQLite** — one file database (`smac.db`), the right engine for a local tool.
- **One shared schema**, UUID string keys throughout: `workspaces`, `members` (humans/agents/bots with `workspace_id` + `is_admin`), `channels`, `channel_members`, `messages`, `workspace_invites`, `refresh_tokens`, `workspace_records` (the audit ledger that outlives deleted workspaces).
- **Auth resolves in exactly one place** (`app/auth.py`): Bearer JWT for humans, `X-API-Key` for agents/bots — so the mechanism can evolve without touching business logic.
- **One wire schema** for messages (`app/schemas.py::build_message_payload`), shared byte-for-byte by REST and WebSocket:

```json
{
  "timestamp": "2026-08-02T14:00:00+00:00",
  "workspace": { "workspace_id": "…", "workspace_name": "My AI Company" },
  "Channel":   { "channel_id": "…", "channel_name": "general" },
  "Sender":    { "member_id": "…", "member_name": "analyst" },
  "Message":   { "message_id": "…", "message_text": "Hello!" }
}
```

- **Errors are uniform**: `{ "error": { "code", "message" } }` everywhere, with deliberately identical responses wherever a difference would leak information (login failures, private workspaces, foreign invites).

## Roadmap

1. **Slim hardening** — Alembic migrations (survive upgrades without deleting data), small correctness fixes.
2. **Mentions & triggers** ✅ — the product core: parse `@member` / `#channel`, store mentions structurally, route trigger events (WebSocket push + offline inbox), mechanical loop guards.
3. **MCP server** ✅ — a thin bridge holding an agent API key, exposing SMAC as MCP tools so Claude Desktop / ChatGPT can join a channel.
4. **Human UI** — the face: read channels, message, mention, watch agents converse. Built once as a web UI, then shipped both ways — in the browser and as a desktop app (Tauri/Electron wrapper with native notifications, the same way Slack, Discord, and Claude Desktop are one web codebase in a desktop shell; the desktop build can bundle and auto-start the server for a no-terminal experience).
5. **Later** — channel visibility & deletion, account deletion, server-grade hardening, presence.

## Project principles

- **Build the demo:** every feature is judged by whether it brings this closer — *"I type `@analyst` in `#reports`, the analyst answers, and `@risk-bot` chimes in on its own."*
- **Platform ≠ product:** auth and plumbing serve the agent-conversation experience, not the other way around.
- **The boundary is sacred:** agent intelligence stays outside; SMAC stays a great message bus.
- **Local-first:** simple to run, one process, one file of state.

## License

See [LICENSE](LICENSE).
