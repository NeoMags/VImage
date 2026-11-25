// Connects the UI to the engine: waits for the shell to report the endpoint, rebuilds the
// client when the engine restarts (new port and token), and shows a full-screen state while
// the engine is starting or has failed.
import { ApiError, ApiProvider, createApiClient, useLiveEvents } from "@imagevault/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { FullScreen } from "./components/States";
import { getEndpoint, NotReady, onEngineEvent } from "./native";

type Engine =
  | { state: "starting"; slow: boolean }
  | { state: "ready"; port: number; token: string; generation: number }
  | { state: "failed"; message: string };

const POLL_MS = 200;
const SLOW_MS = 15_000;

export function EngineGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [engine, setEngine] = useState<Engine>({ state: "starting", slow: false });
  const [attempt, setAttempt] = useState(0);

  // Poll the shell until the engine is ready; again after every restart (attempt changes).
  useEffect(() => {
    let stop = false;
    const t0 = Date.now();
    const poll = async () => {
      while (!stop) {
        try {
          const ep = await getEndpoint();
          if (stop) return;
          setEngine((prev) => ({
            state: "ready",
            ...ep,
            generation: prev.state === "ready" ? prev.generation + 1 : 1,
          }));
          return;
        } catch (e) {
          if (!(e instanceof NotReady)) {
            if (!stop) setEngine({ state: "failed", message: e instanceof Error ? e.message : String(e) });
            return;
          }
          if (!stop) setEngine({ state: "starting", slow: Date.now() - t0 > SLOW_MS });
          await new Promise((r) => setTimeout(r, POLL_MS));
        }
      }
    };
    void poll();
    return () => {
      stop = true;
    };
  }, [attempt]);

  useEffect(
    () =>
      onEngineEvent({
        restarted: () => setAttempt((n) => n + 1),
        failed: (message) => setEngine({ state: "failed", message }),
      }),
    [],
  );

  // A new engine process knows nothing of what this window had cached.
  const generation = engine.state === "ready" ? engine.generation : 0;
  useEffect(() => {
    if (generation > 1) void qc.invalidateQueries();
  }, [generation, qc]);

  const port = engine.state === "ready" ? engine.port : 0;
  const token = engine.state === "ready" ? engine.token : "";
  const client = useMemo(() => (port ? createApiClient({ port, token }) : null), [port, token]);
  const retry = useCallback(() => {
    setEngine({ state: "starting", slow: false });
    setAttempt((n) => n + 1);
  }, []);

  if (engine.state === "failed") {
    return (
      <FullScreen
        tone="danger"
        title="The ImageVault engine could not start"
        detail={engine.message}
        action={{ label: "Try again", onClick: retry }}
      />
    );
  }
  if (!client) {
    return (
      <FullScreen
        title="Starting ImageVault…"
        detail={engine.state === "starting" && engine.slow ? "This is taking longer than usual." : undefined}
        busy
      />
    );
  }
  return (
    <ApiProvider client={client} key={generation}>
      <LiveProvider>{children}</LiveProvider>
    </ApiProvider>
  );
}

// ------------------------------------------------------------------------------ live status
const LiveContext = createContext<ReturnType<typeof useLiveEvents>>("connecting");

function LiveProvider({ children }: { children: ReactNode }) {
  const state = useLiveEvents();
  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}

export const useLiveState = () => useContext(LiveContext);

/** Query retry policy: keep retrying while the engine is (re)starting, never for real errors. */
export function shouldRetry(failures: number, error: unknown): boolean {
  return error instanceof ApiError && error.transient && failures < 5;
}
