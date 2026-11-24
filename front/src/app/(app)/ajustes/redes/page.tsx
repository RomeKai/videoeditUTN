import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/dev/ScreenPlaceholder';
import { SCREENS } from '@/dev/screens';

export const metadata: Metadata = { title: 'Redes sociales' };

// H3 · Redes sociales: pantalla pendiente. El dominio dueño reemplaza este archivo (ver front/ROUTES.md).
export default function Page() {
  return <ScreenPlaceholder {...SCREENS.H3} page={false} headingLevel={2} />;
}
