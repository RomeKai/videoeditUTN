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
