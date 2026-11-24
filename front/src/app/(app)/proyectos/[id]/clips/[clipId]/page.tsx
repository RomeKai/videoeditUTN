import type { Metadata } from 'next';
import { ClipEditorScreen } from '@/features/revision/ClipEditorScreen';

export const metadata: Metadata = { title: 'Editor de clip' };

// D4 · Editor de clip (fuera del layout de pestañas del proyecto: usa la cabecera compacta).
export default async function Page({ params }: { params: Promise<{ id: string; clipId: string }> }) {
  const { id, clipId } = await params;
  return <ClipEditorScreen projectId={id} clipId={clipId} />;
}
