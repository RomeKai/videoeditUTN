'use client';

import { createContext, useContext } from 'react';
import type { Project } from '@/api/contracts/projects';

const ProjectContext = createContext<Project | null>(null);

/** The project the layout loaded, handed to the tab below it. */
export const ProjectProvider = ProjectContext.Provider;

/**
 * The project of the current route, from the project layout. The tabs read it
 * here instead of running their own query, so there is one place that decides
 * when to poll and what to show after the project is deleted.
 */
export function useCurrentProject(): Project | null {
  return useContext(ProjectContext);
}

/** A clip retried or re-rendered keeps a finished project `completed` or `partial`: keep polling while it renders. */
export function anyClipRendering(project: Pick<Project, 'clips'>): boolean {
  return project.clips.some((clip) => clip.status === 'rendering');
}

/** How long after its time Clips keeps asking about a publication that is not out yet: then a stuck one stops the polling. */
export const POST_GOING_OUT_WAIT_MS = 10 * 60_000;

const ON_ITS_WAY = new Set(['SCHEDULED', 'QUEUED', 'PROCESSING']);

/**
 * A publication of a clip is going out right now: publishing, or in the queue or scheduled for a time that already
 * came (the dispatcher queues posts up to an hour ahead, so «in the queue» alone is not «now»). The card says
 * «Programado» until it is out, so the project is asked again meanwhile and the card turns «Publicado» by itself,
 * for at most POST_GOING_OUT_WAIT_MS after its time: a stuck post, or one the beat never sent, stops the polling
 * (the project detail carries the transcript). In the demo's hybrid mode the clips come from the backend without
 * the mocked publications, so this never polls there.
 */
export function anyPostGoingOut(project: Pick<Project, 'clips'>, now = Date.now()): boolean {
  return project.clips.some((clip) =>
    (clip.posts ?? []).some((post) => {
      if (!ON_ITS_WAY.has(post.status)) return false;
      const at = Date.parse(post.publish_at);
      if (!Number.isFinite(at)) return false;
      return (post.status === 'PROCESSING' || at <= now) && now - at <= POST_GOING_OUT_WAIT_MS;
    }),
  );
}

/** What the project tabs keep polling for once the project itself is done. */
export function clipsChanging(project: Pick<Project, 'clips'>): boolean {
  return anyClipRendering(project) || anyPostGoingOut(project);
}
