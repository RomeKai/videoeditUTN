import { MutationCache, QueryClient } from '@tanstack/react-query';
import { POLL_INTERVAL_MS } from './config';
import { ApiError, ContractError, NetworkError, SessionExpiredError, isOutcomeUnknown } from './errors';

/** Retry only what can get better by itself: no answer from the network, or a 5xx. Once. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false;
  if (error instanceof SessionExpiredError || error instanceof ContractError) return false;
  if (error instanceof ApiError) return error.status >= 500 && error.status !== 501;
  return error instanceof NetworkError;
}

export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    mutationCache: new MutationCache({
      // A write whose answer never came may have been done: everything on screen is read again, so
      // the person sees what really happened (the project that was created, the balance) before retrying.
      onError: (error) => {
        if (isOutcomeUnknown(error)) void client.invalidateQueries();
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: shouldRetry,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  });
  return client;
}

/**
 * Live update rule of map 1.4: poll every 5 s while `active(data)` says something
 * is in progress, stop on a final state. TanStack Query already pauses interval
 * refetching while the tab is hidden (refetchIntervalInBackground is false).
 */
export function pollWhile<T>(active: (data: T | undefined) => boolean) {
  return {
    refetchInterval: (query: { state: { data?: unknown } }) => (active(query.state.data as T | undefined) ? POLL_INTERVAL_MS : false),
    refetchIntervalInBackground: false,
  } as const;
}

/** Query keys, one tree per domain, so mutations can invalidate precisely. */
export const keys = {
  me: ['me'] as const,
  notificationPreferences: ['me', 'notification-preferences'] as const,
  workspaces: {
    all: ['workspaces'] as const,
    detail: (id: string) => ['workspaces', id] as const,
    members: (id: string) => ['workspaces', id, 'members'] as const,
    invitation: (token: string) => ['invitations', token] as const,
  },
  billing: {
    all: ['billing'] as const,
    plans: ['plans'] as const,
    pricing: ['pricing'] as const,
    estimate: (input: unknown) => ['billing', 'estimate', input] as const,
    subscription: ['billing', 'subscription'] as const,
    wallet: ['billing', 'wallet'] as const,
    usage: (month?: string) => ['billing', 'usage', month ?? 'current'] as const,
    transactions: (query: unknown) => ['billing', 'transactions', query] as const,
    packages: ['billing', 'packages'] as const,
    purchase: (id: string) => ['billing', 'purchase', id] as const,
  },
  projects: {
    all: ['projects'] as const,
    list: (query: unknown) => ['projects', 'list', query] as const,
    detail: (id: string) => ['projects', 'detail', id] as const,
    paperEdit: (id: string) => ['projects', 'detail', id, 'paper-edit'] as const,
    activity: (id: string) => ['projects', 'detail', id, 'activity'] as const,
  },
  clips: {
    all: ['clips'] as const,
    detail: (id: string) => ['clips', id] as const,
    preview: (id: string) => ['clips', id, 'preview'] as const,
  },
  brandKits: {
    all: ['brand-kits'] as const,
    detail: (id: string) => ['brand-kits', id] as const,
  },
  social: {
    accounts: ['social', 'accounts'] as const,
    posts: ['social', 'posts'] as const,
    postList: (query: unknown) => ['social', 'posts', 'list', query] as const,
    post: (id: string) => ['social', 'posts', 'detail', id] as const,
  },
  notifications: ['notifications'] as const,
  system: ['system', 'status'] as const,
} as const;
