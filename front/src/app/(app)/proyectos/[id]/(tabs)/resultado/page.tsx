import type { Metadata } from 'next';
import { ResultScreen } from '@/features/proyecto/ResultScreen';

export const metadata: Metadata = { title: 'Resultado' };

// D5 · Proyecto › Resultado. The header and the tabs come from the layout.
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ResultScreen projectId={id} />;
}
