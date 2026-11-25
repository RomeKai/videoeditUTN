'use client';

import { keepPreviousData, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import type { Platform } from '../contracts/common';
import * as contract from '../contracts/social';
import { keys, pollWhile } from '../query';
import { useScopedQuery } from './scope';

const IN_FLIGHT = new Set(['QUEUED', 'PROCESSING']);

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

/** E2: polls every 5 s while any publication of the page is queued or publishing. */
export function usePosts(query: contract.PostsQuery = {}, { enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({
    queryKey: keys.social.postList(query),
    queryFn: ({ signal }) => apiCall(contract.listPosts, { query, signal }),
    placeholderData: keepPreviousData,
    enabled,
    ...pollWhile<{ results: contract.ScheduledPost[] }>((page) => Boolean(page?.results.some((post) => IN_FLIGHT.has(post.status)))),
  });
}

export function usePost(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: keys.social.post(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getPost, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
    ...pollWhile<contract.ScheduledPost>((post) => Boolean(post && (IN_FLIGHT.has(post.status) || post.seo_status === 'pending'))),
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

/** Edit text or reschedule (also the calendar drag). Only before QUEUED: 409 otherwise. */
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
