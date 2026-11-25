import type { Metadata } from 'next';
import { Suspense } from 'react';
import { PublicacionesScreen } from '@/features/publicar/PublicacionesScreen';

export const metadata: Metadata = { title: 'Publicaciones' };

// E2 · Publicaciones. The view, the filters and the open detail live in the URL (useSearchParams needs the boundary).
export default function Page() {
  return (
    <Suspense fallback={null}>
      <PublicacionesScreen />
    </Suspense>
  );
}
