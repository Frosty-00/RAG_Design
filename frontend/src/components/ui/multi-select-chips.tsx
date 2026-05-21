/** Tag input with filterable suggestions.
 *
 *  Behaviour:
 *  - Selected values render as removable chips inside the input box.
 *  - Typing filters the dropdown of `suggestions`.
 *  - Click suggestion → add as chip + clear input.
 *  - Enter on the input → add the raw typed text (if `allowCustom`) — useful
 *    when whitelisting a user that doesn't exist yet.
 *  - Backspace on empty input → remove the last chip.
 *
 *  Designed for ACL inputs where we want to **discover** what users/groups
 *  exist (no more typo'd `alics` silently failing the whitelist) while
 *  still allowing free-text for forward-looking entries.
 */
import { X } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export interface MultiSelectChipsProps {
  value: string[];
  onChange: (value: string[]) => void;
  suggestions: string[];
  placeholder?: string;
  /** When true (default), Enter on free-text adds it as a chip even if
   *  it's not in `suggestions`. Set false to force selection from list. */
  allowCustom?: boolean;
  /** Optional id so a <Label htmlFor> can target the inner input. */
  id?: string;
  className?: string;
  /** Shown under the input as a hint, e.g. "No users yet — type to add". */
  emptySuggestionsHint?: string;
}

export function MultiSelectChips({
  value,
  onChange,
  suggestions,
  placeholder,
  allowCustom = true,
  id,
  className,
  emptySuggestionsHint,
}: MultiSelectChipsProps) {
  const [input, setInput] = useState("");
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const filtered = useMemo(() => {
    const q = input.trim().toLowerCase();
    return suggestions
      .filter((s) => !value.includes(s))
      .filter((s) => !q || s.toLowerCase().includes(q));
  }, [suggestions, value, input]);

  const add = (raw: string) => {
    const t = raw.trim();
    if (!t || value.includes(t)) {
      setInput("");
      return;
    }
    onChange([...value, t]);
    setInput("");
  };

  const remove = (tag: string) => {
    onChange(value.filter((x) => x !== tag));
    inputRef.current?.focus();
  };

  return (
    <div className={cn("relative", className)}>
      <div
        className={cn(
          "flex min-h-[36px] flex-wrap items-center gap-1 rounded-md border border-input bg-background px-2 py-1.5 text-sm",
          "focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background",
        )}
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((tag) => (
          <Badge key={tag} variant="secondary" className="gap-1 pl-2 pr-1">
            {tag}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                remove(tag);
              }}
              className="rounded-sm hover:bg-muted-foreground/20"
              aria-label={`Remove ${tag}`}
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
        <input
          ref={inputRef}
          id={id}
          value={input}
          // readOnly when free text isn't accepted — typing into a field
          // that silently rejects the input is confusing. The user can
          // still focus the cell to open the dropdown of suggestions.
          readOnly={!allowCustom}
          onChange={(e) => {
            if (!allowCustom) return;
            setInput(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          // Delay blur so the click on a suggestion lands before we close
          // the dropdown (clicks bubble through onMouseDown on the items).
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (input.trim()) {
                if (allowCustom) {
                  add(input);
                } else if (filtered.length === 1) {
                  add(filtered[0]);
                }
              }
            } else if (e.key === "Backspace" && !input && value.length > 0) {
              remove(value[value.length - 1]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          placeholder={
            value.length === 0
              ? placeholder
              : !allowCustom
                ? "click to add more"
                : ""
          }
          className={cn(
            "min-w-[80px] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground",
            !allowCustom && "cursor-pointer caret-transparent",
          )}
        />
      </div>

      {open && (filtered.length > 0 || (input.trim() && allowCustom)) && (
        <div className="absolute left-0 right-0 z-20 mt-1 max-h-48 overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-md">
          {filtered.map((s) => (
            <button
              key={s}
              type="button"
              // onMouseDown fires before input's onBlur — without this, the
              // blur closes the menu before the click registers.
              onMouseDown={(e) => {
                e.preventDefault();
                add(s);
              }}
              className="block w-full px-3 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground"
            >
              {s}
            </button>
          ))}
          {/* "Add as new" affordance: when user typed something that isn't in
              suggestions, show a row to confirm-add it. */}
          {input.trim() &&
            allowCustom &&
            !suggestions.some(
              (s) => s.toLowerCase() === input.trim().toLowerCase(),
            ) && (
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  add(input);
                }}
                className="block w-full border-t px-3 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              >
                + Add <span className="font-mono">"{input.trim()}"</span> (not
                in list)
              </button>
            )}
        </div>
      )}

      {emptySuggestionsHint &&
        suggestions.length === 0 &&
        value.length === 0 && (
          <p className="mt-1 text-[10px] text-muted-foreground">
            {emptySuggestionsHint}
          </p>
        )}
    </div>
  );
}
