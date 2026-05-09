/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Production installs often omit devDependencies (ESLint); lint locally with `npm run lint`.
  eslint: {
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
