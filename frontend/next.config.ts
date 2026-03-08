import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
  },
  async rewrites() {
    return [
      {
        source: "/api/findr/:path*",
        destination: "http://localhost:8001/:path*",
      },
      {
        source: "/api/editr/:path*",
        destination: "http://localhost:8002/:path*",
      },
    ];
  },
};

export default nextConfig;
