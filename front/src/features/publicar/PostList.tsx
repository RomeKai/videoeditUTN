'use client';

import type { Platform } from '@/api/contracts/common';
import type { ScheduledPost, SocialAccount } from '@/api/contracts/social';
import { formatDuration, formatTime } from '@/lib/format';
import { Badge, Button, Card, NETWORKS, NetworkChip, Num, Table, VideoThumb, cx } from '@/ui';
import { StatusBadge } from './PostBits';
import styles from './Publicaciones.module.css';
import { accountOf, failureMessage, groupByDay, isStuck, needsReconnect, sameDay, shortDayTitle } from './posts';

export interface PostActions {
  open: (post: ScheduledPost, mode?: 'view' | 'edit') => void;
  retry: (post: ScheduledPost) => void;
  askCancel: (post: ScheduledPost) => void;
  reconnect: (platform: Platform) => void;
  /** Opens the publish panel for the clip (a draft becomes a publication there). */
  /** Opens the publish panel for the clip; with a network, only that one comes ticked. */
  publish: (clipId: string, platform?: Platform) => void;
  /** Props that disable an action the role cannot do, with its reason. */
  publishGate: { disabledReason?: string };
  connectGate: { disabledReason?: string };
  /** Post with a retry or a cancel in flight. */
  busyId: string | null;
}

/** The one action a row offers, by state: the rest is in the detail. */
export function RowAction({ post, accounts, actions, touch = false }: { post: ScheduledPost; accounts?: SocialAccount[]; actions: PostActions; touch?: boolean }) {
  const size = touch ? 'md' : 'sm';
  const network = NETWORKS[post.platform].shortName;
  const busy = actions.busyId === post.id;
  if (post.status === 'PUBLISHED') {
    return post.post_url ? (
      <Button variant="ghost" size={size} href={post.post_url} external iconRight="external" aria-label={`Ver la publicación de «${post.clip.title}» en ${network}`}>
        Ver publicación
      </Button>
    ) : null;
  }
  if (post.status === 'FAILED') {
    return needsReconnect(post, accounts) ? (
      <Button size={size} iconLeft="link" aria-label={`Reconectar ${network}`} onClick={() => actions.reconnect(post.platform)} {...actions.connectGate}>
        Reconectar
      </Button>
    ) : (
      <Button size={size} iconLeft="retry" loading={busy} aria-label={`Reintentar «${post.clip.title}» en ${network}`} onClick={() => actions.retry(post)} {...actions.publishGate}>
        Reintentar
      </Button>
    );
  }
  if (post.status === 'QUEUED') {
    return (
      <Button variant="ghost" size={size} aria-label={`Cancelar «${post.clip.title}» en ${network}`} onClick={() => actions.askCancel(post)} {...actions.publishGate}>
        Cancelar
      </Button>
    );
  }
  if (post.status === 'SCHEDULED') {
    return (
      <Button variant="ghost" size={size} aria-label={`Reprogramar «${post.clip.title}» en ${network}`} onClick={() => actions.open(post, 'edit')} {...actions.publishGate}>
        Reprogramar
      </Button>
    );
  }
  if (post.status === 'DRAFT') {
    return (
      <Button size={size} iconLeft="calendar" aria-label={`Programar «${post.clip.title}» en ${network}`} onClick={() => actions.publish(post.video_clip, post.platform)} {...actions.publishGate}>
        Programar
      </Button>
    );
  }
  if (isStuck(post)) {
    return (
      <Button variant="ghost" size={size} iconLeft="retry" loading={busy} onClick={() => actions.retry(post)} {...actions.publishGate}>
        Reintentar
      </Button>
    );
  }
  return null;
}

function accountName(accounts: SocialAccount[] | undefined, platform: Platform): string {
  return accountOf(accounts, platform)?.username ?? 'Sin cuenta';
}

export interface PostListProps {
  posts: ScheduledPost[];
  accounts?: SocialAccount[];
  now: Date;
  selectedId?: string | null;
  actions: PostActions;
}

/** E2b · list on a desktop: a table grouped by day. */
export function PostTable({ posts, accounts, now, selectedId, actions }: PostListProps) {
  const days = groupByDay(posts);
  return (
    <Table className={styles.ptable} caption="Publicaciones por día">
      <thead>
        <tr>
          <th>Clip</th>
          <th style={{ width: 136 }}>Red</th>
          <th style={{ width: 64 }}>Hora</th>
          <th style={{ width: 120 }}>Estado</th>
          <th style={{ width: 156 }}>
            <span className="sr-only">Acción</span>
          </th>
        </tr>
      </thead>
      {days.map(({ day, posts: dayPosts }) => (
        <tbody key={day ? day.getTime() : 'draft'}>
          <tr className={styles.dayrow}>
            <th colSpan={5} scope="rowgroup">
              <span className={styles.dayhead}>
                <span>
                  {day ? shortDayTitle(day) : 'Sin fecha'}
                  {day && sameDay(day, now) ? (
                    <Badge tone="outline" size="sm">
                      Hoy
                    </Badge>
                  ) : null}
                </span>
                <span className="num subtle">{dayPosts.length}</span>
              </span>
            </th>
          </tr>
          {dayPosts.map((post) => {
            const selected = post.id === selectedId;
            return (
              <tr key={post.id} className={cx(selected && styles.rowSelected)} aria-selected={selected || undefined}>
                <td>
                  <div className={styles.clipcell}>
                    <VideoThumb ratio="9:16" xs seed={post.video_clip} className={styles.lthumb} />
                    <span className={styles.cliptxt}>
                      <button type="button" className={styles.ltitle} aria-expanded={selected} onClick={() => actions.open(post)}>
                        {post.clip.title}
                      </button>
                      <span className="t-label subtle">
                        {post.clip.project_title} · <Num>{formatDuration(post.clip.duration_seconds)}</Num>
                      </span>
                    </span>
                  </div>
                </td>
                <td>
                  <span className={styles.netname}>
                    <NetworkChip platform={post.platform} size="sm" hideName />
                    <span className={styles.netcol}>
                      <span>{NETWORKS[post.platform].shortName}</span>
                      <span className="t-label subtle">{accountName(accounts, post.platform)}</span>
                    </span>
                  </span>
                </td>
                <td>
                  {post.status === 'DRAFT' ? (
                    <span className="subtle" aria-label="Sin fecha">
                      —
                    </span>
                  ) : (
                    <Num>{formatTime(post.publish_at)}</Num>
                  )}
                </td>
                <td>
                  <StatusBadge status={post.status} />
                </td>
                <td className={styles.act}>
                  <RowAction post={post} accounts={accounts} actions={actions} />
                </td>
              </tr>
            );
          })}
        </tbody>
      ))}
    </Table>
  );
}

/** M8 · list on a phone: one card per day. */
export function PostCards({ posts, accounts, now, actions }: PostListProps) {
  const days = groupByDay(posts);
  return (
    <div className={styles.mDays} aria-label="Publicaciones">
      {days.map(({ day, posts: dayPosts }) => (
        <section key={day ? day.getTime() : 'draft'} className={styles.mDay}>
          <h2 className={cx(styles.mDayhead, day && sameDay(day, now) && styles.mDayToday)}>
            <span>
              {day ? shortDayTitle(day) : 'Sin fecha'}
              {day && sameDay(day, now) ? (
                <Badge tone="outline" size="sm">
                  Hoy
                </Badge>
              ) : null}
            </span>
            <span className="num subtle">{dayPosts.length}</span>
          </h2>
          <Card as="ul" className={styles.mList}>
            {dayPosts.map((post) => {
              const network = NETWORKS[post.platform].shortName;
              return (
                <li key={post.id} className={styles.mItem}>
                  <button type="button" className={styles.mOpen} aria-label={`Ver el detalle de «${post.clip.title}» en ${network}`} onClick={() => actions.open(post)}>
                    <VideoThumb ratio="9:16" xs seed={post.video_clip} className={styles.mThumb} />
                    <span className={styles.mBody}>
                      <span className={styles.mTitle}>{post.clip.title}</span>
                      <span className={styles.mSub}>
                        {post.clip.project_title} · <Num>{formatDuration(post.clip.duration_seconds)}</Num>
                      </span>
                      <span className={styles.mNet}>
                        <NetworkChip platform={post.platform} size="sm" hideName />
                        {network} · {accountName(accounts, post.platform)}
                      </span>
                    </span>
                    {post.status === 'DRAFT' ? null : <span className={cx(styles.mTime, 'num')}>{formatTime(post.publish_at)}</span>}
                  </button>
                  {post.status === 'FAILED' ? <p className={styles.mErr}>{failureMessage(post, accounts)}</p> : null}
                  <div className={styles.mFoot}>
                    <StatusBadge status={post.status} />
                    <RowAction post={post} accounts={accounts} actions={actions} touch />
                  </div>
                </li>
              );
            })}
          </Card>
        </section>
      ))}
    </div>
  );
}
