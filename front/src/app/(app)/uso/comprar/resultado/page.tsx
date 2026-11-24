import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/dev/ScreenPlaceholder';
import { SCREENS } from '@/dev/screens';

export const metadata: Metadata = { title: 'Resultado de la compra' };

// G5 · Resultado de la compra: pantalla pendiente. El dominio dueño reemplaza este archivo (ver front/ROUTES.md).
export default function Page() {
  return <ScreenPlaceholder {...SCREENS.G5} page={false} headingLevel={2} />;
}
