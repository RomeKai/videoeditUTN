'use client';

import { useQueries, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import type { Platform } from '@/api/contracts/common';
import type { PostsQuery, ScheduledPost } from '@/api/contracts/social';
import { seoPending, useActiveWorkspaceId, usePostActions } from '@/api/hooks';
import { keys } from '@/api/query';
import { forgetDraft, isOwnDraft, rememberDraft } from './draftRegistry';
import { pairKey, type PairKey } from './publish';

/** While a draft waits for its AI text the panel asks again this often. */
const DRAFT_POLL_MS = 2_000;

interface DraftPage {
  count: number;
  next: string | null;
  previous: string | null;
  results: ScheduledPost[];
}

export interface PanelPair {
  clipId: string;
  platform: Platform;
}

export interface PanelDrafts {
  /** The drafts of every clip were read after the panel opened. */
  loaded: boolean;
  /** Draft of the pair as rendered. */
  draftOf: (clipId: string, platform: Platform) => ScheduledPost | undefined;
  /** Draft of the pair as the cache has it right now (after `whenIdle()` it includes the ones just created). */
  read: (clipId: string, platform: Platform) => ScheduledPost | undefined;
  /** Resolves when no draft is being created or discarded. */
  whenIdle: () => Promise<void>;
  /** Replaces the cached copy of a draft (after "Regenerar"). */
  put: (post: ScheduledPost) => void;
  /** The draft was published or scheduled: it is no longer a draft and it is never discarded. */
  markSent: (post: Pick<ScheduledPost, 'id' | 'video_clip'>) => void;
  /** The draft is gone from the server: forget it. */
  drop: (post: Pick<ScheduledPost, 'id' | 'video_clip'>) => void;
  /** Stops creating drafts and discards the ones this panel created that were not sent. */
  discardAll: () => Promise<void>;
}

const draftsQuery = (clipId: string): PostsQuery => ({ status: 'DRAFT', clip: clipId, page_size: 100 });

/**
 * The helper drafts of the publish panel. A draft is what makes the backend
 * generate the AI text, so the panel creates one for each pair it is showing
 * (`wanted`), one at a time, and owns their whole life: they are remembered in
 * the browser, discarded when the panel closes or goes away by any route, and
 * the ones that come back after that are cancelled on arrival.
 */
export function usePanelDrafts({ clipIds, wanted, enabled }: { clipIds: string[]; wanted: PanelPair[]; enabled: boolean }): PanelDrafts {
  const queryClient = useQueryClient();
  const actions = usePostActions();
  const workspaceId = useActiveWorkspaceId();
  const keyOf = useCallback((clipId: string) => [...keys.social.postList(draftsQuery(clipId)), workspaceId] as const, [workspaceId]);

  const queries = useQueries({
    queries: clipIds.map((clipId) => ({
      queryKey: keyOf(clipId),
      queryFn: ({ signal }: { signal: AbortSignal }) => actions.list(draftsQuery(clipId), signal) as Promise<DraftPage>,
      enabled: Boolean(workspaceId),
      staleTime: 0,
      refetchInterval: (query: { state: { data?: DraftPage } }) => (query.state.data?.results.some((post) => seoPending(post)) ? DRAFT_POLL_MS : false),
      refetchIntervalInBackground: false,
    })),
  });
  const loaded = Boolean(workspaceId) && queries.every((query) => query.isFetchedAfterMount);

  /** Pairs already asked for, so none is asked twice; drafts this panel may discard (id → clip). */
  const requested = useRef(new Set<PairKey>());
  const mine = useRef(new Map<string, string>());
  const closed = useRef(false);
  const queue = useRef<Promise<void>>(Promise.resolve());

  const read = useCallback(
    (clipId: string, platform: Platform): ScheduledPost | undefined =>
      queryClient.getQueryData<DraftPage>(keyOf(clipId))?.results.find((post) => post.platform === platform && post.status === 'DRAFT'),
    [keyOf, queryClient],
  );
  const draftOf = (clipId: string, platform: Platform): ScheduledPost | undefined =>
    queries[clipIds.indexOf(clipId)]?.data?.results.find((post) => post.platform === platform && post.status === 'DRAFT');

  const edit = useCallback(
    (clipId: string, change: (results: ScheduledPost[]) => ScheduledPost[]) => {
      const key = keyOf(clipId);
      // A refetch already in flight would bring the list as it was before this change.
      void queryClient.cancelQueries({ queryKey: key });
      queryClient.setQueryData<DraftPage>(key, (page) => {
        const results = change(page?.results ?? []);
        return { count: results.length, next: page?.next ?? null, previous: page?.previous ?? null, results };
      });
    },
    [keyOf, queryClient],
  );
  const put = useCallback(
    (post: ScheduledPost) => edit(post.video_clip, (results) => [...results.filter((item) => item.id !== post.id), post]),
    [edit],
  );
  const drop = useCallback(
    (post: Pick<ScheduledPost, 'id' | 'video_clip'>) => {
      mine.current.delete(post.id);
      forgetDraft(post.id);
      edit(post.video_clip, (results) => results.filter((item) => item.id !== post.id));
    },
    [edit],
  );

  const discardOne = useCallback(
    (id: string) =>
      actions.discard(id).then(
        () => forgetDraft(id),
        // It stays remembered: the next visit to this workspace discards it.
        () => undefined,
      ),
    [actions],
  );
  const discardAll = useCallback((): Promise<void> => {
    closed.current = true;
    const ids = [...mine.current.keys()];
    mine.current.clear();
    queue.current = queue.current.then(() => Promise.all(ids.map(discardOne))).then(() => undefined);
    return queue.current;
  }, [discardOne]);

  // Drafts of this tab that survived a reload are this panel's again.
  const listed = queries.map((query) => query.data?.results.map((post) => post.id).join(',') ?? '').join('|');
  useEffect(() => {
    for (const query of queries) {
      for (const post of query.data?.results ?? []) if (isOwnDraft(post.id)) mine.current.set(post.id, post.video_clip);
    }
    // `listed` changes when the lists do.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listed]);

  const wantedKey = wanted.map((pair) => pairKey(pair.clipId, pair.platform)).join(',');
  useEffect(() => {
    if (!enabled || !loaded || !workspaceId) return;
    for (const { clipId, platform } of wanted) {
      const key = pairKey(clipId, platform);
      if (requested.current.has(key) || read(clipId, platform)) continue;
      requested.current.add(key);
      // One at a time: each draft is a write, and none is urgent.
      queue.current = queue.current.then(async () => {
        // The panel may have closed while the ones before were being created.
        if (closed.current) return;
        let post: ScheduledPost;
        try {
          post = await actions.create({ video_clip: clipId, platform, draft: true });
        } catch {
          // Without a draft there is no suggestion: the user writes the text.
          return;
        }
        rememberDraft({ id: post.id, clip: clipId, workspace: workspaceId });
        // It came back after the panel closed: nobody is going to use it.
        if (closed.current) return discardOne(post.id);
        mine.current.set(post.id, clipId);
        put(post);
      });
    }
    // `wantedKey` stands for `wanted`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, loaded, workspaceId, wantedKey, actions, discardOne, put, read]);

  // Any way out of the panel (Back, another screen, a different selection, the tab going away) discards the drafts.
  const discardRef = useRef(discardAll);
  discardRef.current = discardAll;
  useEffect(() => {
    closed.current = false;
    const onPageHide = () => void discardRef.current();
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      void discardRef.current();
    };
  }, []);

  return useMemo(
    () => ({
      loaded,
      draftOf,
      read,
      whenIdle: () => queue.current,
      put,
      markSent: (post: Pick<ScheduledPost, 'id' | 'video_clip'>) => drop(post),
      drop,
      discardAll,
    }),
    // `draftOf` reads the query results of this render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [loaded, listed, queries.map((query) => query.dataUpdatedAt).join(','), read, put, drop, discardAll],
  );
}
