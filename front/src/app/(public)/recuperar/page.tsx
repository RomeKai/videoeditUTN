import type { Metadata } from 'next';
import { one, type SearchParams } from '@/features/acceso/params';
import { RecoverScreen } from '@/features/acceso/RecoverScreen';

export const metadata: Metadata = { title: 'Recuperar contraseña' };

// A4 · Recuperar contraseña. The link of the email brings its token in the fragment (`#token=…`, read by the
// screen): without it the screen asks for the link (step 1), with it for the new password (step 2).
// `?email=` only prefills step 1.
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  return <RecoverScreen email={one(params.email)} />;
}
