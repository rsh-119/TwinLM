import type { NextConfig } from "next";
import createMDX from "@next/mdx";

// Static export for GitHub Pages. The Pages workflow sets PAGES_BASE_PATH
// (e.g. "/TwinLM"); locally the site serves from "/".
const nextConfig: NextConfig = {
  output: "export",
  basePath: process.env.PAGES_BASE_PATH || "",
  trailingSlash: true, // /docs/quickstart/index.html: works on any static host
  images: { unoptimized: true },
  pageExtensions: ["ts", "tsx", "mdx"],
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

const withMDX = createMDX({
  options: {
    remarkPlugins: ["remark-gfm"],
    rehypePlugins: ["rehype-slug"],
  },
});

export default withMDX(nextConfig);
