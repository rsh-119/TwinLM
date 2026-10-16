import type { NextConfig } from "next";

// Production: a static export served by the gateway at /app.
// Development (`npm run dev`): the same /app paths, with the gateway's API
// proxied from http://localhost:8000 so cookies stay same-origin.
const dev = process.env.NODE_ENV === "development";
const GATEWAY = process.env.TWINLM_GATEWAY || "http://localhost:8000";

const nextConfig: NextConfig = {
  basePath: "/app",
  trailingSlash: true,
  images: { unoptimized: true },
  ...(dev
    ? {
        async rewrites() {
          return ["auth", "admin", "console", "v1"].map((p) => ({
            source: `/${p}/:path*`,
            destination: `${GATEWAY}/${p}/:path*`,
            basePath: false as const,
          }));
        },
      }
    : { output: "export" as const }),
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
