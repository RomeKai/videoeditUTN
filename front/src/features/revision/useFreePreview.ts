'use client';

import { useEffect, useState } from 'react';
import type { ClipPreview } from '@/api/contracts/clips';
import { ApiError } from '@/api/errors';
import { useClipPreview, useRequestClipPreview } from '@/api/hooks';
import { errorMessage } from '@/lib/error-messages';

export type FreePreviewState = 'idle' | 'working' | 'ready' | 'failed';

export interface FreePreview {
  state: FreePreviewState;
  preview: ClipPreview | null;
  /** Why the request was refused (409: not available for this project; 429: too many at once). */
  error: string | null;
  isRequesting: boolean;
  request: () => void;
}

/**
 * PTE-03: free 360p preview of a clip as it is saved. It is asked for on
 * demand and then polled every 5 s until it is ready or failed; nothing is
 * fetched before the person asks (no preview yet is a 404, not an error).
 */
export function useFreePreview(clipId: string | null): FreePreview {
  const [requestedFor, setRequestedFor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRequestClipPreview(clipId ?? '');
  const requested = Boolean(clipId) && requestedFor === clipId;
  const query = useClipPreview(clipId, { enabled: requested });
  const preview = requested ? (query.data ?? null) : null;

  useEffect(() => {
    setError(null);
  }, [clipId]);

  const state: FreePreviewState = !preview
    ? request.isPending
      ? 'working'
      : 'idle'
    : preview.status === 'ready'
      ? 'ready'
      : preview.status === 'failed'
        ? 'failed'
        : 'working';

  return {
    state,
    preview,
    error,
    isRequesting: request.isPending,
    request: () => {
      if (!clipId || request.isPending) return;
      setError(null);
      request.mutate(undefined, {
        onSuccess: () => setRequestedFor(clipId),
        onError: (reason) => {
          if (reason instanceof ApiError && reason.status === 429) setError('Ya hay vistas previas en proceso. Probá de nuevo en unos segundos.');
          else if (reason instanceof ApiError && reason.status === 409) setError(reason.message);
          else setError(errorMessage(reason, 'No pudimos pedir la vista previa. Probá de nuevo.'));
        },
      });
    },
  };
}
