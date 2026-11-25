'use client';

import Link from 'next/link';
import type { Notification } from '@/api/contracts/notifications';
import { useMarkAllNotificationsRead, useMarkNotificationRead, useNotifications } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Icon, type IconName } from '@/ui/Icon';
import { DateTime } from '@/ui/Numeric';
import { SkeletonRows } from '@/ui/Skeleton';
import styles from './AppShell.module.css';
import { useGuardedNavigation } from './leaveGuard';
import { LoadError } from './LoadError';
import { notificationHref } from './nav';

const TYPE_ICON: Record<Notification['type'], IconName> = {
  project_ready: 'clips',
  render_finished: 'check-circle',
  project_failed: 'error',
  post_failed: 'error',
  low_balance: 'tokens',
};

/** J2 · Notificaciones: the last 20, a dot on the unread ones and "Marcar todo como leído". Each one leads to its project or publication. */
export function NotificationsPanel({ onNavigate }: { onNavigate?: () => void }) {
  const notifications = useNotifications();
  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();
  const navigation = useGuardedNavigation();
  const unread = notifications.data?.unread_count ?? 0;
  const items = notifications.data?.results ?? [];
  return (
    <>
      <div className={styles.notiHead}>
        <h2 className="t-h3">Notificaciones</h2>
        {unread > 0 ? (
          <Badge tone="accent" size="sm">
            {unread} sin leer
          </Badge>
        ) : null}
        <span className={styles.spacer} />
        <Button size="sm" variant="ghost" disabled={unread === 0} loading={markAll.isPending} onClick={() => markAll.mutate()}>
          Marcar todo como leído
        </Button>
      </div>
      <div className={styles.notiList}>
        {notifications.isLoading ? <SkeletonRows rows={3} thumb={false} label="Cargando notificaciones" /> : null}
        {notifications.isError ? (
          <div style={{ padding: 12 }}>
            <LoadError error={notifications.error} onRetry={() => void notifications.refetch()} fullPageForMissing={false} />
          </div>
        ) : null}
        {notifications.isSuccess && items.length === 0 ? <p className={cx('t-small muted', styles.notiEmpty)}>No tenés notificaciones.</p> : null}
        {items.map((item) => {
          const isUnread = item.read_at === null;
          const failed = item.type === 'project_failed' || item.type === 'post_failed';
          return (
            <button
              key={item.id}
              type="button"
              className={cx(styles.noti, isUnread && styles.notiUnread)}
              onClick={() => {
                if (isUnread) markRead.mutate(item.id);
                onNavigate?.();
                navigation.push(notificationHref(item));
              }}
            >
              <span className={cx(styles.notiIcon, failed && styles.notiIconErr)}>
                <Icon name={TYPE_ICON[item.type]} />
                {isUnread ? <span className={styles.notiDot} aria-hidden="true" /> : null}
              </span>
              <span className={styles.notiMain}>
                <span className={styles.notiTitle}>{item.title}</span>
                <span className="t-small muted">{item.body}</span>
                <span className={cx('t-label subtle', styles.notiWhen)}>
                  <DateTime value={item.created_at} format="relative" />
                  {isUnread ? <span className="sr-only"> · sin leer</span> : null}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      <div className={styles.notiFoot}>
        <span className="t-small subtle">Se guardan las últimas 20</span>
        <Link className="lnk t-small" href="/perfil/notificaciones" onClick={onNavigate}>
          Avisos por email
        </Link>
      </div>
    </>
  );
}
