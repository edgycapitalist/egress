/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The frontend is a thin client over the gateway; no server-side secrets.
  output: "standalone",
};

export default nextConfig;
