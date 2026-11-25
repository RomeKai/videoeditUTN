import type { Metadata } from 'next';
import { ActivityScreen } from '@/features/proyecto/ActivityScreen';

export const metadata: Metadata = { title: 'Actividad' };

// D1f · Proyecto › Actividad. The header and the tabs come from the layout.
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ActivityScreen projectId={id} />;
}
