/**
 * JSDOM never actually lays anything out -- every scrollable element
 * reports `scrollTop`/`scrollHeight`/`clientHeight` as `0`, so `Feed`'s
 * auto-follow/pause/"N new below" scroll semantics (web spec §2) can't be
 * exercised by real scrolling. This installs a tiny writable/readable
 * trio of those three properties on a given element so a test can set
 * whatever metrics it wants, then `fireEvent.scroll(el)` to trigger
 * `Feed`'s scroll handler against them.
 *
 * Usage:
 *   const metrics = installScrollMetrics(el, { scrollTop: 0, scrollHeight: 500, clientHeight: 300 });
 *   metrics.set({ scrollTop: 0 }); // simulate the user scrolling to the top
 *   fireEvent.scroll(el);
 */

export type ScrollMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

export type ScrollMetricsHandle = {
  /** Overwrite one or more of the three metrics in place. */
  set(next: Partial<ScrollMetrics>): void;
  /** The metrics as they currently stand. */
  get(): ScrollMetrics;
};

export function installScrollMetrics(
  el: HTMLElement,
  initial: ScrollMetrics
): ScrollMetricsHandle {
  const state: ScrollMetrics = { ...initial };

  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => state.scrollTop,
    set: (value: number) => {
      state.scrollTop = value;
    },
  });
  Object.defineProperty(el, "scrollHeight", {
    configurable: true,
    get: () => state.scrollHeight,
  });
  Object.defineProperty(el, "clientHeight", {
    configurable: true,
    get: () => state.clientHeight,
  });

  return {
    set(next) {
      Object.assign(state, next);
    },
    get() {
      return { ...state };
    },
  };
}
