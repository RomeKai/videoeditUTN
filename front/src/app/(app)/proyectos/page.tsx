import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ProjectsScreen } from '@/features/proyectos/ProjectsScreen';

export const metadata: Metadata = { title: 'Proyectos' };

// B2 · Proyectos. The filter, the order, the page and the search live in the URL (useSearchParams needs the boundary).
export default function Page() {
  return (
    <Suspense fallback={null}>
      <ProjectsScreen />
    </Suspense>
  );
}
