import type { Metadata } from 'next';
import { LoginScreen } from '@/features/acceso/LoginScreen';
import { one, type SearchParams } from '@/features/acceso/params';

export const metadata: Metadata = { title: 'Iniciar sesión' };

// A1 · Iniciar sesión. `?siguiente=` is the page to open after signing in; `?motivo=verificar` comes from A3.
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  return <LoginScreen next={one(params.siguiente)} email={one(params.email)} verifyPending={one(params.motivo) === 'verificar'} />;
}
