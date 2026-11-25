'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState, type ReactNode } from 'react';
import { useLogout, useNotifications, useWallet } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { formatNumber, formatTokens } from '@/lib/format';
import { ROLE_LABEL } from '@/lib/permissions';
import { useTheme } from '@/lib/theme';
import { Avatar } from '@/ui/Avatar';
import { Badge } from '@/ui/Badge';
import { Button, IconButton } from '@/ui/Button';
import { Drawer } from '@/ui/Dialog';
import { Icon } from '@/ui/Icon';
import { Kbd } from '@/ui/Kbd';
import { Popover } from '@/ui/Popover';
import { NavTabs } from '@/ui/Tabs';
import { Tooltip } from '@/ui/Tooltip';
import styles from './AppShell.module.css';
import { Brand } from './Brand';
import { CommandMenu } from './CommandMenu';
import { useGuardedNavigation } from './leaveGuard';
import { MAIN_NAV } from './nav';
import { NotificationsPanel } from './NotificationsPanel';
import { usePermissions, useSession } from './session';
import { CreateWorkspaceDialog, WorkspaceList, WorkspaceSwitcher } from './WorkspaceSwitcher';

/** Routes where the phone's fixed "Nuevo proyecto" button would cover the screen's own action bar. */
const NO_FAB = [/^\/proyectos\/nuevo/, /^\/proyectos\/[^/]+\/revision/, /^\/proyectos\/[^/]+\/clips\/[^/]+/, /^\/brand-kits\/[^/]+/, /^\/bienvenida/];

function Balance() {
  const wallet = useWallet();
  const { workspace } = useSession();
  const available = wallet.data?.available_balance ?? workspace?.available_balance ?? 0;
  const reserved = wallet.data?.reserved_balance ?? 0;
  const low = wallet.data ? wallet.data.available_balance < wallet.data.low_balance_threshold : false;
  const label = `Saldo: ${formatTokens(available)} disponibles${reserved > 0 ? `, ${formatNumber(reserved)} reservados` : ''}${low ? '. Saldo bajo' : ''}`;
  return (
    <Tooltip
      side="bottom"
      align="end"
      content={
        <>
          {formatNumber(reserved)} reservados{low ? ' · saldo bajo' : ''}
        </>
      }
      className={styles.balanceHit}
    >
      <Link className={cx(styles.balance, low && styles.balanceLow)} href="/uso" aria-label={label}>
        <span className={styles.balanceDot} aria-hidden="true" />
        {formatTokens(available)}
      </Link>
    </Tooltip>
  );
}

function AccountMenu({ close, mobile, onCreateWorkspace, onOpenNotifications }: { close: () => void; mobile: boolean; onCreateWorkspace: () => void; onOpenNotifications: () => void }) {
  const { me, workspace } = useSession();
  const { theme, toggle } = useTheme();
  const notifications = useNotifications();
  const wallet = useWallet();
  const logout = useLogout();
  const router = useRouter();
  const navigation = useGuardedNavigation();
  const unread = notifications.data?.unread_count ?? 0;
  if (!me) return null;
  return (
    <div
      className={styles.menu}
      role="menu"
      aria-label="Cuenta y workspace"
      onKeyDown={(event) => {
        // Arrow keys move between the items, like any menu.
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role^="menuitem"]'));
        const index = items.indexOf(document.activeElement as HTMLElement);
        let next: number | null = null;
        if (event.key === 'ArrowDown') next = (index + 1) % items.length;
        else if (event.key === 'ArrowUp') next = (index - 1 + items.length) % items.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = items.length - 1;
        if (next === null) return;
        event.preventDefault();
        items[next]?.focus();
      }}
    >
      <div className={styles.menuHead}>
        <Avatar name={me.name} seed={me.id} />
        <div className={styles.grow}>
          <div style={{ fontWeight: 500 }} className={styles.ellip}>
            {me.name}
          </div>
          <div className={cx('t-small muted', styles.ellip)}>{me.email}</div>
        </div>
        {workspace ? (
          <Badge tone="outline" size="sm">
            {ROLE_LABEL[workspace.role]}
          </Badge>
        ) : null}
      </div>
      <div className={styles.sep} role="separator" />
      {mobile ? (
        <>
          <WorkspaceList onDone={close} onCreate={onCreateWorkspace} />
          <div className={styles.sep} role="separator" />
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            onClick={() => {
              close();
              onOpenNotifications();
            }}
          >
            <Icon name="bell" />
            <span className={styles.grow}>Notificaciones</span>
            {unread > 0 ? (
              <Badge tone="accent" size="sm">
                {unread} {unread === 1 ? 'nueva' : 'nuevas'}
              </Badge>
            ) : null}
          </button>
          <Link role="menuitem" className={styles.menuItem} href="/uso" onClick={close}>
            <Icon name="tokens" />
            <span className={styles.grow}>Saldo</span>
            <span className="num t-small muted">
              {formatNumber(wallet.data?.available_balance ?? workspace?.available_balance ?? 0)}
              {wallet.data && wallet.data.reserved_balance > 0 ? ` · ${formatNumber(wallet.data.reserved_balance)} reservados` : ''}
            </span>
          </Link>
        </>
      ) : null}
      <Link role="menuitem" className={styles.menuItem} href="/perfil" onClick={close}>
        <Icon name="user" />
        <span>Perfil</span>
      </Link>
      <Link role="menuitem" className={styles.menuItem} href="/perfil/notificaciones" onClick={close}>
        <Icon name="mail" />
        <span>Avisos por email</span>
      </Link>
      <button type="button" role="menuitem" className={styles.menuItem} onClick={toggle}>
        <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
        <span>{theme === 'dark' ? 'Tema claro' : 'Tema oscuro'}</span>
      </button>
      <div className={styles.sep} role="separator" />
      <button
        type="button"
        role="menuitem"
        className={styles.menuItem}
        onClick={() => {
          close();
          navigation.confirm(() => logout.mutate({}, { onSettled: () => router.replace('/login') }));
        }}
      >
        <Icon name="logout" />
        <span>Cerrar sesión</span>
      </button>
    </div>
  );
}

/**
 * App shell (map 1.1): a top bar of two rows, no sidebar.
 * Row 1: logo, workspace switcher, search (⌘K), balance, notifications, "Nuevo proyecto" and avatar.
 * Row 2: Proyectos · Publicaciones · Brand kits · Uso y facturación · Ajustes.
 * On phones the switcher and the bell move into the avatar menu and "Nuevo proyecto" is a fixed button.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? '';
  const router = useRouter();
  const { me } = useSession();
  const { can, gate, reason } = usePermissions();
  const notifications = useNotifications();
  const [commandOpen, setCommandOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const unread = notifications.data?.unread_count ?? 0;
  const canCreate = can('project.create');

  // ⌘K / Ctrl+K opens the command menu from anywhere (map 1.10).
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setCommandOpen((value) => !value);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const showFab = !NO_FAB.some((pattern) => pattern.test(pathname));

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.row}>
          <Brand href="/proyectos" />
          <span className={cx(styles.slash, styles.desktopOnly)} aria-hidden="true">
            /
          </span>
          <span className={styles.desktopOnly}>
            <WorkspaceSwitcher onCreate={() => setCreateOpen(true)} />
          </span>
          <span className={styles.spacer} />
          <button type="button" className={cx(styles.search, styles.desktopOnly)} aria-label="Buscar o ejecutar un comando" onClick={() => setCommandOpen(true)}>
            <span>
              <Icon name="search" size={14} />
              <span className={styles.searchLabel}>Buscar…</span>
            </span>
            <Kbd>⌘K</Kbd>
          </button>
          <Balance />
          <span className={cx(styles.bell, styles.desktopOnly)}>
            <Popover
              ariaLabel="Notificaciones"
              align="end"
              panelClassName={styles.notiPop}
              trigger={(props) => <IconButton icon="bell" label={unread > 0 ? `Notificaciones: ${unread} sin leer` : 'Notificaciones'} {...props} />}
            >
              {({ close }) => <NotificationsPanel onNavigate={close} />}
            </Popover>
            {unread > 0 ? <span className={styles.unread} aria-hidden="true" /> : null}
          </span>
          <span className={styles.desktopOnly}>
            <Button variant="primary" size="sm" iconLeft="plus" href="/proyectos/nuevo" {...gate('project.create')}>
              Nuevo proyecto
            </Button>
          </span>
          <span className={styles.desktopOnly}>
            <Popover
              ariaLabel="Cuenta"
              align="end"
              trigger={(props) => (
                <button type="button" className={styles.avatarBtn} aria-label="Tu cuenta" {...props}>
                  {me ? <Avatar name={me.name} seed={me.id} /> : null}
                </button>
              )}
            >
              {({ close }) => <AccountMenu close={close} mobile={false} onCreateWorkspace={() => setCreateOpen(true)} onOpenNotifications={() => setNotificationsOpen(true)} />}
            </Popover>
          </span>
          <span className={styles.mobileOnly}>
            <Popover
              ariaLabel="Cuenta y workspace"
              align="end"
              trigger={(props) => (
                <button
                  type="button"
                  className={styles.avatarBtn}
                  aria-label={`Menú de cuenta: workspace, notificaciones y perfil${unread > 0 ? `. ${unread} notificaciones nuevas` : ''}`}
                  {...props}
                >
                  {me ? <Avatar name={me.name} seed={me.id} /> : null}
                  {unread > 0 ? <span className={styles.unread} aria-hidden="true" /> : null}
                </button>
              )}
            >
              {({ close }) => <AccountMenu close={close} mobile onCreateWorkspace={() => setCreateOpen(true)} onOpenNotifications={() => setNotificationsOpen(true)} />}
            </Popover>
          </span>
        </div>
        <NavTabs
          variant="main"
          ariaLabel="Secciones"
          items={MAIN_NAV.map((item) => ({ href: item.href, label: item.label, shortLabel: item.shortLabel }))}
        />
      </header>
      <main className={styles.main} id="contenido">
        {children}
      </main>
      {showFab ? (
        canCreate ? (
          <Link className={styles.fab} href="/proyectos/nuevo" aria-label="Nuevo proyecto">
            <Icon name="plus" size={20} />
          </Link>
        ) : (
          <button type="button" className={cx(styles.fab, styles.fabDisabled)} aria-label={`Nuevo proyecto. ${reason}`} aria-disabled="true" title={reason}>
            <Icon name="plus" size={20} />
          </button>
        )
      ) : null}
      <CommandMenu open={commandOpen} onClose={() => setCommandOpen(false)} />
      <CreateWorkspaceDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      <Drawer open={notificationsOpen} onClose={() => setNotificationsOpen(false)} title="Notificaciones">
        <div style={{ margin: '-24px -24px', display: 'flex', flexDirection: 'column' }}>
          <NotificationsPanel
            onNavigate={() => {
              setNotificationsOpen(false);
              router.refresh();
            }}
          />
        </div>
      </Drawer>
    </div>
  );
}
