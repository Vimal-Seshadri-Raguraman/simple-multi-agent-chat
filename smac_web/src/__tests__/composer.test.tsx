import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Composer from "../components/Composer";
import { RateLimitedError } from "../lib/errors";
import type { ChannelOut, MemberOut } from "../lib/api";

const MEMBERS: MemberOut[] = [
  {
    member_id: "m1",
    member_name: "Alice Human",
    member_type: "human",
    handle: "alice",
    created_at: "2026-01-01T00:00:00",
    account_id: "acc-1",
    role: "member",
  },
  {
    member_id: "m2",
    member_name: "Report Bot",
    member_type: "agent",
    handle: "reportbot",
    created_at: "2026-01-01T00:00:00",
    account_id: "acc-2",
    role: "member",
  },
];

const CHANNELS: ChannelOut[] = [
  { channel_id: "c1", channel_name: "general" },
  { channel_id: "c2", channel_name: "random" },
];

describe("Composer contract (web spec §2)", () => {
  it("a leading '/' hands off to the palette, prefiltered by whatever follows, and clears the draft", () => {
    const onOpenPalette = vi.fn();
    render(
      <Composer members={MEMBERS} channels={CHANNELS} onSend={vi.fn()} onOpenPalette={onOpenPalette} />
    );
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "/chan" } });
    expect(onOpenPalette).toHaveBeenCalledWith("chan");
    expect(input).toHaveValue("");
  });

  it("'@' opens the members autocomplete, filtered by what follows", () => {
    render(
      <Composer members={MEMBERS} channels={CHANNELS} onSend={vi.fn()} onOpenPalette={vi.fn()} />
    );
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "hey @rep" } });
    expect(screen.getByText("@reportbot")).toBeInTheDocument();
    expect(screen.queryByText("@alice")).not.toBeInTheDocument();
  });

  it("selecting a member autocomplete entry (click) inserts the handle into the draft", () => {
    render(
      <Composer members={MEMBERS} channels={CHANNELS} onSend={vi.fn()} onOpenPalette={vi.fn()} />
    );
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "hey @rep" } });
    fireEvent.mouseDown(screen.getByText("@reportbot"));
    expect(input).toHaveValue("hey @reportbot ");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("'#' opens the channels autocomplete, and Enter selects the highlighted entry", () => {
    render(
      <Composer members={MEMBERS} channels={CHANNELS} onSend={vi.fn()} onOpenPalette={vi.fn()} />
    );
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "see #ran" } });
    expect(screen.getByText("#random")).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).toHaveValue("see #random ");
  });

  it("ArrowDown/ArrowUp move the autocomplete highlight", () => {
    const withThirdMember = [
      ...MEMBERS,
      {
        member_id: "m3",
        member_name: "Alicia",
        member_type: "human",
        handle: "aliciab",
        created_at: "2026-01-01T00:00:00",
        account_id: "acc-3",
        role: "member",
      },
    ];
    render(
      <Composer members={withThirdMember} channels={CHANNELS} onSend={vi.fn()} onOpenPalette={vi.fn()} />
    );
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "@ali" } });
    // Matches @alice and @aliciab -- @alice is first/active by default.
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).toHaveValue("@aliciab ");
  });

  it("Escape closes the autocomplete without altering the draft", () => {
    render(
      <Composer members={MEMBERS} channels={CHANNELS} onSend={vi.fn()} onOpenPalette={vi.fn()} />
    );
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "hey @rep" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(input).toHaveValue("hey @rep");
  });

  it("Enter sends the message and clears the draft on success", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<Composer members={MEMBERS} channels={CHANNELS} onSend={onSend} onOpenPalette={vi.fn()} />);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "hello there" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello there");
    await screen.findByDisplayValue("");
  });

  it("Shift+Enter inserts a newline instead of sending", () => {
    const onSend = vi.fn();
    render(<Composer members={MEMBERS} channels={CHANNELS} onSend={onSend} onOpenPalette={vi.fn()} />);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "line one" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("a 429 shows the server's message inline and PRESERVES the draft", async () => {
    const onSend = vi.fn().mockRejectedValue(
      new RateLimitedError("rate_limited", "Slow down — you're posting too fast.")
    );
    render(<Composer members={MEMBERS} channels={CHANNELS} onSend={onSend} onOpenPalette={vi.fn()} />);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "spam spam spam" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent(/slow down/i);
    expect(input).toHaveValue("spam spam spam");
  });
});
