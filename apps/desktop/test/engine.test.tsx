import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EngineGate } from "../src/engine";

const native = vi.hoisted(() => ({ getEndpoint: vi.fn(), onEngineEvent: vi.fn(() => () => {}) }));
vi.mock("../src/native", async () => {
  const real = await vi.importActual<typeof import("../src/native")>("../src/native");
  return { ...real, getEndpoint: native.getEndpoint, onEngineEvent: native.onEngineEvent, getAppPaths: async () => null };
});

class SilentWS {
  onmessage = null;
  onclose = null;
  close() {}
}
vi.stubGlobal("WebSocket", SilentWS);

const wrap = (ui: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

describe("EngineGate", () => {
  it("waits while the engine starts, then shows the app", async () => {
    const { NotReady } = await import("../src/native");
    native.getEndpoint
      .mockRejectedValueOnce(new NotReady("engine not ready yet"))
      .mockResolvedValueOnce({ port: 5000, token: "abc" });
    wrap(<EngineGate><p>app</p></EngineGate>);
    expect(screen.getByText("Starting ImageVault…")).toBeTruthy();
    expect(await screen.findByText("app")).toBeTruthy();
  });

  it("shows the shell's give-up message", async () => {
    native.getEndpoint.mockRejectedValueOnce(new Error("The ImageVault engine stopped 4 times within a minute"));
    wrap(<EngineGate><p>app</p></EngineGate>);
    expect((await screen.findByRole("alert")).textContent).toContain("stopped 4 times");
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
