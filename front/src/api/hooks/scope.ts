'use client';

import { useQuery, type DefaultError, type QueryKey, type UseQueryOptions, type UseQueryResult } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { getAccessSnapshot, getActiveWorkspaceId, getWorkspaceSnapshot, subscribeSession } from '../session';

/**
 * Whether the browser holds a session. `undefined` while it is not known yet:
 * on the server and during hydration there is no localStorage, and deciding
 * "no session" there would send a signed-in user to the login on every reload.
 */
export function useSessionPresence(): boolean | undefined {
  const snapshot = useSyncExternalStore<string | null | undefined>(subscribeSession, getAccessSnapshot, () => undefined);
  return snapshot === undefined ? undefined : snapshot !== null;
}

/** True while there is a stored session (does not say it is still valid). False while unknown. */
export function useHasSession(): boolean {
  return useSessionPresence() === true;
}

/** Id of the active workspace (what the client sends in X-Workspace-Id), or null until the session picks one. */
export function useActiveWorkspaceId(): string | null {
  return useSyncExternalStore(subscribeSession, getWorkspaceSnapshot, () => null);
}

/** A query key with the active workspace appended: for setQueryData on a workspace-scoped query. */
export function scopedKey<K extends QueryKey>(key: K): readonly [...K, string | null] {
  return [...key, getActiveWorkspaceId()] as const;
}

/**
 * useQuery for data that belongs to a workspace. It waits until the active
 * workspace is known (so the first request already carries X-Workspace-Id) and
 * keeps one cache entry per workspace: switching never shows the other one's data.
 * The workspace id goes at the end of the key, so invalidating by prefix still works.
 */
export function useScopedQuery<TQueryFnData = unknown, TError = DefaultError, TData = TQueryFnData, TQueryKey extends QueryKey = QueryKey>(
  options: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>,
): UseQueryResult<TData, TError> {
  const workspaceId = useActiveWorkspaceId();
  return useQuery({
    ...options,
    queryKey: [...options.queryKey, workspaceId] as unknown as TQueryKey,
    enabled: Boolean(workspaceId) && options.enabled !== false,
  } as UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>);
}
