import type { Metadata } from 'next';
import { one, type SearchParams } from '@/features/acceso/params';
import { VerifyEmailPage } from '@/features/acceso/VerifyEmailScreen';

export const metadata: Metadata = { title: 'Verificar email' };

// A3 · Verificar email. `?email=` is the account that waits. The link of the email brings its token in the
// fragment (`#token=…`, read by the screen); `?token=` is a link sent before that, valid until it expires.
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  return <VerifyEmailPage email={one(params.email)} token={one(params.token)} fromLogin={one(params.motivo) === 'login'} next={one(params.siguiente)} />;
}
