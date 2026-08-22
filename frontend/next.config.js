/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",        // needed for the multi-stage Docker build
  reactStrictMode: true,

  // Proxy same-origin /api/* requests to the FastAPI backend.
  // BACKEND_URL is server-side only so browser API requests stay on the
  // frontend origin, allowing HttpOnly auth cookies to work with middleware.
  // WebSockets connect directly to Railway via NEXT_PUBLIC_WS_URL.
  async rewrites() {
    if (!process.env.BACKEND_URL) {
      return [];
    }

    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
