import type { Metadata } from 'next';
import { BrandKitsScreen } from '@/features/brand-kits/BrandKitsScreen';

export const metadata: Metadata = { title: 'Brand kits' };

// F1 · Brand kits
export default function Page() {
  return <BrandKitsScreen />;
}
