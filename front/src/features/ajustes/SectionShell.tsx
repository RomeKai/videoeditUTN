'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { useMembers, useSocialAccounts } from '@/api/hooks';
import { useIsMobile } from '@/lib/hooks';
import { PROFILE_NAV, SETTINGS_NAV } from '@/shell/nav';
import { usePermissions, useSession } from '@/shell/session';
import { Avatar, Icon, PageHead, type IconName } from '@/ui';
import styles from './ajustes.module.css';
import { membersSummary, networksSummary } from './summaries';

export interface SectionItem {
  href: string;
  label: string;
  icon: IconName;
  exact?: boolean;
  /** Second line of the row on phones (M10): what the section holds right now. */
  summary?: ReactNode;
}

function isCurrent(pathname: string, item: SectionItem): boolean {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/**
 * Sections at the left and the content in a 720 px column (H and I screens).
 * Under 640 px it becomes the list of M10: each section is a row with its
 * summary and the open one shows its content right below, in the document too,
 * so the routes stay the same on every screen size.
 */
export function SectionShell({ ariaLabel, items, children }: { ariaLabel: string; items: SectionItem[]; children: ReactNode }) {
  const pathname = (usePathname() ?? '').replace(/\/+$/, '');
  const mobile = useIsMobile();
  const active = Math.max(
    0,
    items.findIndex((item) => isCurrent(pathname, item)),
  );
  const link = (item: SectionItem) => (
    <Link key={item.href} href={item.href} className={styles.sideItem} aria-current={isCurrent(pathname, item) ? 'page' : undefined}>
      <Icon name={item.icon} />
      <span className={styles.sideText}>
        <span className={styles.sideTitle}>{item.label}</span>
        {item.summary ? <span className={styles.sideSummary}>{item.summary}</span> : null}
      </span>
      <span className={styles.chev} aria-hidden="true">
        <Icon name="chevron-down" />
      </span>
    </Link>
  );
  if (mobile) {
    // The document follows what is seen, so focus and reading order do too: the rows up to the
    // open section, its content, then the rest of the rows.
    const after = items.slice(active + 1);
    return (
      <div className={styles.split}>
        <nav className={styles.side} aria-label={ariaLabel}>
          {items.slice(0, active + 1).map(link)}
        </nav>
        <div className={styles.content}>{children}</div>
        {after.length > 0 ? (
          <nav className={styles.side} aria-label={`${ariaLabel}, continuación`}>
            {after.map(link)}
          </nav>
        ) : null}
      </div>
    );
  }
  return (
    <div className={styles.split}>
      <nav className={styles.side} aria-label={ariaLabel}>
        {items.map(link)}
      </nav>
      <div className={styles.content}>{children}</div>
    </div>
  );
}

/** Ajustes (H1–H3): Miembros and Redes sociales only appear for Dueño and Admin (map 1.7). */
export function SettingsShell({ children }: { children: ReactNode }) {
  const { workspace } = useSession();
  const { can } = usePermissions();
  const admin = can('members.manage');
  const members = useMembers(workspace?.id, { enabled: admin });
  const accounts = useSocialAccounts();
  const summaries: Record<string, ReactNode> = {
    '/ajustes': workspace?.name,
    '/ajustes/miembros': members.data ? membersSummary(members.data) : undefined,
    '/ajustes/redes': accounts.data ? networksSummary(accounts.data) : undefined,
  };
  const items = SETTINGS_NAV.filter((item) => admin || !item.adminOnly).map((item) => ({ ...item, summary: summaries[item.href] }));
  return (
    <>
      <PageHead title="Ajustes" description={workspace ? `Workspace ${workspace.name}` : undefined} />
      <SectionShell ariaLabel="Secciones de ajustes" items={items}>
        {children}
      </SectionShell>
    </>
  );
}

/** Tu cuenta (I1, I2). */
export function AccountShell({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const summaries: Record<string, ReactNode> = { '/perfil': me?.email, '/perfil/notificaciones': 'Emails que querés recibir' };
  return (
    <>
      <div className={styles.headRow}>
        {me ? <Avatar name={me.name} seed={me.id} size="lg" className={styles.headAvatar} /> : null}
        <PageHead title="Tu cuenta" description={me ? `${me.name} · ${me.email}` : undefined} />
      </div>
      <SectionShell ariaLabel="Secciones de tu cuenta" items={PROFILE_NAV.map((item) => ({ ...item, summary: summaries[item.href] }))}>
        {children}
      </SectionShell>
    </>
  );
}
