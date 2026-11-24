import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/dev/ScreenPlaceholder';
import { SCREENS } from '@/dev/screens';

export const metadata: Metadata = { title: 'Perfil' };

// I1 · Perfil: pantalla pendiente. El dominio dueño reemplaza este archivo (ver front/ROUTES.md).
export default function Page() {
  return <ScreenPlaceholder {...SCREENS.I1} page={false} headingLevel={2} />;
}
