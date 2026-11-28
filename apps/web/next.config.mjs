/** The browser talks to Next only; /api is proxied to FastAPI so session cookies stay first-party. */
const apiUrl = process.env.VERITY_API_URL ?? "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  // The OG image reads its font from disk, so the standalone build must carry it.
  outputFileTracingIncludes: { "/\\[locale\\]/opengraph-image": ["./src/assets/fonts/**"] },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
  async headers() {
    return [
      {
        // Everything except the chatbot widget, whose framing policy middleware.ts sets per chatbot.
        source: "/((?!widget/).*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
      { source: "/:path*", headers: [{ key: "X-Content-Type-Options", value: "nosniff" }] },
      { source: "/widget.js", headers: [{ key: "Cache-Control", value: "public, max-age=300" }, { key: "Access-Control-Allow-Origin", value: "*" }] },
    ];
  },
};

export default nextConfig;
