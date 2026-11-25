// Nested collections (B16): ordering, paths and the "can't move inside itself" rule, shared by the
// sidebar tree, the pickers and the filter panel.
import type { Collection } from "@imagevault/shared-types";

const byName = (a: Collection, b: Collection) => a.name.localeCompare(b.name);

export function childrenOf(all: Collection[]): Map<number | null, Collection[]> {
  const kids = new Map<number | null, Collection[]>();
  for (const c of all) {
    const p = c.parent_id ?? null;
    kids.set(p, [...(kids.get(p) ?? []), c]);
  }
  for (const list of kids.values()) list.sort(byName);
  return kids;
}

/** Depth-first, parents before children, by name at each level. */
export function collectionOptions(all: Collection[]): { c: Collection; depth: number }[] {
  const kids = childrenOf(all);
  const out: { c: Collection; depth: number }[] = [];
  const seen = new Set<number>();
  const walk = (parent: number | null, depth: number) => {
    for (const c of kids.get(parent) ?? []) {
      if (seen.has(c.id)) continue; // defensive: the engine refuses cycles
      seen.add(c.id);
      out.push({ c, depth });
      walk(c.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** The collection and everything inside it (a collection cannot move into any of these). */
export function subtree(all: Collection[], id: number): Set<number> {
  const kids = childrenOf(all);
  const out = new Set<number>([id]);
  const stack = [id];
  while (stack.length) for (const c of kids.get(stack.pop()!) ?? []) if (!out.has(c.id)) (out.add(c.id), stack.push(c.id));
  return out;
}

/** "Trips › Italy › Rome". */
export function pathOf(all: Collection[], id: number): string {
  const byId = new Map(all.map((c) => [c.id, c]));
  const names: string[] = [];
  const seen = new Set<number>();
  for (let c = byId.get(id); c && !seen.has(c.id); c = c.parent_id == null ? undefined : byId.get(c.parent_id)) {
    seen.add(c.id);
    names.unshift(c.name);
  }
  return names.join(" › ");
}
