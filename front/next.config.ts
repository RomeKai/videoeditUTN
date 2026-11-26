import type { NextConfig } from 'next';
import { PHASE_PRODUCTION_BUILD } from 'next/constants';

type ApiMode = 'mock' | 'hybrid' | 'real';

/** Same rule as `resolveApiMode` in src/api/config.ts (this file cannot import from src). */
function apiMode(): ApiMode {
  const mode = process.env.NEXT_PUBLIC_API_MODE?.trim().toLowerCase();
  if (mode === 'mock' || mode === 'hybrid' || mode === 'real') return mode;
  return process.env.NEXT_PUBLIC_API_MOCKS === 'true' ? 'mock' : 'real';
}

/** Opt-in for a production build that is not `real` (the Playwright builds). */
const MOCKS_IN_BUILD = 'API_MOCKS_IN_BUILD';

export default function config(phase: string): NextConfig {
  const mode = apiMode();
  // A production build with the mocks on ships MSW, the dev panel, /dev and (in hybrid) the identity bridge.
  // That is never what a deploy wants, and a stray .env.local is enough to get it: it has to be asked for.
  if (phase === PHASE_PRODUCTION_BUILD && mode !== 'real' && process.env[MOCKS_IN_BUILD] !== 'true') {
    throw new Error(
      `El build de producción quedó en modo "${mode}" (NEXT_PUBLIC_API_MODE / NEXT_PUBLIC_API_MOCKS). ` +
        `Un build de producción habla con el backend real: usá NEXT_PUBLIC_API_MODE=real, o ${MOCKS_IN_BUILD}=true si de verdad querés un build con mocks (los de Playwright).`,
    );
  }
  return {
    reactStrictMode: true,
    // The Playwright smoke builds with mocks on into its own folder (see playwright.config.ts).
    distDir: process.env.NEXT_DIST_DIR ?? '.next',
    // The repo root has no lockfile of its own: keep file tracing inside front/.
    outputFileTracingRoot: __dirname,
    // `geist` ships its fonts as ESM that imports next/font/local.
    transpilePackages: ['geist'],
    // The mode is decided when the app is built, for the server and the browser alike. Left to
    // `process.env`, a variable that is not set at build time is read by the server when it starts and is
    // undefined in the browser: the two render different trees (React #418).
    env: {
      NEXT_PUBLIC_API_MODE: mode,
      NEXT_PUBLIC_API_REAL: process.env.NEXT_PUBLIC_API_REAL ?? '',
    },
  };
}
