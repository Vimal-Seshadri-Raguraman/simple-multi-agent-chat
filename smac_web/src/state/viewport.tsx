/**
 * The responsive-tier signal (web spec §1): `>=900px` is the desktop
 * three-pane layout (Rail + Room + Drawer, all persistent); `<900px` is
 * the mobile tier (rail -> swipe/tap drawer, right panel -> bottom
 * sheet, thumb-anchored composer -- `AuthedShell.tsx` wires the actual
 * layout switch, this module only decides WHICH tier applies).
 *
 * One `<ViewportProvider>` wraps the authed shell; every component that
 * needs to know the tier reads it via `useViewportTier()` rather than
 * querying `window.innerWidth` itself, so there is exactly one place
 * (`computeTier` below) a test needs to influence to exercise both
 * tiers -- `src/testing/viewportMock.ts`'s `setViewportWidth()` does
 * that by setting `window.innerWidth` and firing a `resize` event, which
 * is exactly what a real browser does on rotation/window-resize too.
 */

import { type ReactNode, createContext, useContext, useEffect, useState } from "react";

/** The spec's own breakpoint number (§1: "<900px = mobile tier"). */
export const MOBILE_BREAKPOINT_PX = 900;

export type ViewportTier = "desktop" | "mobile";

function computeTier(): ViewportTier {
  return window.innerWidth < MOBILE_BREAKPOINT_PX ? "mobile" : "desktop";
}

const ViewportContext = createContext<ViewportTier | null>(null);

export function ViewportProvider({ children }: { children: ReactNode }) {
  const [tier, setTier] = useState<ViewportTier>(computeTier);

  useEffect(() => {
    function onResize() {
      setTier(computeTier());
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return <ViewportContext.Provider value={tier}>{children}</ViewportContext.Provider>;
}

export function useViewportTier(): ViewportTier {
  const ctx = useContext(ViewportContext);
  if (ctx === null) {
    throw new Error("useViewportTier() must be called within a <ViewportProvider>");
  }
  return ctx;
}
