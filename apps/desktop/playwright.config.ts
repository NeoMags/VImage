import { defineConfig } from "@playwright/test";

// End-to-end: the built UI in Chromium against a real engine (started by e2e/engine.ts).
// The browser has no Tauri, so the test build (VITE_IV_E2E=1) takes the endpoint from the URL.
export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:1421", trace: "off", viewport: { width: 1280, height: 800 } },
  webServer: {
    command: "pnpm exec vite build --mode e2e && pnpm exec vite preview",
    url: "http://localhost:1421",
    reuseExistingServer: false,
    env: { VITE_IV_E2E: "1" },
    timeout: 120_000,
  },
});
