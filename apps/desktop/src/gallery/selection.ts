// Selection rules for the gallery: click, Ctrl+click, Shift+click range, select all loaded, clear.
// Selection is by asset id; ranges use the global display index and include only loaded items.

export interface Selection {
  ids: ReadonlySet<number>;
  /** Global index the next Shift+click extends from. */
  anchor: number | null;
}

export const EMPTY: Selection = { ids: new Set(), anchor: null };

export type ItemAt = (index: number) => { id: number } | undefined;

export function click(
  sel: Selection,
  index: number,
  id: number,
  mods: { ctrl: boolean; shift: boolean },
  itemAt: ItemAt,
): Selection {
  if (mods.shift && sel.anchor !== null) {
    const ids = new Set(mods.ctrl ? sel.ids : []);
    const [a, b] = sel.anchor <= index ? [sel.anchor, index] : [index, sel.anchor];
    for (let i = a; i <= b; i++) {
      const it = itemAt(i);
      if (it) ids.add(it.id);
    }
    return { ids, anchor: sel.anchor };
  }
  if (mods.ctrl) return toggle(sel, index, id);
  return { ids: new Set([id]), anchor: index };
}

export function toggle(sel: Selection, index: number, id: number): Selection {
  const ids = new Set(sel.ids);
  if (ids.has(id)) ids.delete(id);
  else ids.add(id);
  return { ids, anchor: index };
}

export function all(total: number, itemAt: ItemAt): Selection {
  const ids = new Set<number>();
  for (let i = 0; i < total; i++) {
    const it = itemAt(i);
    if (it) ids.add(it.id);
  }
  return { ids, anchor: null };
}
