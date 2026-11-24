'use client';

import type { ReactNode } from 'react';
import { formatDateTime, formatTime, formatTimeZone } from '@/lib/format';
import { cx } from '@/lib/cx';
import { Button } from '@/ui/Button';
import { Icon, type IconName } from '@/ui/Icon';
import { Brand } from './Brand';
import styles from './System.module.css';

export interface SystemMessageProps {
  title: ReactNode;
  /** "404", "403", "500": muted, next to the title. */
  code?: string;
  description?: ReactNode;
  icon?: IconName;
  actions?: ReactNode;
  /** Inside the app shell (no own top bar, less padding). */
  inline?: boolean;
  children?: ReactNode;
}

/** One sentence, one action and nothing else (artboard 04 · Sistema). */
export function SystemMessage({ title, code, description, icon, actions, inline = false, children }: SystemMessageProps) {
  return (
    <div className={cx(styles.body, inline && styles.inline)}>
      {icon ? (
        <span className={styles.icon}>
          <Icon name={icon} size={20} />
        </span>
      ) : null}
      <h1 className="t-h2">
        {title}
        {code ? <span className={cx('num', styles.code)}>{code}</span> : null}
      </h1>
      {description ? <p className={cx('muted', styles.text)}>{description}</p> : null}
      {children}
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </div>
  );
}

/** Full page with only the brand on top: 404, 403 and 500 outside the app shell. */
export function SystemScreen(props: SystemMessageProps) {
  return (
    <div className={styles.screen}>
      <div className={styles.top}>
        <Brand href="/proyectos" />
      </div>
      <main style={{ display: 'contents' }}>
        <SystemMessage {...props} />
      </main>
    </div>
  );
}

/** K1 */
export function NotFoundMessage({ inline }: { inline?: boolean }) {
  return (
    <SystemMessage
      inline={inline}
      title="Esta página no existe"
      code="404"
      description="Puede que el link esté mal escrito o que el proyecto se haya borrado."
      actions={
        <Button variant="primary" href="/proyectos">
          Ir a Proyectos
        </Button>
      }
    />
  );
}

/** K2 */
export function NoAccessMessage({ inline }: { inline?: boolean }) {
  return (
    <SystemMessage
      inline={inline}
      title="No tenés acceso a esto"
      code="403"
      description="Pedile a un admin del workspace."
      actions={
        <Button variant="primary" href="/proyectos">
          Ir a Proyectos
        </Button>
      }
    />
  );
}

/** K3 */
export function ServerErrorMessage({ onRetry, inline }: { onRetry?: () => void; inline?: boolean }) {
  return (
    <SystemMessage
      inline={inline}
      title="Algo falló de nuestro lado"
      code="500"
      description="Ya nos enteramos. Probá de nuevo en un momento."
      actions={
        <>
          {onRetry ? (
            <Button variant="primary" iconLeft="retry" onClick={onRetry}>
              Reintentar
            </Button>
          ) : null}
          <Button variant={onRetry ? 'ghost' : 'primary'} href="/proyectos">
            Ir a Proyectos
          </Button>
        </>
      }
    />
  );
}

/** K5: simple page, without the app bar, with the estimated schedule. */
export function MaintenanceScreen({ startsAt, endsAt }: { startsAt?: string | null; endsAt?: string | null }) {
  return (
    <div className={styles.screen}>
      <main className={styles.body} style={{ paddingTop: 56 }}>
        <Brand className={styles.maintBrand} />
        <span className={styles.icon}>
          <Icon name="wrench" size={20} />
        </span>
        <h1 className="t-h2">Estamos en mantenimiento</h1>
        <p className={cx('muted', styles.text)}>Volvemos en un rato. No hace falta que hagas nada.</p>
        {startsAt && endsAt ? (
          <dl className={styles.sched}>
            <div>
              <dt className="t-over">Horario estimado</dt>
              <dd className="num" suppressHydrationWarning>
                {formatDateTime(startsAt)} – {formatTime(endsAt)} <span className="muted">({formatTimeZone(startsAt)})</span>
              </dd>
            </div>
          </dl>
        ) : null}
      </main>
    </div>
  );
}
