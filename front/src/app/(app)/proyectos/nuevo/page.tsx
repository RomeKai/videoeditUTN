import type { Metadata } from 'next';
import { Suspense } from 'react';
import { NewProjectScreen } from '@/features/proyectos/NewProjectScreen';

export const metadata: Metadata = { title: 'Nuevo proyecto' };

// C1 · Nuevo proyecto. The screen reads ?desde, ?origen and ?prompt, so it renders inside Suspense.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <NewProjectScreen />
    </Suspense>
  );
}
