// What you can do with the collection you are looking at (B16): rename, move, add a
// sub-collection, delete. Deleting never touches photos; sub-collections move up a level.
import { useCollections, useDeleteCollection, useUpdateCollection } from "@imagevault/api-client";
import type { Collection } from "@imagevault/shared-types";
import { Button, ConfirmDialog } from "@imagevault/ui";
import { useState } from "react";
import { useNavigate } from "react-router";

import { errorText } from "../components/States";
import { plural } from "../format";
import { NewCollectionField } from "./CollectionsNav";
import { collectionOptions, subtree } from "./tree";

export function CollectionActions({ coll }: { coll: Collection }) {
  const navigate = useNavigate();
  const collections = useCollections();
  const update = useUpdateCollection();
  const remove = useDeleteCollection();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(coll.name);
  const [adding, setAdding] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const all = collections.data ?? [];
  const blocked = subtree(all, coll.id); // itself and what is inside it
  const kids = all.filter((c) => c.parent_id === coll.id).length;
  const err = update.error ?? remove.error;

  const rename = () => {
    const n = name.trim();
    if (!n || n === coll.name) return setRenaming(false);
    update.mutate({ id: coll.id, name: n }, { onSuccess: () => setRenaming(false) });
  };

  return (
    <div className="collection-actions" role="toolbar" aria-label="Collection">
      {renaming ? (
        <input
          className="inline-input"
          autoFocus
          aria-label="Collection name"
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") rename();
            if (e.key === "Escape") (setName(coll.name), setRenaming(false));
          }}
          onBlur={rename}
        />
      ) : (
        <Button size="sm" onClick={() => (setName(coll.name), setRenaming(true))}>
          Rename
        </Button>
      )}
      <select
        aria-label="Move collection to"
        value=""
        disabled={update.isPending}
        onChange={(e) => {
          const v = e.target.value;
          if (v) update.mutate({ id: coll.id, parent_id: v === "top" ? null : Number(v) });
        }}
      >
        <option value="">Move to…</option>
        {coll.parent_id != null ? <option value="top">Top level</option> : null}
        {collectionOptions(all)
          .filter(({ c }) => !blocked.has(c.id) && c.id !== coll.parent_id)
          .map(({ c, depth }) => (
            <option key={c.id} value={c.id}>
              {"  ".repeat(depth) + c.name}
            </option>
          ))}
      </select>
      {adding ? (
        <NewCollectionField
          parentId={coll.id}
          onDone={(c) => {
            setAdding(false);
            if (c) navigate(`/collections/${c.id}`);
          }}
        />
      ) : (
        <Button size="sm" onClick={() => setAdding(true)}>
          New sub-collection
        </Button>
      )}
      <Button size="sm" variant="danger" onClick={() => setConfirm(true)}>
        Delete…
      </Button>
      {err ? <span className="text-danger small">{errorText(err)}</span> : null}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Delete “${coll.name}”?`}
        confirmLabel="Delete collection"
        danger
        busy={remove.isPending}
        onConfirm={() =>
          // mutateAsync, not mutate: the refetched list drops this collection and unmounts this
          // component before a per-call onSuccess would run.
          void remove
            .mutateAsync(coll.id)
            .then(() => navigate(coll.parent_id != null ? `/collections/${coll.parent_id}` : "/photos"))
            .catch(() => undefined) // shown from remove.error
        }
      >
        <p>
          The {plural(coll.count, "photo")} in it stay in your library and on disk; only this grouping goes.
          {kids ? ` Its ${plural(kids, "sub-collection")} move up one level and are kept.` : ""}
        </p>
      </ConfirmDialog>
    </div>
  );
}
