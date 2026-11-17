import type { NextConfig } from "next";

// Where FastAPI listens, from the Next.js server. `rewrites()` is read when the app is BUILT (the Docker image takes it
// as a build argument); the `/api/v1/*` proxy route handler reads it again at run time.
const backendInternalUrl = (process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // `NEXT_OUTPUT=standalone` (the Docker build) emits a self-contained server in .next/standalone.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" as const } : {}),
  async rewrites() {
    return [
      {
        source: "/api/time",
        destination: `${backendInternalUrl}/api/time`,
      },
      // Public question figures and PDF previews are plain GET files served by FastAPI: they go through the same
      // origin as the pages, so the browser never needs the API's address. `/api/v1/*` is NOT a rewrite: the proxy
      // route handler (src/app/api/v1/[...path]/route.ts) must keep stripping the legacy session cookie, naming the
      // client address and answering 502 when the API is down, which a rewrite cannot do.
      {
        source: "/media/:path*",
        destination: `${backendInternalUrl}/media/:path*`,
      },
    ];
  },
  webpack: (config) => {
    config.resolve.extensionAlias = {
      ...config.resolve.extensionAlias,
      ".js": [".ts", ".tsx", ".js"],
      ".jsx": [".tsx", ".jsx"],
    };
    return config;
  },
};

export default nextConfig;
