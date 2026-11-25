'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useMemo, useRef } from 'react';
import type { Platform } from '@/api/contracts/common';
import { ApiError } from '@/api/errors';
import { useActiveWorkspaceId, usePostActions } from '@/api/hooks';
import { PublishPanel } from './PublishPanel';
import { forgetDraft, orphanDrafts } from './draftRegistry';
import { PLATFORMS } from './posts';

/** Query parameter that opens the panel: `?publicar=<clipId>[,<clipId>…]` (front/ROUTES.md). */
export const PUBLISH_PARAM = 'publicar';
/** Optional: the networks ticked when the panel opens, `&red=INSTAGRAM_REELS[,…]`. */
export const NETWORK_PARAM = 'red';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** The bulk endpoint takes 60 publications: 20 clips in up to 3 networks. */
export const MAX_CLIPS = 20;

/** Clip ids of the parameter: valid, without repeats, in order. */
export function parsePublishParam(value: string | null | undefined): string[] {
  if (!value) return [];
  const ids: string[] = [];
  for (const part of value.split(',')) {
    const id = part.trim();
    if (UUID.test(id) && !ids.includes(id)) ids.push(id);
  }
  return ids.slice(0, MAX_CLIPS);
}

/** Link that opens the panel over `pathname`, keeping the rest of its query. */
export function publishHref(pathname: string, clipIds: string[], search?: string, platforms: Platform[] = []): string {
  const params = new URLSearchParams(search ?? '');
  params.set(PUBLISH_PARAM, clipIds.join(','));
  if (platforms.length > 0) params.set(NETWORK_PARAM, platforms.join(','));
  else params.delete(NETWORK_PARAM);
  return `${pathname}?${params.toString().replace(/%2C/gi, ',')}`;
}

/** Networks of `?red=`: only valid ones, without repeats. */
export function parseNetworkParam(value: string | null | undefined): Platform[] {
  if (!value) return [];
  const wanted = value.split(',').map((part) => part.trim());
  return PLATFORMS.filter((platform) => wanted.includes(platform));
}

/**
 * Discards the helper drafts that a panel of this browser left behind without
 * being able to clean up (a reload, a closed tab, a workspace switch). Runs when
 * the app opens and when the workspace changes, never while that panel is open.
 */
function useDraftSweeper(openClipIds: string[]): void {
  const actions = usePostActions();
  const workspaceId = useActiveWorkspaceId();
  const open = useRef(openClipIds);
  open.current = openClipIds;
  useEffect(() => {
    if (!workspaceId) return;
    const orphans = orphanDrafts(workspaceId, open.current);
    if (orphans.length === 0) return;
    void Promise.all(
      orphans.map((entry) =>
        actions.discard(entry.id).then(
          () => forgetDraft(entry.id),
          // Gone already, or it went out: either way it is not a helper draft any more.
          (error: unknown) => {
            if (error instanceof ApiError && (error.status === 404 || error.status === 409)) forgetDraft(entry.id);
          },
        ),
      ),
    ).then(() => actions.invalidate());
  }, [actions, workspaceId]);
}

function Mount() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const value = params.get(PUBLISH_PARAM);
  const clipIds = useMemo(() => parsePublishParam(value), [value]);
  const network = params.get(NETWORK_PARAM);
  const platforms = useMemo(() => parseNetworkParam(network), [network]);
  useDraftSweeper(clipIds);

  // A screen that opens the panel pushes the parameter: closing goes back to that entry instead of
  // leaving a second copy of the screen in the history. A link that arrives with the parameter has nothing to go back to.
  const isOpen = clipIds.length > 0;
  const first = useRef(true);
  const pushed = useRef(false);
  useEffect(() => {
    if (first.current) first.current = false;
    else pushed.current = isOpen;
  }, [isOpen]);

  const close = useCallback(() => {
    if (pushed.current && window.history.length > 1) {
      pushed.current = false;
      router.back();
      return;
    }
    const next = new URLSearchParams(params.toString());
    next.delete(PUBLISH_PARAM);
    next.delete(NETWORK_PARAM);
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [params, pathname, router]);
  if (!isOpen) return null;
  // A different selection is a different panel: its state starts over.
  return <PublishPanel key={`${clipIds.join(',')}|${platforms.join(',')}`} clipIds={clipIds} platforms={platforms} onClose={close} />;
}

/**
 * Mount point of E1 · Publicar for every screen with the app shell. Any screen
 * opens the panel by adding `?publicar=<clipId>[,…]` to its own URL; closing it
 * removes the parameter and leaves the screen as it was.
 */
export function PublishPanelMount() {
  return (
    <Suspense fallback={null}>
      <Mount />
    </Suspense>
  );
}
