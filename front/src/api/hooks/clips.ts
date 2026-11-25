'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import * as contract from '../contracts/clips';
import type { Clip } from '../contracts/projects';
import { ApiError } from '../errors';
import { keys, pollWhile } from '../query';
import { scopedKey, useScopedQuery } from './scope';

export function useClip(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: keys.clips.detail(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getClip, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
    ...pollWhile<Clip>((clip) => clip?.status === 'rendering'),
  });
}

function useInvalidateClip() {
  const queryClient = useQueryClient();
  return (clipId: string) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.clips.detail(clipId) }),
      queryClient.invalidateQueries({ queryKey: keys.projects.all }),
    ]);
}

/** D4: title and trim. Saving does not render; call useRenderClip after confirming the cost. */
export function useUpdateClip(id: string) {
  const invalidate = useInvalidateClip();
  return useMutation({
    mutationFn: (body: contract.UpdateClipRequest) => apiCall(contract.updateClip, { params: { id }, body }),
    onSuccess: () => invalidate(id),
  });
}

export function useDeleteClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiCall(contract.deleteClip, { params: { id } }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.projects.all }),
        queryClient.invalidateQueries({ queryKey: keys.social.posts }),
      ]),
  });
}

/** Paid re-render of one clip: 402 opens D2. */
export function useRenderClip(id: string) {
  const invalidate = useInvalidateClip();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiCall(contract.renderClip, { params: { id } }),
    onSuccess: () => Promise.all([invalidate(id), queryClient.invalidateQueries({ queryKey: keys.billing.all })]),
  });
}

/** D3: retry a failed clip (free when the failure was ours). */
export function useRetryClip() {
  const invalidate = useInvalidateClip();
  return useMutation({
    mutationFn: (id: string) => apiCall(contract.retryClip, { params: { id } }),
    onSuccess: (_clip, id) => invalidate(id),
  });
}

/** Signed download link; the screen navigates to `url` and shows the toast "Clip descargado". */
export function useClipDownload() {
  return useMutation({ mutationFn: (id: string) => apiCall(contract.getClipDownload, { params: { id } }) });
}

/** Free 360p preview: queues one (202) or returns the one in progress (200). 409: not available; 429: too many at once. */
export function useRequestClipPreview(clipId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiCall(contract.requestClipPreview, { params: { clipId } }),
    onSuccess: (preview) => queryClient.setQueryData(scopedKey(keys.clips.preview(clipId)), preview),
  });
}

/** Current preview of a clip, or null when there is none (404). Polls every 5 s until ready or failed. */
export function useClipPreview(clipId: string | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({
    queryKey: keys.clips.preview(clipId ?? ''),
    queryFn: async ({ signal }) => {
      try {
        return await apiCall(contract.getClipPreview, { params: { clipId: clipId ?? '' }, signal });
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(clipId) && enabled,
    ...pollWhile<contract.ClipPreview | null>((preview) => preview?.status === 'pending' || preview?.status === 'rendering'),
  });
}
