import path from "node:path";
import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const packageJson = JSON.parse(
  readFileSync(path.resolve(__dirname, "./package.json"), "utf-8"),
) as { version: string };

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.NLP_API_URL ?? "http://127.0.0.1:8765";

  return {
    plugins: [react()],
    define: {
      __APP_VERSION__: JSON.stringify(packageJson.version),
    },
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes("node_modules/katex")) return "katex";
            if (id.includes("node_modules/react-markdown") || id.includes("node_modules/remark-") || id.includes("node_modules/rehype-")) return "markdown";
          },
        },
      },
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      hmr: { host: "127.0.0.1", path: "/__nlp_vite_hmr" },
      fs: { allow: [path.resolve(__dirname)] },
      proxy: {
        "/api": { target, changeOrigin: true },
        "/health": { target, changeOrigin: true },
        "/ws": { target, changeOrigin: true, ws: true },
      },
    },
    test: {
      environment: "happy-dom",
      globals: true,
      setupFiles: ["./src/tests/setup.ts"],
      maxWorkers: 4,
    },
  };
});
