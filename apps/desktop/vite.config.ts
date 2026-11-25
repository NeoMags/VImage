import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Tauri loads http://localhost:1420 in `tauri dev` and ../dist in a build.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  preview: { port: 1421, strictPort: true },
  build: { target: "es2022", outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", globals: true, include: ["test/**/*.test.{ts,tsx}"], css: { modules: { classNameStrategy: "non-scoped" } } },
});
