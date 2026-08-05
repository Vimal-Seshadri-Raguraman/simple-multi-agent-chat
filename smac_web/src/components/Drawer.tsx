import type { ReactNode } from "react";

/**
 * The right, toggleable drawer (web spec §2). This task's only panel is
 * `MembersPanel` (the Agents panel from constitution §6's Settings-
 * adjacent surface is a later task); `Drawer` itself is just the
 * slide-in shell + close affordance so a later panel can be added
 * without touching this file.
 */
export type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
};

export default function Drawer({ open, onClose, title, children }: DrawerProps) {
  if (!open) {
    return null;
  }
  return (
    <aside className="drawer" aria-label={title}>
      <div className="drawer__header">
        <span className="drawer__title">{title}</span>
        <button type="button" className="drawer__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="drawer__body">{children}</div>
    </aside>
  );
}
