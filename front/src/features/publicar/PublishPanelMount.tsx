'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useMemo, useRef } from 'react';
import type { Platform } from '@/api/contracts/common';
import { ApiError } from '@/api/errors';
import { useActiveWorkspaceId, usePostActions } from '@/api/hooks';
import { PublishPanel } from './PublishPanel';
import { findOrphanDrafts, forgetDraft, isStillOrphan } from './draftRegistry';
import { PLATFORMS, isUntouchedDraft } from './posts';

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

/**
 * Where the tab is in its history: the entry's index where the browser tells (Navigation API),
 * or the number of entries. It only grows when a navigation adds an entry.
 */
function historyPosition(): number {
  const index = (window as { navigation?: { currentEntry?: { index?: number } | null } }).navigation?.currentEntry?.index;
  return typeof index === 'number' && index >= 0 ? index : window.history.length;
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
    let cancelled = false;
    let again: number | undefined;
    const sweep = async () => {
      const { orphans, retryAt } = await findOrphanDrafts(workspaceId, open.current);
      if (cancelled) return;
      // Some were kept only because a panel used them a moment ago (a reload, a tab that is slow to
      // answer): if nobody claims them by then, they go in the next pass.
      if (retryAt !== null) again = window.setTimeout(() => void sweep(), Math.max(1000, retryAt - Date.now()));
      let discarded = 0;
      for (const entry of orphans) {
        // A panel may have taken it back while this was running.
        if (!isStillOrphan(entry, open.current)) continue;
        try {
          const post = await actions.get(entry.id);
          if (cancelled) return;
          // Asked again after the wait, right before throwing it away: the panel of its clip may have just opened.
          if (!isStillOrphan(entry, open.current)) continue;
          // Only a draft nobody touched is thrown away: one that went out, or that a person edited, is theirs.
          if (isUntouchedDraft(post)) {
            await actions.discard(entry.id);
            discarded += 1;
          }
          forgetDraft(entry.id);
        } catch (error) {
          // Gone already: nothing to clean up. Anything else is tried again next time.
          if (error instanceof ApiError && (error.status === 404 || error.status === 409)) forgetDraft(entry.id);
        }
      }
      if (discarded > 0) void actions.invalidate();
    };
    void sweep();
    return () => {
      cancelled = true;
      window.clearTimeout(again);
    };
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
  // leaving a second copy of the screen in the history. A link that arrives with the parameter, or a
  // screen that replaces its URL to open it, has no entry to go back to.
  const isOpen = clipIds.length > 0;
  const before = useRef<number | null>(null);
  const wasOpen = useRef(false);
  const pushed = useRef(false);
  useEffect(() => {
    if (!isOpen) {
      before.current = historyPosition();
      pushed.current = false;
    } else if (!wasOpen.current) {
      pushed.current = before.current !== null && historyPosition() > before.current;
    }
    wasOpen.current = isOpen;
  });

  const close = useCallback(() => {
    if (pushed.current) {
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
