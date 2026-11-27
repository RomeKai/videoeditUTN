'use client';

import { keepPreviousData, useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { apiCall } from '../client';
import type { Platform } from '../contracts/common';
import * as contract from '../contracts/social';
import { keys, pollWhile } from '../query';
import { useScopedQuery } from './scope';

const IN_FLIGHT = new Set(['QUEUED', 'PROCESSING']);

/**
 * On its way out: queued or publishing, or scheduled for a time that already came. "Publish now" (and a retry)
 * leaves the post `SCHEDULED` for now, and the beat of the backend queues it within five minutes.
 */
export function goingOut(post: Pick<contract.ScheduledPost, 'status' | 'publish_at'>, now = Date.now()): boolean {
  return IN_FLIGHT.has(post.status) || (post.status === 'SCHEDULED' && Date.parse(post.publish_at) <= now);
}

/** How long a publication is polled for its AI text. After that the UI stops waiting and the user writes it. */
export const SEO_WAIT_MS = 120_000;

/**
 * The AI text of the post is still expected: `pending`, and asked for less than two minutes ago
 * (the creation, or the last change, which is what "Regenerar" touches). A generation that never
 * answers must not keep a screen polling forever.
 */
export function seoPending(post: Pick<contract.ScheduledPost, 'seo_status' | 'created_at' | 'updated_at'>, now = Date.now()): boolean {
  if (post.seo_status !== 'pending') return false;
  const since = Math.max(Date.parse(post.created_at) || 0, Date.parse(post.updated_at) || 0);
  return now - since < SEO_WAIT_MS;
}

export function useSocialAccounts() {
  return useScopedQuery({ queryKey: keys.social.accounts, queryFn: ({ signal }) => apiCall(contract.listSocialAccounts, { signal }) });
}

/** D6: returns the network's authorization URL; the dialog opens it in a new window and refetches accounts on return. */
export function useConnectSocialAccount() {
  return useMutation({
    mutationFn: (body: { platform: Platform; return_url?: string }) => apiCall(contract.connectSocialAccount, { body }),
  });
}

export function useDisconnectSocialAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (platform: Platform) => apiCall(contract.disconnectSocialAccount, { params: { platform } }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.social.accounts }),
        queryClient.invalidateQueries({ queryKey: keys.social.posts }),
      ]),
  });
}

/** E2 and E1: polls every 5 s while any publication of the page is going out (`goingOut`) or waiting for its AI text (at most two minutes each). */
export function usePosts(query: contract.PostsQuery = {}, { enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({
    queryKey: keys.social.postList(query),
    queryFn: ({ signal }) => apiCall(contract.listPosts, { query, signal }),
    placeholderData: keepPreviousData,
    enabled,
    ...pollWhile<{ results: contract.ScheduledPost[] }>((page) =>
      Boolean(page?.results.some((post) => goingOut(post) || seoPending(post))),
    ),
  });
}

export function usePost(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: keys.social.post(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getPost, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
    ...pollWhile<contract.ScheduledPost>((post) => Boolean(post && (goingOut(post) || seoPending(post)))),
  });
}

function useInvalidatePosts() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.social.posts }),
      queryClient.invalidateQueries({ queryKey: keys.projects.all }),
      queryClient.invalidateQueries({ queryKey: keys.clips.all }),
    ]);
}

/** E1: publish now (no `publish_at`) or schedule. 403 → plan (scheduling/crossposting); 409 → reconnect the account. */
export function useCreatePost() {
  const invalidate = useInvalidatePosts();
  return useMutation({ mutationFn: (body: contract.CreatePostInput) => apiCall(contract.createPost, { body }), onSuccess: invalidate });
}

/** E1c: several clips and networks at once. */
export function useCreatePostsBulk() {
  const invalidate = useInvalidatePosts();
  return useMutation({
    mutationFn: (posts: contract.CreatePostInput[]) => apiCall(contract.createPostsBulk, { body: { posts } }),
    onSuccess: invalidate,
  });
}

/** Edit text, reschedule (also the calendar drag) or publish a draft (`publish_at`: date or null for now). Only before QUEUED: 409 otherwise. */
export function useUpdatePost() {
  const invalidate = useInvalidatePosts();
  return useMutation({
    mutationFn: ({ id, ...body }: contract.UpdatePostRequest & { id: string }) => apiCall(contract.updatePost, { params: { id }, body }),
    onSuccess: invalidate,
  });
}

export function useCancelPost() {
  const invalidate = useInvalidatePosts();
  return useMutation({ mutationFn: (id: string) => apiCall(contract.cancelPost, { params: { id } }), onSuccess: invalidate });
}

export function useRetryPost() {
  const invalidate = useInvalidatePosts();
  return useMutation({ mutationFn: (id: string) => apiCall(contract.retryPost, { params: { id } }), onSuccess: invalidate });
}

/** E1 · "Regenerar": asks the AI for a new caption and hashtags; the post comes back with `seo_status: 'pending'`. */
export function useRegeneratePostSeo() {
  const invalidate = useInvalidatePosts();
  return useMutation({ mutationFn: (id: string) => apiCall(contract.regeneratePostSeo, { params: { id } }), onSuccess: invalidate });
}

/**
 * E1: the calls of the publish panel without the cache invalidation of the mutations above.
 * The panel creates and discards helper drafts one by one and sends a batch of publications:
 * refetching every list after each call would be a storm, so it calls `invalidate()` once at the end.
 */
export function usePostActions() {
  const queryClient = useQueryClient();
  return useMemo(
    () => ({
      list: (query: contract.PostsQuery, signal?: AbortSignal) => apiCall(contract.listPosts, { query, signal }),
      get: (id: string) => apiCall(contract.getPost, { params: { id } }),
      /** `idempotencyKey`: one per submit of the person, the same when that submit is retried (it must not publish twice). */
      create: (body: contract.CreatePostInput, idempotencyKey?: string) => apiCall(contract.createPost, { body, idempotencyKey }),
      createBulk: (posts: contract.CreatePostInput[], idempotencyKey?: string) => apiCall(contract.createPostsBulk, { body: { posts }, idempotencyKey }).then((data) => data.posts),
      update: ({ id, ...body }: contract.UpdatePostRequest & { id: string }) => apiCall(contract.updatePost, { params: { id }, body }),
      /** `workspaceId`: the draft's own workspace, for a panel that is closing while the active one changes. */
      discard: (id: string, workspaceId?: string) => apiCall(contract.cancelPost, { params: { id }, workspaceId }),
      regenerate: (id: string) => apiCall(contract.regeneratePostSeo, { params: { id } }),
      invalidate: () =>
        Promise.all([
          queryClient.invalidateQueries({ queryKey: keys.social.posts }),
          queryClient.invalidateQueries({ queryKey: keys.projects.all }),
          queryClient.invalidateQueries({ queryKey: keys.clips.all }),
        ]),
    }),
    [queryClient],
  );
}
