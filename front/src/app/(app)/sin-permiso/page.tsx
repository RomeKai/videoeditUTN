import type { Metadata } from 'next';
import { NoAccessMessage } from '@/shell/SystemPage';

export const metadata: Metadata = { title: 'Sin permiso' };

/** K2 · Sin permiso. Screens usually render <NoAccessMessage inline /> in place; this route exists to link to it. */
export default function NoAccessPage() {
  return <NoAccessMessage inline />;
}
