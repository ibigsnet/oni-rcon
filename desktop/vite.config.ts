import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri serves the built files; in development it points the window at this server.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true, watch: { ignored: ["**/src-tauri/**"] } },
  build: { target: "es2022", chunkSizeWarningLimit: 1500 },
  test: { environment: "node" },
} as any);
