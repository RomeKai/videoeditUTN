'use client';

import { useQuery, type DefaultError, type Query, type QueryKey, type UseQueryOptions, type UseQueryResult } from '@tanstack/react-query';
import { useMemo, useSyncExternalStore } from 'react';
import { getAccessSnapshot, getActiveWorkspaceId, getDeferredWorkspaceSnapshot, getWorkspaceSnapshot, subscribeSession } from '../session';

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

/** The workspace another tab switched to while this one kept its own (it has unsaved changes), or null. */
export function useDeferredWorkspaceId(): string | null {
  return useSyncExternalStore(subscribeSession, getDeferredWorkspaceSnapshot, () => null);
}

type AnyQuery = Pick<Query, 'queryKey' | 'meta'>;
const workspaceOf = (query: AnyQuery): unknown => query.meta?.workspace ?? query.queryKey[query.queryKey.length - 1];

/**
 * Whether the cache entry holds data of that workspace: a query of `useScopedQuery` (it says so in
 * its `meta`), or one whose key ends with the workspace id (`scopedKey`).
 */
export const isScopedTo = (workspaceId: string) => (query: AnyQuery): boolean => workspaceOf(query) === workspaceId;

/** Whether the cache entry belongs to no workspace of `workspaceIds` (the session, the plans, the prices…). */
export const isUnscoped = (workspaceIds: Iterable<string>) => {
  const ids = new Set(workspaceIds);
  return (query: AnyQuery): boolean => query.meta?.workspace === undefined && !ids.has(query.queryKey[query.queryKey.length - 1] as string);
};

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
  const { placeholderData, meta: ownMeta } = options;
  // Lets a workspace switch tell this entry from the ones that belong to no workspace. Kept stable:
  // a new object on every render would make the observer take new options on every render.
  const meta = useMemo(() => (workspaceId ? { ...ownMeta, workspace: workspaceId } : ownMeta), [ownMeta, workspaceId]);
  return useQuery({
    ...options,
    // `keepPreviousData` (and any function) only keeps what belongs to this workspace: after a switch the
    // previous key is the other workspace's, and its rows would lead to 404s.
    ...(typeof placeholderData === 'function'
      ? {
          placeholderData: ((previous: unknown, previousQuery: { queryKey: QueryKey } | undefined) =>
            previousQuery && previousQuery.queryKey[previousQuery.queryKey.length - 1] === workspaceId
              ? (placeholderData as (data: unknown, query: unknown) => unknown)(previous, previousQuery)
              : undefined) as typeof placeholderData,
        }
      : {}),
    queryKey: [...options.queryKey, workspaceId] as unknown as TQueryKey,
    meta,
    enabled: Boolean(workspaceId) && options.enabled !== false,
  } as UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>);
}
