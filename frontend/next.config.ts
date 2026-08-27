import type { NextConfig } from "next";

// The app is a pure client SPA (every page is "use client", all data is fetched
// from the API at runtime), so it statically exports to `out/` for Cloudflare
// Pages. The Docker image (single-origin beta / local compose) sets
// NEXT_OUTPUT=standalone to build the Node server flavour instead.
const output =
  process.env.NEXT_OUTPUT === "standalone" ? "standalone" : "export";

const nextConfig: NextConfig = {
  output,
  // No Next image optimizer on a static host.
  images: { unoptimized: true },
  // Emit directory-style routes (out/resources/index.html) so Pages serves
  // /resources without a rewrite rule.
  trailingSlash: true,
  // Hide the floating dev-tools indicator (bottom-left "N") in `next dev`.
  devIndicators: false,
};

export default nextConfig;
