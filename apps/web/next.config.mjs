/** @type {import('next').NextConfig} */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  basePath: basePath || undefined,
  reactStrictMode: true,
  async rewrites() {
    const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";

    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
