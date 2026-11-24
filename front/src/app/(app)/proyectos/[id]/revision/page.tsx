import type { Metadata } from 'next';
import { ReviewScreen } from '@/features/revision/ReviewScreen';

export const metadata: Metadata = { title: 'Revisión de clips' };

// D2 · Revisión de clips (fuera del layout de pestañas del proyecto: usa la cabecera compacta).
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReviewScreen projectId={id} />;
}
