import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FlatCompat } from '@eslint/eslintrc';

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  {
    ignores: [
      '.next/**',
      '.next-e2e/**',
      'node_modules/**',
      'public/mockServiceWorker.js',
      'playwright-report/**',
      'test-results/**',
      'next-env.d.ts',
    ],
  },
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
  {
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      // Screens talk to the backend through src/api hooks only.
      'no-restricted-globals': ['error', { name: 'fetch', message: 'Usá los hooks de src/api (o apiCall) en lugar de fetch.' }],
    },
  },
  {
    files: ['src/api/client.ts', 'src/api/mocks/**', 'e2e/**', '**/*.test.ts', '**/*.test.tsx', 'scripts/**'],
    rules: { 'no-restricted-globals': 'off' },
  },
];

export default config;
