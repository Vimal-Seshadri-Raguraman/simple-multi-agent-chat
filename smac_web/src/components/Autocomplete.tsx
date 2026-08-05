/**
 * The `@`/`#` popper `Composer.tsx` renders below the caret's active
 * token (web spec §2's Composer bullet): a plain listbox, keyboard- and
 * click-selectable. Deliberately dumb -- `Composer` owns the filtering,
 * the active index, and what "select" does; this component only ever
 * renders whatever list it's handed and reports hover/select back up.
 */

export type AutocompleteItem = { id: string; label: string };

export type AutocompleteProps = {
  kind: "members" | "channels";
  items: AutocompleteItem[];
  activeIndex: number;
  onHover: (index: number) => void;
  onSelect: (item: AutocompleteItem) => void;
};

export default function Autocomplete({ kind, items, activeIndex, onHover, onSelect }: AutocompleteProps) {
  if (items.length === 0) {
    return null;
  }
  return (
    <ul className="autocomplete" role="listbox" aria-label={kind === "members" ? "Mention a member" : "Link a channel"}>
      {items.map((item, index) => (
        <li
          key={item.id}
          role="option"
          aria-selected={index === activeIndex}
          className={
            index === activeIndex ? "autocomplete__item autocomplete__item--active" : "autocomplete__item"
          }
          onMouseEnter={() => onHover(index)}
          // onMouseDown (not onClick) fires before the textarea blurs, so
          // selecting an item never loses focus/cursor context.
          onMouseDown={(event) => {
            event.preventDefault();
            onSelect(item);
          }}
        >
          {item.label}
        </li>
      ))}
    </ul>
  );
}
