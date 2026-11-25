import type { Metadata } from 'next';
import { one, type SearchParams } from '@/features/acceso/params';
import { VerifyEmailScreen } from '@/features/acceso/VerifyEmailScreen';

export const metadata: Metadata = { title: 'Verificar email' };

// A3 · Verificar email. `?email=` is the account that waits; `?token=` is the link of the email.
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  return <VerifyEmailScreen email={one(params.email)} token={one(params.token)} fromLogin={one(params.motivo) === 'login'} next={one(params.siguiente)} />;
}
