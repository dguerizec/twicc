import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";

// NOTE: @cloudflare/vitest-pool-workers 0.18.x (the version pinned in this
// project's package.json, for vitest 4) dropped the
// "@cloudflare/vitest-pool-workers/config" subpath and its
// defineWorkersConfig()/defineWorkersProject() helpers in favor of a plain
// vitest/config defineConfig() plus a cloudflareTest() Vite plugin — see the
// package's own dist/codemods/vitest-v3-to-v4.mjs migration script.
export default defineConfig({
  test: {
    setupFiles: ["./test/apply-migrations.js"],
  },
  plugins: [
    cloudflareTest(async () => {
      // fileURLToPath keeps this working on Node 18/20 alike
      // (import.meta.dirname only exists from Node 20.11).
      const migrations = await readD1Migrations(fileURLToPath(new URL("migrations/", import.meta.url)));
      return {
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations },
        },
      };
    }),
  ],
});
