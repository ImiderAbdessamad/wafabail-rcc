import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cpSync, mkdirSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
const publicPdfjs = fileURLToPath(new URL("./public/pdfjs", import.meta.url));

/**
 * PDF.js 5+ charge JBIG2 / JPEG2000 depuis des fichiers WASM. Sans eux, les
 * liasses scannées (cas le plus fréquent) échouent silencieusement.
 */
function copyPdfjsAssets() {
  mkdirSync(publicPdfjs, { recursive: true });
  for (const folder of ["wasm", "cmaps", "standard_fonts"]) {
    cpSync(
      `${root}/node_modules/pdfjs-dist/${folder}`,
      `${publicPdfjs}/${folder}`,
      { recursive: true },
    );
  }
}

copyPdfjsAssets();

/**
 * Le build alimente `static/`, servi tel quel par FastAPI (main.py monte
 * StaticFiles sur "/"). En développement, `npm run dev` sert l'interface sur
 * le port 5173 et relaie l'API vers uvicorn.
 */
export default defineConfig({
  plugins: [react()],
  base: "/",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: fileURLToPath(new URL("../static", import.meta.url)),
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        // Empreinte dans le nom de fichier : plus de modules ES servis depuis
        // un cache navigateur périmé après un déploiement.
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
        // SSE : pas de mise en tampon, sinon la progression arrive d'un bloc
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if ((proxyRes.headers["content-type"] || "").includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
            }
          });
        },
      },
    },
  },
});
