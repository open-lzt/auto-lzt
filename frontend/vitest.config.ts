import { defineConfig } from "vitest/config";

export default defineConfig({
  // A linked @open-lzt/ui carries its own react; without these the kit's hooks run against a
  // second React instance and every kit component throws "Invalid hook call". A symlinked
  // dependency is external to vite by default, so inlining it is what makes dedupe apply.
  resolve: { dedupe: ["react", "react-dom"] },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
    server: { deps: { inline: ["@open-lzt/ui"] } },
  },
});
