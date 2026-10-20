import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the backend runs separately: /api is proxied to it (frontend/specs/overview.md).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": process.env.TESTER_BACKEND_URL ?? "http://127.0.0.1:8090" } },
  build: { outDir: "dist" },
});
