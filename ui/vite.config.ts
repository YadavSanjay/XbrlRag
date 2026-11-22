import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev server proxies /api -> FastAPI BFF (src/api/main.py) so there is no CORS
// fuss in development. Override the target with VITE_BFF_TARGET if needed.
const bffTarget = process.env.VITE_BFF_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: bffTarget, changeOrigin: true },
    },
  },
});
