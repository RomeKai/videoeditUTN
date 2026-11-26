import type { Metadata } from 'next';
import { LoginScreen } from '@/features/acceso/LoginScreen';
import { one, type SearchParams } from '@/features/acceso/params';

export const metadata: Metadata = { title: 'Iniciar sesión' };

// A1 · Iniciar sesión. `?siguiente=` is the page to open after signing in; `?verificado=1` comes from A3.
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  return <LoginScreen next={one(params.siguiente)} email={one(params.email)} verified={one(params.verificado) === '1'} />;
}
