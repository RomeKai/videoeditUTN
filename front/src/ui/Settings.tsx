'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon, type IconName } from './Icon';
import styles from './Patterns.module.css';

/** Group of settings rows with a title (H and I screens, map 9.1.9). `danger` draws the separate danger zone. */
export function SettingsGroup({
  title,
  description,
  actions,
  danger = false,
  className,
  children,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  danger?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cx(styles.setGroup, className)}>
      {title ? (
        <div className={styles.setHead}>
          <div className={styles.setHeadText}>
            <h2 className="t-h3">{title}</h2>
            {description ? <p className="t-small muted">{description}</p> : null}
          </div>
          {actions}
        </div>
      ) : null}
      <div className={danger ? styles.setDanger : undefined}>{children}</div>
    </section>
  );
}

/** One row: title and description on the left, control on the right. */
export function SettingsRow({
  label,
  description,
  htmlFor,
  children,
  className,
}: {
  label: ReactNode;
  description?: ReactNode;
  htmlFor?: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx(styles.setRow, className)}>
      <div className={styles.setLabel}>
        {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span>{label}</span>}
        {description ? <span className={styles.setDesc}>{description}</span> : null}
      </div>
      {children ? <div className={styles.setControl}>{children}</div> : null}
    </div>
  );
}

export interface SideNavItem {
  href: string;
  label: string;
  icon?: IconName;
  exact?: boolean;
}

/** Section navigation at the left of Ajustes and Perfil; a horizontal scroller on small screens. */
export function SideNav({ items, ariaLabel }: { items: SideNavItem[]; ariaLabel: string }) {
  const pathname = (usePathname() ?? '').replace(/\/+$/, '');
  return (
    <nav className={styles.side} aria-label={ariaLabel}>
      {items.map((item) => {
        const current = item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <Link key={item.href} href={item.href} aria-current={current ? 'page' : undefined}>
            {item.icon ? <Icon name={item.icon} /> : null}
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** Two columns: side navigation and content. */
export function SideLayout({ nav, children }: { nav: ReactNode; children: ReactNode }) {
  return (
    <div className={styles.sideLayout}>
      {nav}
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}
