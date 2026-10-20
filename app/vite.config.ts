import { createReadStream, existsSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import preact from "@preact/preset-vite";
import { defineConfig, type Plugin } from "vite";
import { VitePWA } from "vite-plugin-pwa";

/** Dev only: serves the repo's local database so `pnpm dev` shows data without importing a file first. */
const LOCAL_DB = fileURLToPath(new URL("../local/recetario.db", import.meta.url));
function devDatabase(): Plugin {
  return {
    name: "recetario-dev-database",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/dev/recetario.db", (_request, response) => {
        if (!existsSync(LOCAL_DB)) {
          response.statusCode = 404;
          response.end();
          return;
        }
        response.setHeader("Content-Type", "application/octet-stream");
        response.setHeader("Content-Length", statSync(LOCAL_DB).size);
        createReadStream(LOCAL_DB).pipe(response);
      });
    },
  };
}

export default defineConfig({
  plugins: [
    preact(),
    devDatabase(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg"],
      workbox: { globPatterns: ["**/*.{js,css,html,wasm,svg,png}"], maximumFileSizeToCacheInBytes: 5 * 1024 * 1024 },
      manifest: {
        name: "Recetario",
        short_name: "Recetario",
        description: "Recetas que puedes cocinar con lo que tienes en casa",
        lang: "es-MX",
        start_url: ".",
        display: "standalone",
        background_color: "#faf6ef",
        theme_color: "#b4532a",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
    }),
  ],
  base: "./",
  optimizeDeps: { exclude: ["@sqlite.org/sqlite-wasm"] },
});
