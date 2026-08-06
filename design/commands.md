# SMAC command contract

Constitution artifact (`docs/superpowers/specs/2026-08-04-smac-design-system.md` §4).
This table is the normative, canonical list of SMAC commands: every surface
(Terminal, Web, Desktop, Mobile) implements the SAME names with the SAME
one-line help. Surface affordance differs (pull-up vs. Cmd-K palette vs.
sheet); the grammar never does. Each surface's command registry is tested
against this table (the drift guard) — do not fork it per surface.

| Command | Args | Help |
|---|---|---|
| `/register` | (none) | Create a new account (account-first, two-step) |
| `/login` | (none) | Log into your account and pick a workspace |
| `/workspace create` | `<name>` | Found a new workspace |
| `/workspace delete` | (none) | Delete the current workspace (typed confirmation) |
| `/join` | `<code>` | Redeem an invite code to join a workspace |
| `/invite` | (none) | Mint and copy an invite code |
| `/channel` | `<name>` | Switch to a channel |
| `/channel create` | `<name>` | Create a new channel |
| `/channels` | (none) | Show channels with unread/mention badges (alias: `/unreads`) |
| `/whoami` | (none) | Show your identity card |
| `/help` | (none) | List available commands |
| `/quit` | (none) | Leave the app |
