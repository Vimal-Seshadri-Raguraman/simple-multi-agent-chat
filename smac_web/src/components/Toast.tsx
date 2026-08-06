import { useCallback, useRef, useState } from "react";

/**
 * The shared toast surface (task-4 brief). Two independent callers use
 * it: the bell (`AuthedShell.tsx`: another-room mention -> toast, click
 * routes to that room) and `VersionBanner.tsx`'s "SMAC updated --
 * refresh" nudge (task-2 brief's placeholder explicitly deferred its
 * real toast to this task). Each owns its own `useToastQueue()` instance
 * -- there is no cross-component toast bus, just one small reusable
 * queue + presentational stack, matching this codebase's "hand-rolled,
 * no extra state-management dependency" stance elsewhere.
 */

export type ToastItem = {
  id: string;
  message: string;
  /** Called (in addition to dismissing) when the toast body itself is clicked. */
  onClick?: () => void;
  /** Skips the auto-dismiss timer -- for a toast the reader should act on
   * (or explicitly dismiss) rather than have vanish on its own. */
  sticky?: boolean;
};

const AUTO_DISMISS_MS = 6000;

let nextToastId = 0;

export type ToastQueue = {
  toasts: ToastItem[];
  push: (message: string, opts?: { onClick?: () => void; sticky?: boolean }) => string;
  dismiss: (id: string) => void;
};

export function useToastQueue(): ToastQueue {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (message: string, opts: { onClick?: () => void; sticky?: boolean } = {}): string => {
      const id = `toast-${nextToastId++}`;
      setToasts((current) => [
        ...current,
        { id, message, onClick: opts.onClick, sticky: opts.sticky },
      ]);
      if (!opts.sticky) {
        const timer = setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
        timers.current.set(id, timer);
      }
      return id;
    },
    [dismiss]
  );

  return { toasts, push, dismiss };
}

export type ToastProps = {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
};

/** The visual stack, bottom-right (bottom-safe-area-aware on the mobile
 * tier via `shell.css`'s `@media (max-width: 899px)` rule). Renders
 * `null` when empty rather than an empty fixed-position container. */
export default function Toast({ toasts, onDismiss }: ToastProps) {
  if (toasts.length === 0) {
    return null;
  }
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div className="toast" key={toast.id}>
          <button
            type="button"
            className="toast__body"
            onClick={() => {
              toast.onClick?.();
              onDismiss(toast.id);
            }}
          >
            {toast.message}
          </button>
          <button
            type="button"
            className="toast__dismiss"
            aria-label="Dismiss"
            onClick={() => onDismiss(toast.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
