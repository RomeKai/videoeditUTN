import { configure } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { API_URL } from '@/api/config';
import { server } from '@/api/mocks/server';

/** Renders a screen over a seeded mock scenario (see `@/testing`). */
export { renderScenario as renderScreen, startMockApi } from '@/testing';

// These tests render whole screens against the mocked API; on a busy machine the first answer can
// take longer than the default second.
configure({ asyncUtilTimeout: 5000 });

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
