import type { QueryClient } from '@tanstack/react-query';
import type { RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { renderScreen as renderInApp, startScenario } from '@/testing';

export { db, idle } from '@/testing';

/**
 * Test helper: renders a screen the way the app does (see `@/testing`) against
 * the mocked API of a scenario. The test file starts the MSW server (`server`
 * from '@/api/mocks/server').
 */
export function renderScreen(
  ui: ReactElement,
  { scenario = 'estudio-norte', keep = false }: { scenario?: string; keep?: boolean } = {},
): RenderResult & { client: QueryClient } {
  // Seeds the scenario and signs in the user it starts with (Lucía, or the role of the scenario).
  // `keep`: a second app over the data another render already has (another tab of the same browser).
  if (!keep) startScenario(scenario);
  const view = renderInApp(ui);
  return Object.assign(view, { client: view.queryClient });
}

/** Ids of the default scenario: the clips of «Vlog Mendoza» (project 5), all rendered, with SEO. */
export const CLIPS = {
  first: '00000008-0000-4000-8000-0000000001f5',
  second: '00000008-0000-4000-8000-0000000001f6',
  third: '00000008-0000-4000-8000-0000000001f7',
} as const;
