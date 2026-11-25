import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/dev/ScreenPlaceholder';
import { SCREENS } from '@/dev/screens';

export const metadata: Metadata = { title: 'Clips' };

// D3 · Clips: pantalla pendiente. El dominio dueño reemplaza este archivo (ver front/ROUTES.md).
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ScreenPlaceholder {...SCREENS.D3} params={{ id }} />;
}
