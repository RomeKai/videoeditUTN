import { QueryClient } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { renderScreen as renderInApp } from '@/testing';

export { appQueryClient, editDb, signIn } from '@/testing';

/**
 * Renders a screen of the review with the app's providers. Without a `client`
 * the cache dies with the screen (`gcTime: 0`): each render starts from the server.
 */
export function renderScreen(ui: ReactElement, client?: QueryClient) {
  return renderInApp(ui, { client: client ?? new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } }) });
}
