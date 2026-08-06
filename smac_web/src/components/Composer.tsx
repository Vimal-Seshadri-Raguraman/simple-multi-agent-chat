import { type ChangeEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { errorMessage } from "../lib/errors";
import type { ChannelOut, MemberOut } from "../lib/api";
import Autocomplete, { type AutocompleteItem } from "./Autocomplete";

/**
 * The bottom-anchored message composer (web spec §2). Three behaviors
 * layered on one `<textarea>`, in priority order every keystroke checks:
 *
 *  1. A leading `/` hands off to the command palette entirely --
 *     `onOpenPalette` is called with whatever follows the `/` as the
 *     palette's prefilter (task-3 brief: "prefiltered by what follows"),
 *     and the composer's own draft is cleared so the palette owns input
 *     from that point on.
 *  2. `@`/`#` typed as the start of the CURRENT token (the run of non-
 *     whitespace characters ending at the caret) opens the members/
 *     channels `Autocomplete` popper -- ↑/↓ moves the highlight, Enter or
 *     a click selects (replacing the token with `@handle `/`#channel `),
 *     Esc closes it.
 *  3. Otherwise: Enter sends, Shift+Enter inserts a newline (the
 *     textarea's own default behavior, left untouched).
 *
 * A failed send (429 rate-limit or any other `SmacError`) shows the
 * server's own envelope message inline and LEAVES THE DRAFT TEXT ALONE --
 * the brief's "429 → toast + draft preserved" contract; nothing is ever
 * cleared on a failed send, only on a successful one.
 */

type AutocompleteState = {
  kind: "members" | "channels";
  /** Index into `draft` where the active `@`/`#` token starts. */
  tokenStart: number;
  query: string;
  activeIndex: number;
};

export type ComposerProps = {
  members: MemberOut[];
  channels: ChannelOut[];
  onSend: (text: string) => Promise<void>;
  onOpenPalette: (prefilter: string) => void;
  disabled?: boolean;
};

/** The `@`/`#` token (if any) whose run ends exactly at `cursor`. */
function activeTokenAt(value: string, cursor: number): { start: number; token: string } | null {
  const upToCursor = value.slice(0, cursor);
  const match = /(?:^|\s)([@#][^\s]*)$/.exec(upToCursor);
  if (!match) {
    return null;
  }
  const token = match[1];
  return { start: cursor - token.length, token };
}

export default function Composer({ members, channels, onSend, onOpenPalette, disabled }: ComposerProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [autocomplete, setAutocomplete] = useState<AutocompleteState | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const pendingCursorRef = useRef<number | null>(null);

  useEffect(() => {
    if (pendingCursorRef.current !== null && textareaRef.current) {
      const pos = pendingCursorRef.current;
      textareaRef.current.setSelectionRange(pos, pos);
      pendingCursorRef.current = null;
    }
  }, [draft]);

  const autocompleteItems: AutocompleteItem[] =
    autocomplete === null
      ? []
      : autocomplete.kind === "members"
        ? members
            .filter((m) => m.handle.toLowerCase().includes(autocomplete.query))
            .map((m) => ({ id: m.member_id, label: `@${m.handle}` }))
        : channels
            .filter((c) => c.channel_name.toLowerCase().includes(autocomplete.query))
            .map((c) => ({ id: c.channel_id, label: `#${c.channel_name}` }));

  function handleChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const value = event.target.value;
    const cursor = event.target.selectionStart ?? value.length;

    if (value.startsWith("/")) {
      onOpenPalette(value.slice(1));
      setDraft("");
      setAutocomplete(null);
      return;
    }

    setDraft(value);
    setError(null);

    const active = activeTokenAt(value, cursor);
    if (active && active.token.length >= 1) {
      const kind = active.token[0] === "@" ? "members" : "channels";
      setAutocomplete({ kind, tokenStart: active.start, query: active.token.slice(1).toLowerCase(), activeIndex: 0 });
    } else {
      setAutocomplete(null);
    }
  }

  function selectAutocompleteItem(item: AutocompleteItem) {
    if (!autocomplete) return;
    const cursor = textareaRef.current?.selectionStart ?? draft.length;
    const before = draft.slice(0, autocomplete.tokenStart);
    const after = draft.slice(cursor);
    const insertion = `${item.label} `;
    const newDraft = `${before}${insertion}${after}`;
    pendingCursorRef.current = before.length + insertion.length;
    setDraft(newDraft);
    setAutocomplete(null);
    textareaRef.current?.focus();
  }

  async function handleSend() {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      await onSend(text);
      setDraft("");
    } catch (err) {
      // Draft intentionally NOT cleared -- the brief's "draft preserved" contract.
      setError(errorMessage(err));
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (autocomplete && autocompleteItems.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setAutocomplete((a) => (a ? { ...a, activeIndex: (a.activeIndex + 1) % autocompleteItems.length } : a));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setAutocomplete((a) =>
          a ? { ...a, activeIndex: (a.activeIndex - 1 + autocompleteItems.length) % autocompleteItems.length } : a
        );
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        selectAutocompleteItem(autocompleteItems[autocomplete.activeIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setAutocomplete(null);
        return;
      }
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
    // Shift+Enter: no special handling -- the textarea's own default
    // behavior (insert a newline) is exactly what's wanted.
  }

  return (
    <div className="composer">
      {autocomplete && (
        <Autocomplete
          kind={autocomplete.kind}
          items={autocompleteItems}
          activeIndex={autocomplete.activeIndex}
          onHover={(index) => setAutocomplete((a) => (a ? { ...a, activeIndex: index } : a))}
          onSelect={selectAutocompleteItem}
        />
      )}
      {error && (
        <div className="composer__error" role="alert">
          {error}
        </div>
      )}
      <div className="composer__row">
        <textarea
          ref={textareaRef}
          className="composer__input"
          aria-label="Message"
          value={draft}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Message… ( / for commands, @ to mention, # to link a channel )"
          disabled={disabled || sending}
          rows={1}
        />
        <button
          type="button"
          className="composer__send"
          onClick={() => void handleSend()}
          disabled={disabled || sending || draft.trim().length === 0}
        >
          Send
        </button>
      </div>
    </div>
  );
}
