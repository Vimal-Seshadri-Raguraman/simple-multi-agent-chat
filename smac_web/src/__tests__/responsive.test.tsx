import type { ComponentProps } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Drawer from "../components/Drawer";
import Rail from "../components/Rail";
import Room from "../components/Room";
import { MOBILE_BREAKPOINT_PX, ViewportProvider, useViewportTier } from "../state/viewport";
import { setViewportWidth } from "../testing/viewportMock";

// Task-4 brief: "responsive tests toggle a mocked width provider (test
// the LOGIC switch, not pixels)". `state/viewport.tsx`'s hook is the one
// place that reads an actual pixel value (`window.innerWidth`) --
// everything downstream (Rail/Drawer/Room below) takes a plain `mobile`
// boolean prop and is tested with that prop directly, exactly like
// `rail.test.tsx` already tests `Rail` with plain props. This file's
// first `describe` block is the one place pixels come in at all.

function TierProbe() {
  const tier = useViewportTier();
  return <span data-testid="tier">{tier}</span>;
}

describe("useViewportTier (web spec §1: <900px = mobile)", () => {
  it("reports desktop at/above the breakpoint and mobile below it", () => {
    setViewportWidth(1024);
    render(
      <ViewportProvider>
        <TierProbe />
      </ViewportProvider>
    );
    expect(screen.getByTestId("tier")).toHaveTextContent("desktop");

    act(() => setViewportWidth(MOBILE_BREAKPOINT_PX)); // exactly 900 -- still desktop ("<900" is the mobile test)
    expect(screen.getByTestId("tier")).toHaveTextContent("desktop");

    act(() => setViewportWidth(MOBILE_BREAKPOINT_PX - 1)); // 899 -- the logic switch flips here
    expect(screen.getByTestId("tier")).toHaveTextContent("mobile");

    act(() => setViewportWidth(1200)); // back up past the breakpoint
    expect(screen.getByTestId("tier")).toHaveTextContent("desktop");
  });

  it("throws outside a <ViewportProvider> (fails loudly rather than silently defaulting)", () => {
    // Swallow the expected React error-boundary console noise for this one assertion.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<TierProbe />)).toThrow(/useViewportTier/);
    spy.mockRestore();
  });
});

const CHANNELS = [{ channel_id: "c1", channel_name: "general" }];

function renderRail(overrides: Partial<ComponentProps<typeof Rail>> = {}) {
  const props: ComponentProps<typeof Rail> = {
    workspaceName: "Acme",
    memberships: [],
    currentWorkspaceId: "w1",
    onSwitchWorkspace: vi.fn(),
    onCreateOrJoinAnother: vi.fn(),
    channels: CHANNELS,
    unreads: {},
    currentChannelId: "c1",
    onSelectChannel: vi.fn(),
    onCreateChannel: vi.fn(),
    self: null,
    youMenuOpen: false,
    onSetYouMenuOpen: vi.fn(),
    theme: "dark",
    onToggleTheme: vi.fn(),
    onLogout: vi.fn(),
    onRequestClose: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<Rail {...props} />) };
}

describe("Rail's mobile drawer mode (task-4 brief: rail -> swipe/tap drawer)", () => {
  it("desktop (mobile=false, the default): no backdrop, nav never aria-hidden", () => {
    renderRail();
    expect(screen.queryByTestId("rail-backdrop")).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { hidden: true })).not.toHaveAttribute("aria-hidden");
  });

  it("mobile + closed: the drawer is aria-hidden and shows no backdrop", () => {
    renderRail({ mobile: true, open: false });
    expect(screen.queryByTestId("rail-backdrop")).not.toBeInTheDocument();
    const nav = screen.getByRole("navigation", { hidden: true });
    expect(nav).toHaveAttribute("aria-hidden", "true");
    expect(nav.className).toContain("rail--drawer-closed");
  });

  it("mobile + open: shows a backdrop, and clicking it calls onRequestClose", () => {
    const { props } = renderRail({ mobile: true, open: true });
    const backdrop = screen.getByTestId("rail-backdrop");
    expect(screen.getByRole("navigation").className).toContain("rail--drawer-open");

    fireEvent.click(backdrop);
    expect(props.onRequestClose).toHaveBeenCalled();
  });

  it("mobile + open: a left swipe past the threshold calls onRequestClose", () => {
    const { props } = renderRail({ mobile: true, open: true });
    const nav = screen.getByRole("navigation");

    fireEvent.touchStart(nav, { touches: [{ clientX: 200 }] });
    fireEvent.touchEnd(nav, { changedTouches: [{ clientX: 100 }] }); // 100px left

    expect(props.onRequestClose).toHaveBeenCalled();
  });

  it("mobile + open: a short/rightward swipe does NOT close it", () => {
    const { props } = renderRail({ mobile: true, open: true });
    const nav = screen.getByRole("navigation");

    fireEvent.touchStart(nav, { touches: [{ clientX: 100 }] });
    fireEvent.touchEnd(nav, { changedTouches: [{ clientX: 110 }] }); // 10px right

    expect(props.onRequestClose).not.toHaveBeenCalled();
  });
});

describe("Drawer's mobile bottom-sheet mode (task-4 brief: right panel -> bottom sheet)", () => {
  it("desktop (mobile=false, the default): no backdrop, plain 'drawer' class", () => {
    render(
      <Drawer open onClose={vi.fn()} title="Members">
        <p>content</p>
      </Drawer>
    );
    expect(screen.queryByTestId("drawer-backdrop")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Members" }).className).toBe("drawer");
  });

  it("mobile: renders a backdrop and the sheet variant class; backdrop click closes it", () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="Members" mobile>
        <p>content</p>
      </Drawer>
    );
    expect(screen.getByRole("complementary", { name: "Members" }).className).toBe(
      "drawer drawer--sheet"
    );
    fireEvent.click(screen.getByTestId("drawer-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("Room's mobile hamburger (task-4 brief: the rail's open affordance once it's off-screen)", () => {
  const baseProps: ComponentProps<typeof Room> = {
    channel: { channel_id: "c1", channel_name: "general" },
    memberCount: 2,
    onOpenDrawer: vi.fn(),
    messages: [],
    memberById: {},
    hasMoreOlder: false,
    loadingOlder: false,
    onLoadOlder: vi.fn(),
    onView: vi.fn(),
    members: [],
    channels: CHANNELS,
    onSend: vi.fn().mockResolvedValue(undefined),
    onOpenPalette: vi.fn(),
  };

  it("desktop (mobile=false, the default): no hamburger button", () => {
    render(<Room {...baseProps} />);
    expect(screen.queryByLabelText("Open channels")).not.toBeInTheDocument();
  });

  it("mobile: a hamburger button appears and calls onOpenRail", () => {
    const onOpenRail = vi.fn();
    render(<Room {...baseProps} mobile onOpenRail={onOpenRail} />);
    fireEvent.click(screen.getByLabelText("Open channels"));
    expect(onOpenRail).toHaveBeenCalled();
  });
});
