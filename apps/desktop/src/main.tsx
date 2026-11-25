import "@imagevault/ui/tokens.css";
import "./app.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router";

import { App } from "./App";
import { EngineGate, shouldRetry } from "./engine";
import { applyTheme, readTheme } from "./theme";

applyTheme(readTheme());

const queryClient = new QueryClient({
  defaultOptions: {
    // Live events keep data fresh; no refetch just because the window regained focus.
    queries: { retry: shouldRetry, staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <EngineGate>
          <App />
        </EngineGate>
      </HashRouter>
    </QueryClientProvider>
  </StrictMode>,
);
