import type { Metadata } from 'next';
import { one, type SearchParams } from '@/features/acceso/params';
import { RegisterScreen } from '@/features/acceso/RegisterScreen';

export const metadata: Metadata = { title: 'Crear cuenta' };

// A2 · Crear cuenta.
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  return <RegisterScreen email={one(params.email)} />;
}
