'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { USAGE_TABS } from '@/shell/nav';
import { usePermissions, useSession } from '@/shell/session';
import { Icon, PageHead, Tooltip } from '@/ui';
import styles from './uso.module.css';

/**
 * Title and sub-tabs of Uso y facturación. Dueño and Admin get the four tabs;
 * Editor and Lector see only the balance, so the other tabs stay visible but
 * disabled with "Pedile acceso a un admin" (artboard R1).
 */
export function UsageHeader() {
  const { workspace } = useSession();
  const { can, reason } = usePermissions();
  const pathname = (usePathname() ?? '').replace(/\/+$/, '');
  const admin = can('billing.manage');
  const name = workspace?.name ?? 'tu workspace';
  return (
    <>
      <PageHead title="Uso y facturación" description={admin ? `Tokens, movimientos y plan de ${name}.` : `El saldo de tokens de ${name}.`} />
      <nav className={styles.subtabs} aria-label="Uso y facturación">
        {USAGE_TABS.map((tab) => {
          const current = tab.exact ? pathname === tab.href : pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          if (!admin && !tab.exact) {
            return (
              <Tooltip key={tab.href} content={reason} side="bottom" className={styles.subtabWrap}>
                <span className={`${styles.subtab} ${styles.subtabOff}`} role="link" aria-disabled="true" tabIndex={0}>
                  <Icon name="lock" size={12} />
                  {tab.label}
                </span>
              </Tooltip>
            );
          }
          return (
            <Link key={tab.href} href={tab.href} className={styles.subtab} aria-current={current ? 'page' : undefined}>
              {tab.label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
