import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI calls the FastAPI backend through this proxy, so no API keys or
// backend URLs ever reach the browser bundle.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
