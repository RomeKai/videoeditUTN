import type { ReactNode } from 'react';
import { ProjectShell } from '@/features/proyecto/ProjectShell';

/**
 * Layout of the project's tabs (Resumen, Clips or Resultado, Transcripción and
 * Actividad): the single project header and the sub-tabs, so only the tab's
 * content changes. Revisión (D2) and Editor (D4) stay out of this group: they
 * use the compact header.
 */
export default async function ProjectTabsLayout({ children, params }: { children: ReactNode; params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProjectShell projectId={id}>{children}</ProjectShell>;
}
