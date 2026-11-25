import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { CollectionPicker } from "../src/collections/CollectionPicker";
import { CollectionsNav } from "../src/collections/CollectionsNav";
import { collectionOptions, pathOf, subtree } from "../src/collections/tree";
import { setFilters } from "../src/gallery/filters";
import { Assets } from "../src/screens/Assets";
import { renderWith } from "./helpers";

vi.mock("../src/native", () => ({
  revealInExplorer: vi.fn(),
  getAppPaths: vi.fn(async () => null),
  inTauri: () => false,
}));

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  setFilters({});
  localStorage.clear();
});

const coll = (id: number, name: string, parent_id: number | null, count = 0) => ({
  id,
  uid: `c${id}`,
  name,
  parent_id,
  kind: "manual" as const,
  count,
  created_at: "2026-11-25T10:00:00Z",
});
const TREE = [coll(1, "Trips", null), coll(2, "Italy", 1, 3), coll(3, "Rome", 2, 1), coll(4, "Cats", null, 2)];

describe("collection tree helpers", () => {
  it("orders, nests, paths and blocks moving inside itself", () => {
    expect(collectionOptions(TREE).map(({ c, depth }) => `${depth}:${c.name}`)).toEqual(["0:Cats", "0:Trips", "1:Italy", "2:Rome"]);
    expect(pathOf(TREE, 3)).toBe("Trips › Italy › Rome");
    expect([...subtree(TREE, 1)].sort()).toEqual([1, 2, 3]);
    expect([...subtree(TREE, 4)]).toEqual([4]);
  });
});

describe("Collections sidebar", () => {
  it("shows the nested tree with counts, collapses, and creates a collection", async () => {
    let created: unknown = null;
    const { calls } = renderWith(
      <Routes>
        <Route path="*" element={<CollectionsNav />} />
      </Routes>,
      {
        "GET /v1/collections": () => TREE,
        "POST /v1/collections": async (req) => {
          created = await req.json();
          return coll(9, "Pets", null);
        },
      },
    );
    const tree = await screen.findByRole("tree", { name: "Collections" });
    expect(within(tree).getByRole("link", { name: /Italy\s*3/ }).getAttribute("href")).toBe("/collections/2");
    expect(within(tree).getByRole("link", { name: /Rome\s*1/ })).toBeTruthy();
    fireEvent.click(within(tree).getByRole("button", { name: "Collapse Trips" }));
    expect(within(tree).queryByRole("link", { name: /Italy/ })).toBeNull();
    expect(JSON.parse(localStorage.getItem("iv.collapsed")!)).toEqual([1]);

    fireEvent.click(screen.getByRole("button", { name: "New collection" }));
    const input = screen.getByRole("textbox", { name: "New collection name" });
    fireEvent.change(input, { target: { value: "  Pets " } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(created).toEqual({ name: "Pets", parent_id: null }));
    expect(calls.some((r) => r.method === "POST")).toBe(true);
  });
});

describe("Add to collection picker", () => {
  it("searches by path and creates a new one when the name is new", async () => {
    const picked: [number, string][] = [];
    renderWith(<CollectionPicker exclude={4} onPick={(id, name) => picked.push([id, name])} />, {
      "GET /v1/collections": () => TREE,
      "POST /v1/collections": () => coll(9, "Venice", null),
    });
    fireEvent.click(screen.getByRole("button", { name: "Add to collection…" }));
    const list = await screen.findByRole("listbox", { name: "Collections" });
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(3)); // Cats is excluded
    const find = screen.getByRole("searchbox", { name: "Find or name a collection" });
    fireEvent.change(find, { target: { value: "rom" } });
    fireEvent.click(within(list).getByRole("button", { name: "Trips › Italy › Rome" }));
    expect(picked).toEqual([[3, "Rome"]]);

    fireEvent.click(screen.getByRole("button", { name: "Add to collection…" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Find or name a collection" }), { target: { value: "Venice" } });
    fireEvent.click(screen.getByRole("button", { name: "New collection “Venice”" }));
    await waitFor(() => expect(picked).toEqual([[3, "Rome"], [9, "Venice"]]));
  });
});

describe("Collection view", () => {
  const routes = (seen: URL[], extra: Record<string, (req: Request) => unknown> = {}) => ({
    "GET /v1/collections": () => TREE,
    "GET /v1/assets:months": (req: Request) => {
      seen.push(new URL(req.url));
      return { total: 0, months: [] };
    },
    ...extra,
  });

  it("shows only that collection, keeps it fixed, and deletes after asking", async () => {
    const seen: URL[] = [];
    let deleted = false;
    setFilters({ collection: 4, favorite: true }); // set in All photos; the viewed collection wins
    renderWith(
      <Routes>
        <Route path="/collections/:id" element={<Assets collectionId={2} />} />
        <Route path="/collections/1" element={<p>parent page</p>} />
      </Routes>,
      routes(seen, { "DELETE /v1/collections/2": () => ((deleted = true), undefined) }),
      "/collections/2",
    );
    expect(await screen.findByRole("heading", { name: /Trips › Italy/ })).toBeTruthy();
    expect(await screen.findByText("No photos match these filters")).toBeTruthy();
    const q = seen.at(-1)!.searchParams;
    expect(q.get("collection")).toBe("2");
    expect(q.get("favorite")).toBe("true");
    const chips = screen.getByLabelText("Active filters");
    expect(within(chips).queryByText(/Cats/)).toBeNull();
    expect(within(chips).getByText("Favourites")).toBeTruthy();

    // Move: not into itself or its child (Rome); Trips is already its parent.
    const move = screen.getByRole("combobox", { name: "Move collection to" }) as HTMLSelectElement;
    expect([...move.options].map((o) => o.textContent)).toEqual(["Move to…", "Top level", "Cats"]);

    fireEvent.click(screen.getByRole("button", { name: "Delete…" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete “Italy”?" });
    expect(dialog.textContent).toContain("3 photos in it stay in your library");
    expect(dialog.textContent).toContain("1 sub-collection move up one level");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete collection" }));
    expect(await screen.findByText("parent page")).toBeTruthy();
    expect(deleted).toBe(true);
  });

  it("renames on Enter and says when a collection is gone", async () => {
    let body: unknown = null;
    renderWith(<Assets collectionId={4} />, routes([], {
      "PATCH /v1/collections/4": async (req) => ((body = await req.json()), coll(4, "Kittens", null, 2)),
    }));
    expect(await screen.findByText("This collection is empty")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Rename" }));
    const name = screen.getByRole("textbox", { name: "Collection name" });
    fireEvent.change(name, { target: { value: "Kittens" } });
    fireEvent.keyDown(name, { key: "Enter" });
    await waitFor(() => expect(body).toEqual({ name: "Kittens" }));
  });

  it("explains a missing collection", async () => {
    renderWith(<Assets collectionId={99} />, routes([]));
    expect(await screen.findByText("This collection no longer exists")).toBeTruthy();
  });
});
