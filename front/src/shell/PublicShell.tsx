'use client';

import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Card';
import { ThemeToggle } from '@/ui/ThemeToggle';
import { Brand } from './Brand';
import styles from './PublicShell.module.css';
import { useSession } from './session';

/**
 * Layout of the public screens (A1–A6): the brand, links to Precios and to the
 * other access screen, and the content. No app bar.
 */
export function PublicShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? '';
  const { status } = useSession();
  const signedIn = status === 'authenticated';
  return (
    <div className={styles.pub}>
      <header className={styles.head}>
        <Brand href={signedIn ? '/proyectos' : '/login'} label="OneCreator" />
        <span className={styles.spacer} />
        <ThemeToggle />
        {pathname !== '/precios' ? (
          <Button variant="ghost" size="sm" href="/precios">
            Precios
          </Button>
        ) : null}
        {signedIn ? (
          <Button variant="secondary" size="sm" href="/proyectos">
            Ir a la app
          </Button>
        ) : (
          <>
            {pathname !== '/login' ? (
              <Button variant={pathname === '/precios' ? 'ghost' : 'secondary'} size="sm" href="/login">
                Iniciar sesión
              </Button>
            ) : null}
            {pathname !== '/registro' ? (
              <Button variant="secondary" size="sm" href="/registro">
                Crear cuenta
              </Button>
            ) : null}
          </>
        )}
      </header>
      <main className={styles.main}>{children}</main>
      <footer className={styles.foot}>
        <span>© OneCreator</span>
        <a href="mailto:soporte@onecreator.app">Soporte</a>
      </footer>
    </div>
  );
}

/** The centered card of the access screens (kit artboards A1–A5): title, sentence and the form. */
export function AuthCard({
  title,
  description,
  wide = false,
  children,
  footer,
}: {
  title: ReactNode;
  description?: ReactNode;
  wide?: boolean;
  children: ReactNode;
  /** Under a divider: "¿No tenés cuenta? Crear cuenta". */
  footer?: ReactNode;
}) {
  return (
    <Card className={cx(styles.card, wide && styles.cardWide)}>
      <div className={styles.title}>
        <h1 className="t-h2">{title}</h1>
        {description ? <p className="t-small muted">{description}</p> : null}
      </div>
      {children}
      {footer ? (
        <>
          <hr className="divider" />
          <p className={styles.alt}>{footer}</p>
        </>
      ) : null}
    </Card>
  );
}

/** Vertical form with the 16 px rhythm of the access screens. */
export const authFormClass = styles.form;
