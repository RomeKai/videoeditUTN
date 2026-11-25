import type { ReactNode } from 'react';
import { OnboardingShell } from '@/shell/OnboardingShell';

/** Welcome (B1): needs a session, but shows only the brand and who is signed in. */
export default function OnboardingLayout({ children }: { children: ReactNode }) {
  return <OnboardingShell>{children}</OnboardingShell>;
}
