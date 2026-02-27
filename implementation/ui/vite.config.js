import { resolve } from "node:path";
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  resolve: {
    alias: {
      $lib: resolve(__dirname, "src/lib"),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: process.env.VOXLOGICA_DEV_BACKEND_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/results_viewer.js": {
        target: process.env.VOXLOGICA_DEV_BACKEND_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: () => "/static/results_viewer.js",
      },
      "/dask_queue_viz.js": {
        target: process.env.VOXLOGICA_DEV_BACKEND_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: () => "/static/dask_queue_viz.js",
      },
      "/livereload": {
        target: process.env.VOXLOGICA_DEV_BACKEND_URL || "http://127.0.0.1:8000",
        ws: true,
      },
    },
  },
  build: {
    outDir: resolve(__dirname, "../python/voxlogica/static"),
    emptyOutDir: false,
    cssCodeSplit: false,
    lib: {
      entry: resolve(__dirname, "src/main.js"),
      name: "VoxLogicAStudio",
      formats: ["iife"],
      fileName: () => "app.js",
    },
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        assetFileNames: (assetInfo) => {
          const name = assetInfo.name || "";
          if (name.endsWith(".css")) {
            return "app.css";
          }
          return "assets/[name][extname]";
        },
      },
    },
  },
});
