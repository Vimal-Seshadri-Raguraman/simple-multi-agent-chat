/**
 * Shared avatar-initials helper (SMAC-85 polish pass, work item 3: "avatar
 * chips ... in MessageLine and MembersPanel"). Two letters, derived from
 * the member's display name when there is one (first letter of the first
 * two words), falling back to the first two characters of the handle --
 * every member always HAS a handle, so this never returns an empty chip.
 */
export function initialsFor(name: string | undefined | null, handle: string): string {
  const trimmed = (name ?? "").trim();
  if (trimmed) {
    const words = trimmed.split(/\s+/).filter(Boolean);
    if (words.length >= 2) {
      return (words[0][0] + words[1][0]).toUpperCase();
    }
    if (words.length === 1 && words[0].length > 0) {
      return words[0].slice(0, 2).toUpperCase();
    }
  }
  return handle.slice(0, 2).toUpperCase();
}
