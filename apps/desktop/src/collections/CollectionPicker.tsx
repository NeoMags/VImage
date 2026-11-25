// "Add to collection…" (B16): a small searchable list of the nested collections, with
// "New collection" for a name that does not exist yet.
import { useCollections, useCreateCollection } from "@imagevault/api-client";
import { Button } from "@imagevault/ui";
import { useEffect, useMemo, useRef, useState } from "react";

import { errorText } from "../components/States";
import { collectionOptions, pathOf } from "./tree";

export function CollectionPicker({
  label = "Add to collection…",
  disabled,
  exclude,
  onPick,
}: {
  label?: string;
  disabled?: boolean;
  /** A collection not to offer (the one being viewed). */
  exclude?: number;
  onPick: (id: number, name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const collections = useCollections();
  const create = useCreateCollection();
  const all = collections.data ?? [];
  const query = q.trim().toLocaleLowerCase();

  // Searching shows matches with their full path; otherwise the indented tree.
  const rows = useMemo(() => {
    const opts = collectionOptions(all).filter(({ c }) => c.id !== exclude);
    if (!query) return opts.map(({ c, depth }) => ({ c, depth, text: c.name }));
    return opts
      .filter(({ c }) => c.name.toLocaleLowerCase().includes(query))
      .map(({ c }) => ({ c, depth: 0, text: pathOf(all, c.id) }));
  }, [all, exclude, query]);
  const exact = all.some((c) => c.name.toLocaleLowerCase() === query);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const pick = (id: number, name: string) => {
    setOpen(false);
    setQ("");
    onPick(id, name);
  };

  return (
    <div className="picker" ref={box} onKeyDown={(e) => e.key === "Escape" && (e.stopPropagation(), setOpen(false))}>
      <Button size="sm" disabled={disabled} aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((o) => !o)}>
        {label}
      </Button>
      {open ? (
        <div className="picker-pop" role="dialog" aria-label="Choose a collection">
          <input
            className="inline-input"
            type="search"
            autoFocus
            aria-label="Find or name a collection"
            placeholder="Find or name a collection"
            maxLength={200}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <ul className="picker-list" role="listbox" aria-label="Collections">
            {rows.map(({ c, depth, text }) => (
              <li key={c.id} role="option" aria-selected={false}>
                <button type="button" style={{ paddingLeft: 8 + depth * 14 }} onClick={() => pick(c.id, c.name)}>
                  {text}
                </button>
              </li>
            ))}
            {!rows.length && !q.trim() ? <li className="muted small">No collections yet.</li> : null}
          </ul>
          {q.trim() && !exact ? (
            <Button
              size="sm"
              disabled={create.isPending}
              onClick={() => {
                setErr(null);
                create.mutate(
                  { name: q.trim() },
                  { onSuccess: (c) => pick(c.id, c.name), onError: (e) => setErr(errorText(e)) },
                );
              }}
            >
              New collection “{q.trim()}”
            </Button>
          ) : null}
          {err ? <p className="text-danger small">{err}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
