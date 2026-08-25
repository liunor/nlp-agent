import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.NLP_MONITOR_URL ?? "http://127.0.0.1:8766";
  return {
    root: path.resolve(__dirname, "monitor-app"),
    publicDir: false,
    plugins: [react()],
    resolve: { alias: { "@": path.resolve(__dirname, "src") } },
    build: { outDir: path.resolve(__dirname, "monitor-dist"), emptyOutDir: true, sourcemap: false },
    server: {
      host: "127.0.0.1", port: 5174, strictPort: true,
      hmr: { host: "127.0.0.1", path: "/__nlp_monitor_hmr" },
      fs: { allow: [path.resolve(__dirname)] },
      proxy: { "/api": { target, changeOrigin: true }, "/health": { target, changeOrigin: true }, "/ws": { target, changeOrigin: true, ws: true } },
    },
  };
});
