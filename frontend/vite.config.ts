import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy forwards /api to the local FastAPI backend so the SPA never hardcodes a host.
export default defineConfig({
  plugins: [react()],
  // A linked @open-lzt/ui carries its own react in node_modules; without dedupe the kit's hooks
  // run against a second React instance and every kit component throws "Invalid hook call".
  resolve: { dedupe: ["react", "react-dom"] },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
