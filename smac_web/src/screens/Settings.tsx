/**
 * Placeholder Settings screen (web spec §2: "the administration home").
 * The real Agents/Invites/Workspace panels are Task 5's job -- this task
 * only needs a navigable target for the command-palette entries that
 * point at flows not built yet (`/workspace create`, `/workspace delete`,
 * `/invite`, `/join` -- see `lib/commands.ts`'s module docstring), per
 * the task-3 brief's explicit allowance for a minimal stub screen here.
 * Deliberately does nothing else -- do not add real Settings features to
 * this file; replace it wholesale in Task 5.
 */
export type SettingsProps = {
  onBack: () => void;
};

export default function Settings({ onBack }: SettingsProps) {
  return (
    <div className="settings-stub">
      <h1>Settings</h1>
      <p>Agents, invites, and workspace administration are arriving in T5.</p>
      <button type="button" onClick={onBack}>
        Back to the room
      </button>
    </div>
  );
}
