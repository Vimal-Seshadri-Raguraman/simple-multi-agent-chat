"""`sanitize()`: the control-byte / secret-token choke point every
string leaving this process toward a real terminal passes through.

INVARIANT (constitution §7.5; final-review fix wave F3+F4, closing the
security review's Vuln 1/2 findings): every string that leaves this
process toward a terminal goes through exactly one of two sanctioned
boundaries --

1. `sanitize()` (this module) for human-readable text -- every widget
   `tui.py` renders, and every plain `print()` in `main.py` (the
   `ConfigError`/`JoinFailed` stderr messages printed before any TUI
   exists, and the `--chat-only` REPL's handle banner and the model's
   replies).
2. `json.dumps` for `--headless`'s JSON-lines mode, which backslash-
   escapes every C0 control byte (ESC included) per the JSON spec on its
   own -- no `sanitize()` call is needed, or made, there.

There is no third path. A new print/write anywhere in this package of
text that ultimately came from SMAC (message bodies, sender/handle/
workspace names) or from the model (a reply, which can echo back
whatever untrusted content it was shown) must route through one of the
two boundaries above, or it reopens the exact hole a hostile/MITM'd
`SMAC_URL`, or an ordinary workspace member's message the model echoes
back, can exploit: raw control bytes (a title-bar spoof, a screen
clear/reposition, an OSC52 clipboard write) landing on the operator's
real terminal.

Lives here, not in `tui.py` (where an earlier version of this fix put
it): this function has zero Rich/Textual dependencies (pure `re`), but
`main.py` deliberately never imports `tui.py` eagerly -- the lazy-TUI
seam is load-bearing, so `--headless`/`--chat-only`/`--once` can run
without ever exercising `textual`. A dependency-free home lets `main.py`
import this module unconditionally, for every mode, while `tui.py`
imports from here too (its own call sites are unchanged).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Every C0 control byte except tab/newline/CR (those get the
#: whitespace-collapse policy below, not an escape), DEL, and every C1
#: control byte -- `\x00-\x08`, `\x0b-\x1f` (skips \t=09, \n=0a),
#: `\x7f-\x9f`. This deliberately includes ESC (0x1b), which is the byte
#: `rich.control.STRIP_CONTROL_CODES` (7, 8, 11, 12, 13) does NOT cover
#: -- see `tui.py`'s module docstring for the full threat model.
_CONTROL_BYTE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

#: A residual heuristic, NOT the primary defense -- see `sanitize()`'s
#: docstring. `sk-ant-` is the one prefix worth matching by shape alone:
#: it's Anthropic's real key format, and it can show up in text this
#: process didn't generate itself (e.g. a key the model echoes back in a
#: reply, or an operator pasting one into chat). There used to be a
#: `smac-` branch here too, on the theory that it caught "this project's
#: own SMAC keys" -- it did not: `app/auth.py`'s `generate_api_key()`
#: returns a bare `secrets.token_urlsafe(32)` string, no prefix, nothing
#: this regex could ever match. The actual defense for the SMAC key (and
#: for the Anthropic key, redundantly with the rule below) is exact-value
#: redaction: see `sanitize()`'s `known_secrets` parameter.
_SECRET_TOKEN = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{4,}\b")

_REDACTED = "[REDACTED]"


def sanitize(text: str, known_secrets: Iterable[str] = ()) -> str:
    """Neutralize `text` before it reaches a terminal. Every string that
    ultimately came from SMAC (message bodies, sender/handle/workspace
    names -- anything that traces back to `SMAC_URL`, which is untrusted)
    or from the model (which can echo back whatever untrusted content it
    was shown) MUST pass through this before a `print()`/`Text.append()`/
    `Static.update()` call ever sees it.

    `known_secrets` is the set of secret VALUES this process actually
    holds at runtime -- e.g. `agent.link.credentials.api_key` and
    `agent.config.anthropic_api_key`. This is the ONLY redaction here
    with a real guarantee attached to it: if a known secret's exact
    value appears anywhere in `text`, it is replaced. Everything else in
    this function -- the `sk-ant-` prefix rule included -- is a
    heuristic that happens to catch some shapes and definitely misses
    others. There is no general "any secret, known or not, gets
    redacted" guarantee; an arbitrary unknown token in an unrecognized
    shape renders as-is.

    Policy, applied in order:

    0. Every non-empty, non-whitespace-only value in `known_secrets` is
       replaced, by exact substring match, with `[REDACTED]`. An empty
       or whitespace-only entry is skipped entirely rather than matched
       -- `text.replace("", "[REDACTED]")` would insert the replacement
       between every character, corrupting the whole output, and an
       empty "secret" is never a real one (e.g. a credential that
       hasn't been obtained yet).
    1. Tab and newline (`\\t`, `\\n`, `\\r\\n`, `\\r`) collapse to a
       single space. The TUI's panes are line-oriented (one
       `RichLog.write()` per event) and the REPL is line-oriented too
       (one `print()` per exchange) -- a raw newline in SMAC-or-model-
       sourced text would otherwise split one line across several, a
       collapsed space keeps the one-line-per-thing invariant intact
       without losing the text.
    2. Every other C0 control byte (0x00-0x08, 0x0b-0x1f), DEL (0x7f),
       and every C1 control byte (0x80-0x9f) -- ESC (0x1b) included --
       is replaced with its visible `\\xHH` escape, not silently
       dropped. Visible-but-inert beats invisible-but-gone: an
       attempted injection (a title-bar spoof, a screen clear, an
       OSC52 clipboard write) shows up as harmless literal text instead
       of vanishing without a trace.
    3. An Anthropic-shaped token (`sk-ant-...` -- see `_SECRET_TOKEN`)
       is replaced with `[REDACTED]`, belt-and-braces on top of step 0
       for a key this process doesn't happen to hold by value.
    """
    for secret in known_secrets:
        if secret and secret.strip():
            text = text.replace(secret, _REDACTED)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", " ").replace("\t", " ")
    text = _CONTROL_BYTE.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
    return _SECRET_TOKEN.sub(_REDACTED, text)
