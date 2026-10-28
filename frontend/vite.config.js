import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8"));

export default defineConfig({
  plugins: [react()],
  // the interface's version, compared with the server's at runtime to catch a stale build
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 2000 },
});
