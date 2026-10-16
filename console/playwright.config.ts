import { defineConfig } from "@playwright/test";

// End-to-end tests against a running gateway that serves the built console.
// TWINLM_URL defaults to a local throwaway gateway; ADMIN_TOKEN must match it.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.TWINLM_URL || "http://localhost:8123",
    channel: process.env.PW_CHANNEL || "msedge",
    viewport: { width: 1440, height: 900 },
  },
});
