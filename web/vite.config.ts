import { defineConfig } from "vite";

/**
 * `npm run build` deja la interfaz dentro del paquete Python (mapeador_py/web), que es
 * lo que sirve `python -m mapeador_py servir`: en producción no hace falta Node.
 *
 * En desarrollo (`npm run dev`) la API la atiende el servidor Python
 * (python -m mapeador_py servir --puerto 8080) y Vite le reenvía /api.
 */
const API = process.env.MAPEADOR_PY_API || "http://127.0.0.1:8080";

export default defineConfig({
  base: "./",
  build: { target: "es2022", chunkSizeWarningLimit: 1000, outDir: "../mapeador_py/web", emptyOutDir: true },
  server: { host: true, port: 5173, proxy: { "/api": { target: API, changeOrigin: true } } },
  preview: { proxy: { "/api": { target: API, changeOrigin: true } } },
});
