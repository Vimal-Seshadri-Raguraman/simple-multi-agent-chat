import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Feed from "../components/Feed";
import MessageLine from "../components/MessageLine";
import type { MemberOut, MessagePayload } from "../lib/api";
import { installScrollMetrics } from "../testing/scrollMock";

function makeMessage(overrides: Partial<MessagePayload> = {}): MessagePayload {
  return {
    timestamp: "2026-08-04T10:15:00",
    workspace: { workspace_id: "w1", workspace_name: "Acme" },
    Channel: { channel_id: "c1", channel_name: "general" },
    Sender: { member_id: "m1", member_name: "Alice Human" },
    Message: { message_id: "msg-1", message_text: "hello" },
    mentions: [],
    channel_refs: [],
    ...overrides,
  };
}

const HUMAN: MemberOut = {
  member_id: "m1",
  member_name: "Alice Human",
  member_type: "human",
  handle: "alice",
  created_at: "2026-01-01T00:00:00",
  account_id: "acc-1",
  role: "member",
};

const AGENT: MemberOut = {
  member_id: "m2",
  member_name: "Bot Agent",
  member_type: "agent",
  handle: "reportbot",
  created_at: "2026-01-01T00:00:00",
  account_id: "acc-2",
  role: "member",
};

describe("MessageLine (web spec §2 Room bullet)", () => {
  it("renders [HH:MM] @handle in mono, resolving the sender's handle from the members store", () => {
    render(<MessageLine payload={makeMessage()} memberById={{ [HUMAN.member_id]: HUMAN }} />);
    expect(screen.getByText("[10:15]")).toBeInTheDocument();
    expect(screen.getByText("@alice")).toBeInTheDocument();
  });

  it("tints an agent sender's handle (member type from the members store, not the payload)", () => {
    const payload = makeMessage({ Sender: { member_id: "m2", member_name: "Bot Agent" } });
    render(<MessageLine payload={payload} memberById={{ [AGENT.member_id]: AGENT }} />);
    const handle = screen.getByText("@reportbot");
    expect(handle.className).toContain("message-line__handle--agent");
  });

  it("does not tint a human sender's handle", () => {
    render(<MessageLine payload={makeMessage()} memberById={{ [HUMAN.member_id]: HUMAN }} />);
    const handle = screen.getByText("@alice");
    expect(handle.className).not.toContain("message-line__handle--agent");
  });

  it("resolves <@id> tokens to @handle chips via the payload's own mentions array", () => {
    const payload = makeMessage({
      Message: { message_id: "msg-2", message_text: "hey <@m2>, check this out" },
      mentions: [{ member_id: "m2", handle: "reportbot", member_name: "Bot Agent" }],
    });
    render(<MessageLine payload={payload} memberById={{}} />);
    const chip = screen.getByText("@reportbot");
    expect(chip.className).toContain("message-line__mention-chip");
    expect(screen.queryByText(/<@m2>/)).not.toBeInTheDocument();
  });

  it("leaves an unknown <@id> token exactly as literal text", () => {
    const payload = makeMessage({
      Message: { message_id: "msg-3", message_text: "ref <@does-not-exist>" },
      mentions: [],
    });
    render(<MessageLine payload={payload} memberById={{}} />);
    expect(screen.getByText(/ref <@does-not-exist>/)).toBeInTheDocument();
  });

  it("applies mention-bg to a line that mentions the current viewer", () => {
    const payload = makeMessage({
      Message: { message_id: "msg-4", message_text: "hi <@me>" },
      mentions: [{ member_id: "me", handle: "viewer", member_name: "Viewer" }],
    });
    const { container } = render(
      <MessageLine payload={payload} memberById={{}} currentMemberId="me" />
    );
    expect(container.querySelector(".message-line--mention")).not.toBeNull();
  });

  it("does not apply mention-bg when the viewer isn't mentioned", () => {
    const payload = makeMessage({ mentions: [] });
    const { container } = render(
      <MessageLine payload={payload} memberById={{}} currentMemberId="me" />
    );
    expect(container.querySelector(".message-line--mention")).toBeNull();
  });

  // -- Mandatory security test (task-3 brief / web spec §7.5) -----------
  it("[MANDATORY XSS TEST] renders an <img onerror>/<script> message body as inert literal text", () => {
    const payload = makeMessage({
      Message: {
        message_id: "msg-xss",
        message_text: '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>',
      },
    });
    const { container } = render(<MessageLine payload={payload} memberById={{}} />);

    // Never actually parsed as HTML -- no such elements exist in the DOM.
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    // The exploit never ran.
    expect((window as unknown as { __pwned?: unknown }).__pwned).toBeUndefined();
    // The literal characters ARE present, as ordinary text.
    expect(container.textContent).toContain(
      '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>'
    );
  });
});

describe("Feed (web spec §2 Room bullet: day dividers, scroll semantics)", () => {
  it("renders a day divider once per calendar day, not once per message", () => {
    const messages = [
      makeMessage({ timestamp: "2026-08-04T09:00:00", Message: { message_id: "a", message_text: "one" } }),
      makeMessage({ timestamp: "2026-08-04T09:05:00", Message: { message_id: "b", message_text: "two" } }),
      makeMessage({ timestamp: "2026-08-05T09:05:00", Message: { message_id: "c", message_text: "three" } }),
    ];
    const { container } = render(
      <Feed
        channelId="c1"
        messages={messages}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={vi.fn()}
        onView={vi.fn()}
      />
    );
    expect(container.querySelectorAll(".feed__day-divider")).toHaveLength(2);
  });

  it("auto-follows: a new message while at the bottom scrolls to bottom with no pill, and marks read", () => {
    const onView = vi.fn();
    const { rerender } = render(
      <Feed
        channelId="c1"
        messages={[makeMessage({ Message: { message_id: "1", message_text: "hi" } })]}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={vi.fn()}
        onView={onView}
      />
    );
    expect(onView).toHaveBeenCalledWith("c1");

    const scrollEl = screen.getByTestId("feed-scroll");
    installScrollMetrics(scrollEl, { scrollTop: 0, scrollHeight: 400, clientHeight: 400 });

    rerender(
      <Feed
        channelId="c1"
        messages={[
          makeMessage({ Message: { message_id: "1", message_text: "hi" } }),
          makeMessage({ Message: { message_id: "2", message_text: "there" } }),
        ]}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={vi.fn()}
        onView={onView}
      />
    );

    expect(screen.queryByText(/new below/)).not.toBeInTheDocument();
    expect(scrollEl.scrollTop).toBe(400);
  });

  it("pauses auto-follow on scroll-up, showing/growing an 'N new below ↓' pill", () => {
    const onView = vi.fn();
    const { rerender } = render(
      <Feed
        channelId="c1"
        messages={[makeMessage({ Message: { message_id: "1", message_text: "hi" } })]}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={vi.fn()}
        onView={onView}
      />
    );
    const scrollEl = screen.getByTestId("feed-scroll");
    installScrollMetrics(scrollEl, { scrollTop: 500, scrollHeight: 1000, clientHeight: 400 });
    fireEvent.scroll(scrollEl);

    rerender(
      <Feed
        channelId="c1"
        messages={[
          makeMessage({ Message: { message_id: "1", message_text: "hi" } }),
          makeMessage({ Message: { message_id: "2", message_text: "there" } }),
        ]}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={vi.fn()}
        onView={onView}
      />
    );
    expect(screen.getByText("1 new below ↓")).toBeInTheDocument();

    rerender(
      <Feed
        channelId="c1"
        messages={[
          makeMessage({ Message: { message_id: "1", message_text: "hi" } }),
          makeMessage({ Message: { message_id: "2", message_text: "there" } }),
          makeMessage({ Message: { message_id: "3", message_text: "world" } }),
        ]}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={vi.fn()}
        onView={onView}
      />
    );
    expect(screen.getByText("2 new below ↓")).toBeInTheDocument();

    fireEvent.click(screen.getByText("2 new below ↓"));
    expect(screen.queryByText(/new below/)).not.toBeInTheDocument();
  });

  it("loads older history when scrolled to the top and more is available", () => {
    const onLoadOlder = vi.fn();
    render(
      <Feed
        channelId="c1"
        messages={[makeMessage({ Message: { message_id: "1", message_text: "hi" } })]}
        memberById={{}}
        hasMoreOlder
        loadingOlder={false}
        onLoadOlder={onLoadOlder}
        onView={vi.fn()}
      />
    );
    const scrollEl = screen.getByTestId("feed-scroll");
    installScrollMetrics(scrollEl, { scrollTop: 0, scrollHeight: 1000, clientHeight: 400 });
    fireEvent.scroll(scrollEl);
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
  });

  it("does not trigger load-older when there's nothing more", () => {
    const onLoadOlder = vi.fn();
    render(
      <Feed
        channelId="c1"
        messages={[makeMessage({ Message: { message_id: "1", message_text: "hi" } })]}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={onLoadOlder}
        onView={vi.fn()}
      />
    );
    const scrollEl = screen.getByTestId("feed-scroll");
    installScrollMetrics(scrollEl, { scrollTop: 0, scrollHeight: 1000, clientHeight: 400 });
    fireEvent.scroll(scrollEl);
    expect(onLoadOlder).not.toHaveBeenCalled();
  });

  it("suppresses mark-read while scrolled away from the bottom", () => {
    const onView = vi.fn();
    render(
      <Feed
        channelId="c1"
        messages={[makeMessage({ Message: { message_id: "1", message_text: "hi" } })]}
        memberById={{}}
        hasMoreOlder={false}
        loadingOlder={false}
        onLoadOlder={vi.fn()}
        onView={onView}
      />
    );
    onView.mockClear();
    const scrollEl = screen.getByTestId("feed-scroll");
    installScrollMetrics(scrollEl, { scrollTop: 500, scrollHeight: 1000, clientHeight: 400 });
    fireEvent.scroll(scrollEl);
    expect(onView).not.toHaveBeenCalled();
  });
});
