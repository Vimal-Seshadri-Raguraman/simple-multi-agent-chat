/**
 * Test helper (mirrors `testing/scrollMock.ts`'s role for `Feed`): drives
 * `state/viewport.tsx`'s tier signal from a test without needing JSDOM to
 * do any real layout. JSDOM never repaints or applies media queries, so
 * there is no "make the window smaller and CSS reacts" to test against --
 * this sets the one primitive `computeTier()` actually reads
 * (`window.innerWidth`) and fires the same `resize` event a real browser
 * fires on a window resize/rotation, exercising the exact listener
 * `ViewportProvider` installs.
 */
export function setViewportWidth(px: number): void {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: px,
  });
  window.dispatchEvent(new Event("resize"));
}
