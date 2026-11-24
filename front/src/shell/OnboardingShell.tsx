'use client';

import type { ReactNode } from 'react';
import { Avatar } from '@/ui/Avatar';
import { Brand } from './Brand';
import styles from './PublicShell.module.css';
import { RequireSession } from './RequireSession';
import { useSession } from './session';

function Header() {
  const { me } = useSession();
  return (
    <header className={styles.head}>
      <Brand />
      <span className={styles.spacer} />
      {me ? (
        <>
          <span className="t-small muted">{me.name}</span>
          <Avatar name={me.name} seed={me.id} />
        </>
      ) : null}
    </header>
  );
}

/** Layout of the welcome (artboards B1): the brand, the user and a centered card. */
export function OnboardingShell({ children }: { children: ReactNode }) {
  return (
    <RequireSession>
      <div className={styles.pub}>
        <Header />
        <main className={styles.main}>{children}</main>
      </div>
    </RequireSession>
  );
}
