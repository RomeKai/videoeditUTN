import type { Metadata } from 'next';
import { TranscriptScreen } from '@/features/proyecto/TranscriptScreen';

export const metadata: Metadata = { title: 'Transcripción' };

// D1e · Proyecto › Transcripción. The header and the tabs come from the layout.
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TranscriptScreen projectId={id} />;
}
