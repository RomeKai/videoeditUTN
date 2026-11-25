import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The Playwright smoke builds with mocks on into its own folder (see playwright.config.ts).
  distDir: process.env.NEXT_DIST_DIR ?? '.next',
  // The repo root has no lockfile of its own: keep file tracing inside front/.
  outputFileTracingRoot: __dirname,
  // `geist` ships its fonts as ESM that imports next/font/local.
  transpilePackages: ['geist'],
};

export default nextConfig;
