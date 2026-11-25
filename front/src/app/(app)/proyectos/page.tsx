import type { Metadata } from 'next';
import { ProjectsScreen } from '@/features/proyectos/ProjectsScreen';

export const metadata: Metadata = { title: 'Proyectos' };

// B2 · Proyectos
export default function Page() {
  return <ProjectsScreen />;
}
