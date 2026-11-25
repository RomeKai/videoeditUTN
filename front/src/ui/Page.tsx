import Link from 'next/link';
import { Fragment, type CSSProperties, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon } from './Icon';
import styles from './Page.module.css';

export interface PageProps {
  /** Work screens (review, editor) use the full width; the rest is centered at 1200 px. */
  width?: 'default' | 'wide' | 'narrow';
  /** No bottom padding: for pages that end in a sticky <ActionBar>. */
  flush?: boolean;
  className?: string;
  children: ReactNode;
}

/** Page container (kit: .page). The app layout already renders <main>; this is the centered column. */
export function Page({ width = 'default', flush = false, className, children }: PageProps) {
  return (
    <div className={cx(styles.page, width === 'wide' && styles.wide, width === 'narrow' && styles.narrow, flush && styles.flush, className)}>
      {children}
    </div>
  );
}

export interface PageHeadProps {
  title: ReactNode;
  description?: ReactNode;
  /** Right side: filters, the page's single primary action… */
  actions?: ReactNode;
  className?: string;
}

export function PageHead({ title, description, actions, className }: PageHeadProps) {
  return (
    <header className={cx(styles.head, className)}>
      <div className={styles.headText}>
        <h1 className="t-h1">{title}</h1>
        {description ? <p className="muted">{description}</p> : null}
      </div>
      {actions ? <div className={styles.headActions}>{actions}</div> : null}
    </header>
  );
}

export interface Crumb {
  label: ReactNode;
  href?: string;
}

/** Breadcrumbs of detail screens: "Proyectos / Vlog Mendoza". The last item is the current page. */
export function Crumbs({ items, className }: { items: Crumb[]; className?: string }) {
  return (
    <nav className={cx(styles.crumbs, className)} aria-label="Ruta">
      {items.map((item, index) => {
        const last = index === items.length - 1;
        return (
          <Fragment key={index}>
            {index > 0 ? <Icon name="chevron-right" size={14} /> : null}
            {item.href && !last ? (
              <Link href={item.href}>{item.label}</Link>
            ) : (
              <span className={styles.crumbCurrent} aria-current={last ? 'page' : undefined}>
                {item.label}
              </span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}

/** Vertical stack with a gap; saves a CSS module for simple page sections. */
export function Stack({ gap = 16, className, children }: { gap?: number; className?: string; children: ReactNode }) {
  return (
    <div className={cx(styles.stack, className)} style={{ '--gap': `${gap}px` } as CSSProperties}>
      {children}
    </div>
  );
}
