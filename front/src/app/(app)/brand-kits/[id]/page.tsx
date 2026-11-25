import type { Metadata } from 'next';
import { BrandKitEditor } from '@/features/brand-kits/BrandKitEditor';

export const metadata: Metadata = { title: 'Brand kit' };

// F2 · Editar brand kit (`/brand-kits/nuevo` crea uno)
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BrandKitEditor id={id} />;
}
