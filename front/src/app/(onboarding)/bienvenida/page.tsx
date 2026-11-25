import type { Metadata } from 'next';
import { one, type SearchParams } from '@/features/acceso/params';
import { WelcomeScreen, type WelcomeStep } from '@/features/acceso/WelcomeScreen';

export const metadata: Metadata = { title: 'Bienvenida' };

// B1 · Bienvenida. `?paso=2` or `?paso=3` opens on that step.
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const step = Number(one((await searchParams).paso));
  const initialStep: WelcomeStep = step === 2 || step === 3 ? step : 1;
  return <WelcomeScreen key={initialStep} initialStep={initialStep} />;
}
