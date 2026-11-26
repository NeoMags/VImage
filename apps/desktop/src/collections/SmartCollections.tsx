// Smart Collections (S1): the gallery filters saved under a name. The engine works out the photos
// each time one is opened, so new photos that match appear by themselves.
//
// Save: "Save as smart collection…" in the filter panel (All photos).
// Edit: "Edit rule" in a smart collection loads its filters into All photos; the filter panel then
// offers "Update ‘name’" next to "Save as new…".
import { useCollections, useCreateCollection, useSources, useTags, useUpdateCollection } from "@imagevault/api-client";
import type { Collection, SmartRule } from "@imagevault/shared-types";
import { Button } from "@imagevault/ui";
import { useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";

import { errorText } from "../components/States";
import { chipsFor, clean, type Filters, setFilters } from "../gallery/filters";

// ------------------------------------------------------------------ which smart collection is being edited
let editing: { id: number; name: string } | null = null;
const listeners = new Set<() => void>();
function setEditing(next: typeof editing) {
  editing = next;
  for (const fn of listeners) fn();
}
function useEditing() {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => editing,
  );
}

/** The filters a rule keeps; "hidden" and empty values never go in. */
function ruleOf(f: Filters): SmartRule {
  const { hidden: _, ...rest } = clean(f) as Filters & { hidden?: boolean };
  return { v: 1, filters: rest as SmartRule["filters"] };
}

/** In the filter panel: save the current filters as a smart collection, or update the one being edited. */
export function SaveSmart({ filters }: { filters: Filters }) {
  const navigate = useNavigate();
  const create = useCreateCollection();
  const update = useUpdateCollection();
  const edit = useEditing();
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const err = create.error ?? update.error;

  const save = () => {
    const n = name.trim();
    if (!n) return;
    create.mutate(
      { name: n, rule: ruleOf(filters) },
      {
        onSuccess: (c) => {
          setNaming(false);
          setName("");
          setEditing(null);
          setFilters({});
          navigate(`/collections/${c.id}`);
        },
      },
    );
  };

  return (
    <div className="smart-save" role="group" aria-label="Smart collection">
      {edit ? (
        <>
          <Button
            size="sm"
            variant="primary"
            disabled={update.isPending}
            onClick={() =>
              update.mutate(
                { id: edit.id, rule: ruleOf(filters) },
                {
                  onSuccess: () => {
                    setEditing(null);
                    setFilters({});
                    navigate(`/collections/${edit.id}`);
                  },
                },
              )
            }
          >
            Update “{edit.name}”
          </Button>
          <Button size="sm" onClick={() => setEditing(null)}>
            Stop editing
          </Button>
        </>
      ) : null}
      {naming ? (
        <form
          className="smart-name"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <input
            className="inline-input"
            autoFocus
            aria-label="Smart collection name"
            placeholder="Name, e.g. Screenshots this year"
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setNaming(false);
            }}
          />
          <Button size="sm" variant="primary" type="submit" disabled={!name.trim() || create.isPending}>
            Save
          </Button>
        </form>
      ) : (
        <Button size="sm" onClick={() => setNaming(true)}>
          {edit ? "Save as new…" : "Save as smart collection…"}
        </Button>
      )}
      {err ? <p className="text-danger small">{errorText(err)}</p> : null}
    </div>
  );
}

/** At the top of an opened smart collection: what its rule is, and a way to change it. */
export function SmartRuleBar({ coll }: { coll: Collection }) {
  const navigate = useNavigate();
  const tags = useTags();
  const sources = useSources();
  const collections = useCollections();
  const filters = (coll.rule ? clean(coll.rule.filters as Filters) : {}) as Filters;
  const chips = chipsFor(filters, {
    tag: (id) => tags.data?.find((t) => t.id === id)?.name,
    source: (id) => sources.data?.find((s) => s.id === id)?.display_name,
    collection: (id) => collections.data?.find((c) => c.id === id)?.name,
  });
  return (
    <div className="smart-rule" role="group" aria-label="Smart collection rule">
      <span className="muted small">✦ Smart collection. Shows every photo with:</span>
      {coll.rule ? (
        chips.map((c) => (
          <span key={c.key} className="chip chip-static">
            {c.label}
          </span>
        ))
      ) : (
        <span className="text-danger small">its rule could not be read</span>
      )}
      <Button
        size="sm"
        onClick={() => {
          setFilters(filters);
          setEditing({ id: coll.id, name: coll.name });
          try {
            // open the filter panel in All photos, where "Update" is
            const prefs = JSON.parse(localStorage.getItem("iv.gallery") ?? "{}") as Record<string, unknown>;
            localStorage.setItem("iv.gallery", JSON.stringify({ ...prefs, filtersOpen: true }));
          } catch {
            /* the Filters button opens it too */
          }
          navigate("/photos");
        }}
      >
        Edit rule
      </Button>
    </div>
  );
}
