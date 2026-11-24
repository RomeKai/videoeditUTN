import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/dev/ScreenPlaceholder';
import { SCREENS } from '@/dev/screens';

export const metadata: Metadata = { title: 'Aceptar invitación' };

// A5 · Aceptar invitación: pantalla pendiente. El dominio dueño reemplaza este archivo (ver front/ROUTES.md).
export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <ScreenPlaceholder {...SCREENS.A5} page={false} params={{ token }} />;
}
