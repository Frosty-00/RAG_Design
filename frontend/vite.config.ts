import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: Vite proxies /api → :8000 so SSE/CORS just work.
// Prod: built assets are served directly by FastAPI's StaticFiles (Layer 15).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/healthz": "http://localhost:8000",
      "/readyz": "http://localhost:8000",
    },
  },
});
