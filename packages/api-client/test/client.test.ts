import { describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient, toApiError, unwrap } from "../src";

const ep = { port: 4321, token: "t".repeat(32) };

function fakeFetch(status: number, body: unknown, contentType = "application/json") {
  return vi.fn(
    async (_req: Request) =>
      new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": contentType } }),
  );
}

describe("toApiError", () => {
  it("reads problem+json", () => {
    const e = toApiError(404, { type: "x", title: "Not found", status: 404, code: "not_found", detail: "photo 9 not found" });
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.detail, e.transient]).toEqual([404, "not_found", "photo 9 not found", false]);
  });

  it("falls back to the HTTP status for other bodies", () => {
    expect(toApiError(502, "Bad gateway").detail).toBe("Bad gateway");
    expect(toApiError(500, null).detail).toBe("HTTP 500");
  });
});

describe("client", () => {
  it("sends the token and returns typed data", async () => {
    const f = fakeFetch(200, { paused: true });
    const api = createApiClient(ep, f);
    const out = await unwrap(api.POST("/v1/jobs/pause"));
    expectTypeOf(out).toEqualTypeOf<{ paused: boolean }>();
    expect(out.paused).toBe(true);
    const req = f.mock.calls[0]![0];
    expect(req.url).toBe("http://127.0.0.1:4321/v1/jobs/pause");
    expect(req.headers.get("authorization")).toBe(`Bearer ${ep.token}`);
  });

  it("throws ApiError with the engine's problem details", async () => {
    const problem = { type: "t", title: "Conflict", status: 409, code: "conflict", detail: "job 3 is queued" };
    const api = createApiClient(ep, fakeFetch(409, problem, "application/problem+json"));
    const err = await unwrap(api.POST("/v1/jobs/{job_id}/retry", { params: { path: { job_id: 3 } } })).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.code, err.detail]).toEqual(["conflict", "job 3 is queued"]);
  });

  it("reports an unreachable engine as offline (transient)", async () => {
    const api = createApiClient(ep, vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const err: ApiError = await unwrap(api.GET("/v1/stats")).catch((e) => e);
    expect([err.status, err.code, err.transient]).toEqual([0, "offline", true]);
  });
});
