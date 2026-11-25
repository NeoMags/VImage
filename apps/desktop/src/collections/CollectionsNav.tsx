// The Collections section of the sidebar (B16): the nested tree with counts, and "New collection".
import { useCollections, useCreateCollection } from "@imagevault/api-client";
import type { Collection } from "@imagevault/shared-types";
import { useState } from "react";
import { NavLink, useNavigate } from "react-router";

import { errorText } from "../components/States";
import { num } from "../format";
import { childrenOf } from "./tree";

const KEY = "iv.collapsed";

function loadCollapsed(): Set<number> {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY) ?? "[]") as number[]);
  } catch {
    return new Set();
  }
}

/** A name field that creates on Enter and gives up on Esc or blur when empty. */
export function NewCollectionField({
  parentId,
  onDone,
}: {
  parentId: number | null;
  onDone: (created: Collection | null) => void;
}) {
  const create = useCreateCollection();
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const submit = () => {
    const n = name.trim();
    if (!n) return onDone(null);
    setErr(null);
    create.mutate({ name: n, parent_id: parentId }, { onSuccess: (c) => onDone(c as Collection), onError: (e) => setErr(errorText(e)) });
  };
  return (
    <div className="new-collection">
      <input
        className="inline-input"
        autoFocus
        aria-label={parentId == null ? "New collection name" : "New sub-collection name"}
        placeholder="Collection name"
        maxLength={200}
        value={name}
        disabled={create.isPending}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
          if (e.key === "Escape") onDone(null);
        }}
        onBlur={() => !name.trim() && onDone(null)}
      />
      {err ? <p className="text-danger small">{err}</p> : null}
    </div>
  );
}

export function CollectionsNav() {
  const collections = useCollections();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const toggle = (id: number) =>
    setCollapsed((old) => {
      const next = new Set(old);
      if (!next.delete(id)) next.add(id);
      try {
        localStorage.setItem(KEY, JSON.stringify([...next]));
      } catch {
        /* per-viewer convenience only */
      }
      return next;
    });
  const kids = childrenOf(collections.data ?? []);

  const branch = (parent: number | null, depth: number, seen: Set<number>) => (
    <ul className="tree" role={depth ? "group" : "tree"} aria-label={depth ? undefined : "Collections"}>
      {(kids.get(parent) ?? []).map((c) => {
        if (seen.has(c.id)) return null;
        const inner = new Set(seen).add(c.id);
        const hasKids = (kids.get(c.id) ?? []).length > 0;
        const open = !collapsed.has(c.id);
        return (
          <li key={c.id} role="treeitem" aria-expanded={hasKids ? open : undefined} aria-label={c.name}>
            <div className="tree-row" style={{ paddingLeft: depth * 14 }}>
              {hasKids ? (
                <button type="button" className="twisty" aria-label={`${open ? "Collapse" : "Expand"} ${c.name}`} onClick={() => toggle(c.id)}>
                  {open ? "▾" : "▸"}
                </button>
              ) : (
                <span className="twisty" />
              )}
              <NavLink to={`/collections/${c.id}`}>
                <span className="name">{c.name}</span>
                <span className="muted small">{num(c.count)}</span>
              </NavLink>
            </div>
            {hasKids && open ? branch(c.id, depth + 1, inner) : null}
          </li>
        );
      })}
    </ul>
  );

  return (
    <div className="nav-collections" role="region" aria-label="Collections">
      <div className="nav-head">
        <span>Collections</span>
        <button type="button" className="link" aria-label="New collection" title="New collection" onClick={() => setAdding(true)}>
          +
        </button>
      </div>
      {adding ? (
        <NewCollectionField
          parentId={null}
          onDone={(c) => {
            setAdding(false);
            if (c) navigate(`/collections/${c.id}`);
          }}
        />
      ) : null}
      {collections.data?.length ? (
        branch(null, 0, new Set())
      ) : collections.isSuccess && !adding ? (
        <p className="muted small nav-empty">Group photos you choose, like “Trips › Italy”.</p>
      ) : null}
    </div>
  );
}
