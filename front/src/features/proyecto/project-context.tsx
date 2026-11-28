'use client';

import { createContext, useContext } from 'react';
import type { Project } from '@/api/contracts/projects';
import { goingOut } from '@/api/hooks';

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

/**
 * A publication of a clip is on its way out (`goingOut`: in the queue, publishing, or due now): the card
 * says «Programado» until it is out, so the project is asked again until then and the card turns
 * «Publicado» by itself. In the demo's hybrid mode the clips come from the backend without the mocked
 * publications, so this never polls there.
 */
export function anyPostGoingOut(project: Pick<Project, 'clips'>, now = Date.now()): boolean {
  return project.clips.some((clip) => (clip.posts ?? []).some((post) => goingOut(post, now)));
}

/** What the project tabs keep polling for once the project itself is done. */
export function clipsChanging(project: Pick<Project, 'clips'>): boolean {
  return anyClipRendering(project) || anyPostGoingOut(project);
}
