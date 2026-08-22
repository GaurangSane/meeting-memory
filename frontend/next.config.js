/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",        // needed for the multi-stage Docker build
  reactStrictMode: true,

  // Proxy /api/* calls to the FastAPI backend during local development.
  // NOTE: WebSocket (/ws/*) rewrites are intentionally absent — Next.js
  // rewrites do not support the ws:// protocol and will fail the production
  // build with "destination does not start with '/', 'http://', or 'https://'".
  // In production, nginx handles /ws/ proxying directly (see nginx.conf).
  // For local development, set NEXT_PUBLIC_WS_URL in AudioRecorder.tsx to
  // point directly at the backend (e.g. ws://localhost:8000).
  async rewrites() {
    if (!process.env.NEXT_PUBLIC_API_URL) {
      return [];
    }

    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
