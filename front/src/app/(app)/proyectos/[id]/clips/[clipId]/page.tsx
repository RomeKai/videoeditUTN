import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/dev/ScreenPlaceholder';
import { SCREENS } from '@/dev/screens';

export const metadata: Metadata = { title: 'Editor de clip' };

// D4 · Editor de clip: pantalla pendiente. El dominio dueño reemplaza este archivo (ver front/ROUTES.md).
export default async function Page({ params }: { params: Promise<{ id: string; clipId: string }> }) {
  const { id, clipId } = await params;
  return <ScreenPlaceholder {...SCREENS.D4} params={{ id, clipId }} />;
}
