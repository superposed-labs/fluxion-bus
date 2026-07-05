import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Built bundle ships inside the Python package so `pip install fluxion`
// gets a self-contained UI.
const STATIC_OUT = resolve(__dirname, "../src/fluxion/web/static");

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: STATIC_OUT,
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
