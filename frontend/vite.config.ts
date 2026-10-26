import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

// In development the API runs in Docker on :8000. Proxying keeps the dashboard and API on
// one origin, so session cookies work without CORS (production does the same with Caddy).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
