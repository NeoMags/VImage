import type { Source } from "@imagevault/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ApiProvider, createApiClient, useAddSource, useSources } from "../src";

function setup(handler: (req: Request) => Response) {
  const f = vi.fn(async (req: Request) => handler(req));
  const api = createApiClient({ port: 1, token: "t" }, f);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <ApiProvider client={api}>{children}</ApiProvider>
    </QueryClientProvider>
  );
  return { f, qc, wrapper };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("hooks", () => {
  it("useSources returns typed sources", async () => {
    const { wrapper } = setup(() => json([{ id: 1, display_name: "Pictures" }]));
    const { result } = renderHook(() => useSources(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expectTypeOf(result.current.data).toEqualTypeOf<Source[] | undefined>();
    expect(result.current.data?.[0]?.display_name).toBe("Pictures");
  });

  it("useAddSource posts the folder and refreshes the source list", async () => {
    let sources = 0;
    const { f, wrapper } = setup((req) => {
      if (req.method === "POST") return json({ id: 2, scan_run_id: 5 }, 201);
      sources += 1;
      return json([]);
    });
    const list = renderHook(() => useSources(), { wrapper });
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true));
    const add = renderHook(() => useAddSource(), { wrapper });
    const out = await add.result.current.mutateAsync({ path: "D:\\Photos" });
    expect(out).toEqual({ id: 2, scan_run_id: 5 });
    const post = f.mock.calls.find(([r]) => r.method === "POST")![0];
    expect(await post.json()).toEqual({ path: "D:\\Photos" });
    await waitFor(() => expect(sources).toBe(2));
  });
});
