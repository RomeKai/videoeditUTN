import { configure, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { http, HttpResponse } from 'msw';
import { API_URL } from '@/api/config';
import { server } from '@/api/mocks/server';
import { renderScreen as renderInApp, startScenario } from '@/testing';

export { startMockApi } from '@/testing';

// These tests render whole screens against the mocked API; on a busy machine the first answer can
// take longer than the default second.
configure({ asyncUtilTimeout: 5000 });

/**
 * Renders a screen the way the app does (see `@/testing`) over a seeded mock
 * scenario, which also signs in the scenario's user.
 */
export function renderScreen(
  ui: ReactElement,
  { scenario = 'estudio-norte', prepare }: { scenario?: string; /** Changes the seeded database before the screen asks for anything. */ prepare?: () => void } = {},
): RenderResult {
  startScenario(scenario);
  prepare?.();
  return renderInApp(ui);
}

/**
 * Makes one read of the API answer 500 while the rest (the session included)
 * keeps working: the state behind a screen's own error banner. Returns a
 * function that brings the endpoint back, for the "Reintentar" half of a test.
 */
export function failRead(path: string | RegExp): () => void {
  const matcher = typeof path === 'string' ? `${API_URL}${path}` : path;
  server.use(http.get(matcher, () => HttpResponse.json({ detail: 'Error interno.' }, { status: 500 })));
  return () => server.resetHandlers();
}
