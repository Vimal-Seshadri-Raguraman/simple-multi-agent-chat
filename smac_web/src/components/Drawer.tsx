import type { ReactNode } from "react";

/**
 * The right, toggleable drawer (web spec §2). This task's only panel is
 * `MembersPanel` (the Agents panel from constitution §6's Settings-
 * adjacent surface is a later task); `Drawer` itself is just the
 * slide-in shell + close affordance so a later panel can be added
 * without touching this file.
 *
 * **Mobile tier (task-4 brief, web spec §1):** the same panel becomes a
 * bottom sheet instead of a right-side column when `mobile` is true
 * (`shell.css`'s `.drawer--sheet`), with a backdrop that closes it on
 * click -- the desktop drawer needs no backdrop (clicking elsewhere in
 * the three-pane layout doesn't imply "close the members panel"), but a
 * full-width sheet covering the room does.
 */
export type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  /** Mobile tier active (<900px). Default `false` -- desktop callers/tests unaffected. */
  mobile?: boolean;
};

export default function Drawer({ open, onClose, title, children, mobile = false }: DrawerProps) {
  if (!open) {
    return null;
  }
  return (
    <>
      {mobile && (
        <div className="drawer__backdrop" data-testid="drawer-backdrop" onClick={onClose} />
      )}
      <aside className={mobile ? "drawer drawer--sheet" : "drawer"} aria-label={title}>
        <div className="drawer__header">
          <span className="drawer__title">{title}</span>
          <button type="button" className="drawer__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="drawer__body">{children}</div>
      </aside>
    </>
  );
}
