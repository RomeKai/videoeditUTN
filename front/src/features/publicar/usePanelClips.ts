'use client';

import { useQueries } from '@tanstack/react-query';
import { useMemo } from 'react';
import { apiCall } from '@/api/client';
import { getClip } from '@/api/contracts/clips';
import { getProject } from '@/api/contracts/projects';
import { keys } from '@/api/query';
import { clipDuration, type PanelClip } from './publish';

export interface PanelClipsResult {
  /** In the order of `clipIds`; only the ones already loaded. */
  clips: PanelClip[];
  isLoading: boolean;
  error: unknown;
  refetch: () => void;
}

/**
 * The clips the panel publishes, each with what it needs from its project
 * (format, resolution, whether it paid for the AI text). The hooks of
 * `@/api/hooks` load one clip; the panel takes any number, so this uses the same
 * endpoints and cache keys through `useQueries`.
 */
export function usePanelClips(clipIds: string[]): PanelClipsResult {
  const clipQueries = useQueries({
    queries: clipIds.map((id) => ({
      queryKey: keys.clips.detail(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => apiCall(getClip, { params: { id }, signal }),
    })),
  });
  const projectIds = useMemo(() => {
    const ids = new Set<string>();
    for (const query of clipQueries) if (query.data) ids.add(query.data.project);
    return [...ids];
    // The list only changes when a clip finishes loading.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipQueries.map((query) => query.data?.project ?? '').join(',')]);
  const projectQueries = useQueries({
    queries: projectIds.map((id) => ({
      queryKey: keys.projects.detail(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => apiCall(getProject, { params: { id }, signal }),
    })),
  });

  const clips: PanelClip[] = [];
  clipQueries.forEach((query) => {
    const clip = query.data;
    if (!clip) return;
    const project = projectQueries[projectIds.indexOf(clip.project)]?.data;
    if (!project) return;
    clips.push({
      id: clip.id,
      title: clip.title,
      duration: clipDuration(clip),
      aspectRatio: project.aspect_ratio,
      projectId: project.id,
      projectTitle: project.title,
      resolution: project.metadata.render_resolution,
      ready: clip.status === 'completed' || clip.status === 'published',
      seo: project.metadata.use_seo === true,
    });
  });

  const queries = [...clipQueries, ...projectQueries];
  const failed = queries.find((query) => query.isError);
  return {
    clips,
    isLoading: clips.length < clipIds.length && !failed,
    error: failed?.error ?? null,
    refetch: () => {
      for (const query of queries) if (query.isError) void query.refetch();
    },
  };
}
