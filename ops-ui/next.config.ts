import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // In production we run behind a reverse proxy (NPM → Vision:3030).
    // Next.js rewrites /api/* → the local FastAPI on the same host.
    // Setting NEXT_PUBLIC_API_BASE in dev overrides this for hot-reload.
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
