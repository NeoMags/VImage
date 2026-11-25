import { ApiProvider, createApiClient } from "@imagevault/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

export type Handler = (req: Request) => unknown | Response | Promise<unknown | Response>;

/** A fake engine: "METHOD /path" → JSON body (or a Response). Unknown routes answer 404 problem+json. */
export function fakeEngine(routes: Record<string, Handler>) {
  const calls: Request[] = [];
  const fetch = vi.fn(async (req: Request) => {
    calls.push(req.clone());
    const url = new URL(req.url);
    const h = routes[`${req.method} ${url.pathname}`];
    if (!h) {
      return new Response(
        JSON.stringify({ type: "t", title: "Not found", status: 404, code: "not_found", detail: `no route ${url.pathname}` }),
        { status: 404, headers: { "content-type": "application/problem+json" } },
      );
    }
    const out = await h(req);
    if (out instanceof Response) return out;
    return new Response(out === undefined ? null : JSON.stringify(out), {
      status: out === undefined ? 204 : 200,
      headers: { "content-type": "application/json" },
    });
  });
  return { fetch, calls };
}

export function renderWith(ui: ReactNode, routes: Record<string, Handler>, path = "/") {
  const engine = fakeEngine(routes);
  const api = createApiClient({ port: 1, token: "t" }, engine.fetch);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <ApiProvider client={api}>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </ApiProvider>
    </QueryClientProvider>,
  );
  return { ...view, ...engine, qc };
}

export const problem = (status: number, code: string, detail: string) =>
  new Response(JSON.stringify({ type: "t", title: "x", status, code, detail }), {
    status,
    headers: { "content-type": "application/problem+json" },
  });

export const stats = (over: Record<string, unknown> = {}) => ({
  photos: { total: 1234, by_status: {}, by_kind: {}, hidden: 0, favorites: 7, present: 1200, unreachable: 30, missing: 4 },
  files_unsupported: 0,
  tags: 0,
  collections: 0,
  sources: { active: 1 },
  last_scan_at: null,
  jobs: { queued: 5, running: 1, failed: 2, succeeded: 100 },
  queue: { paused: false, gate: null, slots: 3, busy: 1 },
  db_version: 4,
  db_latest: 4,
  ...over,
});

export const source = (over: Record<string, unknown> = {}) => ({
  id: 1,
  uid: "u1",
  kind: "local_folder",
  display_name: "Pictures",
  root_path: "C:\\Users\\Neo\\Pictures",
  status: "active",
  last_scan_at: null,
  last_error: null,
  files: { present: 41220 },
  scanning: false,
  watching: true,
  last_scan: null,
  ...over,
});
