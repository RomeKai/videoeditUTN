'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Tabs.module.css';

export interface NavTabItem {
  href: string;
  label: ReactNode;
  /** Shorter label on phones ("Uso" for "Uso y facturación"); the full label stays as the accessible name. */
  shortLabel?: ReactNode;
  ariaLabel?: string;
  /** Active only on the exact path (for index tabs such as /uso or /proyectos/:id). */
  exact?: boolean;
  /** Extra path prefixes that also mark this tab as current. */
  match?: string[];
  count?: number;
}

function isCurrent(pathname: string, item: NavTabItem): boolean {
  const clean = pathname.replace(/\/+$/, '') || '/';
  if (item.exact) return clean === item.href;
  if (clean === item.href || clean.startsWith(`${item.href}/`)) return true;
  return (item.match ?? []).some((prefix) => clean === prefix || clean.startsWith(`${prefix}/`));
}

export interface NavTabsProps {
  items: NavTabItem[];
  ariaLabel: string;
  /** 'main': second row of the topbar. 'sub': under a page title (kit: .subtabs). */
  variant?: 'main' | 'sub';
  className?: string;
}

/** Tabs that are routes: each one is a link and the current one gets aria-current="page". */
export function NavTabs({ items, ariaLabel, variant = 'sub', className }: NavTabsProps) {
  const pathname = usePathname() ?? '';
  return (
    <nav className={cx(styles.tabs, variant === 'sub' ? styles.sub : styles.main, className)} aria-label={ariaLabel}>
      {items.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={styles.tab}
          aria-current={isCurrent(pathname, item) ? 'page' : undefined}
          aria-label={item.ariaLabel ?? (item.shortLabel && typeof item.label === 'string' ? item.label : undefined)}
        >
          {item.shortLabel ? (
            <>
              <span className={styles.full}>{item.label}</span>
              <span className={styles.short}>{item.shortLabel}</span>
            </>
          ) : (
            item.label
          )}
          {item.count !== undefined ? <span className={styles.count}>{item.count}</span> : null}
        </Link>
      ))}
    </nav>
  );
}

export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
  count?: number;
  disabled?: boolean;
}

export interface TabListProps<T extends string> {
  items: ReadonlyArray<TabItem<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  variant?: 'main' | 'sub';
  /** Prefix for tab/panel ids: tab is `${idBase}-tab-${value}`, panel `${idBase}-panel-${value}`. */
  idBase?: string;
  className?: string;
}

/** In-page tabs (no route change): role=tablist with arrow-key navigation. Pair with <TabPanel>. */
export function TabList<T extends string>({
  items,
  value,
  onChange,
  ariaLabel,
  variant = 'sub',
  idBase = 'tabs',
  className,
}: TabListProps<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const enabled = items.filter((item) => !item.disabled);
    const index = enabled.findIndex((item) => item.value === value);
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % enabled.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + enabled.length) % enabled.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = enabled.length - 1;
    else return;
    event.preventDefault();
    const target = enabled[next];
    if (!target) return;
    onChange(target.value);
    ref.current?.querySelector<HTMLElement>(`#${CSS.escape(`${idBase}-tab-${target.value}`)}`)?.focus();
  };
  return (
    <div
      ref={ref}
      role="tablist"
      aria-label={ariaLabel}
      className={cx(styles.tabs, variant === 'sub' ? styles.sub : styles.main, className)}
      onKeyDown={onKeyDown}
    >
      {items.map((item) => {
        const selected = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            id={`${idBase}-tab-${item.value}`}
            aria-selected={selected}
            aria-controls={`${idBase}-panel-${item.value}`}
            aria-disabled={item.disabled || undefined}
            tabIndex={selected ? 0 : -1}
            className={styles.tab}
            onClick={() => {
              if (!item.disabled) onChange(item.value);
            }}
          >
            {item.label}
            {item.count !== undefined ? <span className={styles.count}>{item.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  value,
  active,
  idBase = 'tabs',
  className,
  children,
}: {
  value: string;
  active: boolean;
  idBase?: string;
  className?: string;
  children: ReactNode;
}) {
  if (!active) return null;
  return (
    <div role="tabpanel" id={`${idBase}-panel-${value}`} aria-labelledby={`${idBase}-tab-${value}`} className={className}>
      {children}
    </div>
  );
}
