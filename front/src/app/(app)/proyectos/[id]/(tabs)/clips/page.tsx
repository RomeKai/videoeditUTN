import type { Metadata } from 'next';
import { ClipsScreen } from '@/features/proyecto/ClipsScreen';

export const metadata: Metadata = { title: 'Clips' };

// D3 · Proyecto › Clips. The header and the tabs come from the layout.
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ClipsScreen projectId={id} />;
}
