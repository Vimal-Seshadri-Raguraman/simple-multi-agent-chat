import { describe, expect, it } from "vitest";
import { Cap, ROLE_LABELS, hasCap } from "../lib/capabilities";

/**
 * Unit coverage for `lib/capabilities.ts`'s pure helpers (SMAC-92 Task 4):
 * `hasCap` is the one function every capability-gated render (Settings'
 * tabs, the palette's dimming, `AgentsPanel`'s read-only mode) goes
 * through, so its edge cases (missing/empty capability lists) are worth
 * pinning down in isolation from any component.
 */
describe("hasCap", () => {
  it("returns true when the capability is present", () => {
    expect(hasCap([Cap.MANAGE_WORKSPACE, Cap.POST], Cap.MANAGE_WORKSPACE)).toBe(true);
  });

  it("returns false when the capability is absent", () => {
    expect(hasCap([Cap.POST, Cap.READ], Cap.MANAGE_WORKSPACE)).toBe(false);
  });

  it("returns false for an empty capability list", () => {
    expect(hasCap([], Cap.POST)).toBe(false);
  });

  it("returns false (never throws) when capabilities is null or undefined -- whoami hasn't resolved yet", () => {
    expect(hasCap(null, Cap.POST)).toBe(false);
    expect(hasCap(undefined, Cap.POST)).toBe(false);
  });
});

describe("ROLE_LABELS", () => {
  it("maps admin/agent_admin to their UI display names", () => {
    expect(ROLE_LABELS.admin).toBe("Workspace Admin");
    expect(ROLE_LABELS.agent_admin).toBe("Agent Admin");
  });

  it("has no entry for the baseline 'member' role -- plain members get no badge", () => {
    expect(ROLE_LABELS.member).toBeUndefined();
  });
});
