# analyst_agent

A real, Anthropic-backed agent that joins a SMAC workspace as a member, answers when mentioned, and can be watched and talked to from your terminal. It's the first thing in this repo with an actual brain: SMAC deliberately routes triggers and owns no model logic, so the brain lives out here, in an example, where it belongs — and where you can see exactly how little glue code it takes to wire one up.

This is also a design driver for the `smac_client` SDK that follows it: `smac_link.py` is deliberately that SDK's rough draft.

## Quickstart

You need a running SMAC server with a workspace already founded (`smac-server --start`, then found one via `smac`, the web UI, or the [root README](../../README.md)'s API quickstart) and an Anthropic API key.

```bash
# From the repo root:
pip install -r examples/analyst_agent/requirements.txt
cp examples/analyst_agent/.env.example examples/analyst_agent/.env
```

Edit `examples/analyst_agent/.env`: paste your `ANTHROPIC_API_KEY`. Then mint the agent its invite code as your workspace's admin — in the SMAC web UI, **Settings → Invites → Agent code → Mint**, or with `curl` (there's currently no `smac` TUI command for this — only the web UI and the raw API mint `agent_code` invites):

```bash
curl -X POST http://127.0.0.1:8000/workspaces/<workspace_id>/invites \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <your access_token>' \
  -d '{"invite_type": "agent_code"}'
```

Paste the returned `code` into `.env` as `SMAC_AGENT_CODE`. Now join it — from the `examples/` directory (`analyst_agent` is a plain package, not installed):

```bash
cd examples
python -m analyst_agent.main --headless
```

This redeems the code (first run only — after that its key is saved locally and `SMAC_AGENT_CODE` is never read again) and starts listening. Leave it running; open a second terminal for the next step.

**Required one-time step:** `/agents/join` only makes the agent a member of the *workspace*, not of any channel — unlike a human, it is not auto-added to `#general` (see [Limitations](#limitations)). Look up its `member_id`, then add it:

```bash
curl "http://127.0.0.1:8000/members?search_name=Analyst" -H 'Authorization: Bearer <your access_token>'
# -> copy "member_id" from the result, then:
curl -X POST http://127.0.0.1:8000/workspaces/<workspace_id>/channels/<general_channel_id>/members \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <your access_token>' \
  -d '{"member_id": "<the agent'"'"'s member_id>"}'
```

Back in a SMAC client (`smac`, the web UI, or another agent), post `@analyst hello!` in `#general` — the still-running agent answers live, right there in its terminal's JSON-lines output (or run `python -m analyst_agent.main` without `--headless` for the two-pane view below).

## The view

```
┌─ analyst_agent · @analyst · Acme Rockets ──────────── SMAC ● connected ─┐
│ INNER VIEW                          │ DIRECT CHAT                      │
│ 10:06:41 ● mention  @aalice #ops    │ you › what did alice ask you?    │
│ 10:06:41   context   20 messages    │ agent › she asked for a summary  │
│ 10:06:41   ▸ model   claude-sonnet-5│   of the ignition test in #ops.  │
│ 10:06:49   ✓ done    1,204/312 8.1s │   I replied 8 seconds ago.       │
│ 10:06:49   → posted to #ops · acked │                                  │
│ 10:07:02 ⊘ skipped  rate cap 6/min  │ you › ▌                          │
├─────────────────────────────────────┴──────────────────────────────────┤
│ > talk to the agent…            F2 inner  F3 chat  F4 pause  F10 quit  │
└────────────────────────────────────────────────────────────────────────┘
```

Left pane: the inner activity stream — one timestamped line per bus event (mentions, guard skips, model calls collapsed to a summary, posts, acks). Right pane: your own direct chat with the agent's brain — a separate thread that never touches SMAC (see [Direct chat](#direct-chat-vs-the-mention-loop) below).

| Key | Does |
|---|---|
| `F2` | Inner view, full width. |
| `F3` | Direct chat, full width. |
| `F4` | Pause — stops answering SMAC mentions (still listens and logs every mention it would have handled, so you can demo the loop guard without unplugging the agent). |
| `F10` / `Ctrl-C` | Quit. |

### Direct chat vs. the mention loop

Typed input in the chat pane calls the agent's brain directly, on its own running thread — it can answer questions about SMAC activity it has personally seen ("what did Alice ask you?") from its own recollection of mentions it has answered, but it **never posts to SMAC, never issues a fresh SMAC read, and never mixes into channel context**. One brain, two conversations, one view over both.

### `--headless` / `--chat-only` / `--once`

```
python -m analyst_agent.main             # the two-pane terminal app (above)
python -m analyst_agent.main --headless  # no TUI: one JSON object per bus event, to stdout
python -m analyst_agent.main --chat-only # a plain stdin/stdout REPL over the brain only -- no mention loop, never touches SMAC
python -m analyst_agent.main --once      # handle exactly one mention, then exit (used by this package's own integration test)
```

`--headless` is the one to run unattended (a systemd unit, a container, a CI job) — same events the inner view renders, as JSON lines on stdout, no terminal required.

## Configuration

Everything lives in `examples/analyst_agent/.env` (copy of `.env.example`, gitignored — never committed): `ANTHROPIC_API_KEY` (required), `SMAC_URL`, `SMAC_AGENT_CODE` (only needed the first run), `AGENT_NAME` (default `Analyst`), `MODEL` (default `claude-sonnet-5`), `MAX_REPLIES_PER_MIN` / `MAX_HOPS` (citizenship-guard tuning, defaults 6 and 3 — note `MAX_HOPS` is currently inert and `MAX_REPLIES_PER_MIN` is the real loop protection, see [Limitations](#limitations)). A missing or malformed value fails fast at startup with the exact fix, never a traceback.

`SMAC_URL` defaults to `http://127.0.0.1:8000`, which is where `smac-server --start` listens when you don't pass `--port`. If you started the server on another port, `smac-server --status` prints the one it's actually using — put that in `.env`.

The agent's SMAC credentials, once obtained, are saved to `~/.config/analyst_agent/<agent-name>.json`, mode `0600` from the moment the file is created.

Dependencies (`examples/analyst_agent/requirements.txt`: `anthropic`, `httpx`, `websockets`, `textual`, `python-dotenv`) are installed separately from the server and never touch the server's own `pyproject.toml` — `pip install -e .` at the repo root still won't pull in `anthropic`.

## Limitations

Read this before you build on top of `analyst_agent` — every item below is a real, current boundary, not a hedge.

- **It only hears mentions while its socket is open.** The mention loop listens on a live WebSocket; if the process is down, any mentions that happened in the meantime sit in the agent's inbox (`GET /mentions`) until it reconnects, at which point it drains and answers them all. This is the same webhook gap recorded in the project's 2026-08-07 production-readiness review (SMAC-99, W4) — there is no push notification to a stopped process. Don't rely on this example for time-sensitive triggers without keeping the process supervised (e.g. `--headless` under systemd/a container restart policy).
- **It does not automatically join any channel.** `POST /agents/join` mints a *workspace* membership only — unlike a human founding or registering into a workspace, an agent/bot is a member of zero channels the moment it joins. A human has to explicitly add it (the `POST /workspaces/{id}/channels/{id}/members` call the quickstart above shows, or the equivalent in the web UI) before it can read history or post anywhere, `#general` included. Skip this step and every mention still creates a live event, but the agent's own history/post calls fail with "not a member of channel."
- **It cannot list workspace members.** An agent's API key is capped to post/read/ack — `GET /members` 403s for it, by design (agents can't enumerate who's in a workspace). Because of that, this example never has a handle to attach to a sender; the inner view and the model's context both show `Sender.member_name` (a display name), never `@handle`.
- **Agent-to-agent loop capping relies on the rate cap, not on counting hops.** `guard.py`'s citizenship rules include a "cap consecutive agent-to-agent turns" rule, but it needs to know which senders are agents versus humans, and that signal doesn't exist on the wire — a message's `Sender` is `member_id`/`member_name` only (see the bullet above). This example never feeds `guard.py`'s `known_agent_ids` set (an earlier version tried inferring it from "senders I've replied to," which wrongly counted a human's second and third mentions as agent-to-agent hops and eventually refused to answer them at all). A human can mention this agent as many times as they like and always get answered. The backstop against an actual runaway agent-to-agent loop is `MAX_REPLIES_PER_MIN` (default 6/min), not hop depth — tune that if you're demoing agent-to-agent chains.
- **Message content can attempt to steer it.** Everything this agent reads — channel history, the message that mentioned it, a chat pane message you type — is untrusted input as far as the model is concerned. `brain.py`'s system prompt includes a one-line prompt-injection hedge, but SMAC deliberately does not own agent brains or defend against this at the routing layer; treating what a model does with untrusted content as safe is the operator's responsibility, not something this example (or SMAC) can guarantee for you.
- **The terminal's control-byte sanitizer is a targeted defense, not a blanket one.** Every SMAC- or model-sourced string that reaches a real terminal — every TUI widget, `main.py`'s `ConfigError`/join-failure stderr prints, and the `--chat-only` REPL's banner and replies — is passed through `sanitize.py`'s `sanitize()` first: it strips/escapes raw control bytes (ESC included, so a message can't clear your screen or spoof your title bar), and it redacts secrets by **exact value** for the two keys this process actually holds (the SMAC API key, the Anthropic key), plus a `sk-ant-…` shaped heuristic on top. `--headless` mode gets the same guarantee for free from `json.dumps`, which escapes control bytes by spec. That is the whole guarantee. There is no general "any secret gets redacted" promise — an arbitrary token in an unrecognized shape renders as-is.

## Testing

```bash
# Unit + view + brain tests -- no ANTHROPIC_API_KEY needed:
python -m pytest examples/analyst_agent -q

# The full integration journey (spawns a real SMAC server) runs as part of
# the command above with no ANTHROPIC_API_KEY needed (a fake brain stands in
# for Anthropic). The `@pytest.mark.live` variant additionally exercises the
# real Anthropic API, and is skipped automatically without ANTHROPIC_API_KEY:
ANTHROPIC_API_KEY=sk-ant-... python -m pytest examples/analyst_agent -q -m live
```

See `tests/test_integration.py` for the end-to-end journey this README's quickstart mirrors: found a workspace, mint an agent code, join, add the agent to `#general`, mention it from a human, and assert the reply lands and the mention is acked — against a real, locally spawned server either way.
