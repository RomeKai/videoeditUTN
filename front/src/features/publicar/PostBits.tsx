'use client';

import { useState } from 'react';
import type { PostStatus } from '@/api/contracts/projects';
import type { ScheduledPost } from '@/api/contracts/social';
import { formatTime } from '@/lib/format';
import { POST_STATUS } from '@/lib/project-status';
import { Badge, Icon, NETWORKS, VideoThumb, cx, type IconName, type Tone, type VideoThumbProps } from '@/ui';
import styles from './Publicaciones.module.css';
import { shortDay, type PostGroup } from './posts';

const TONE_CLASS: Record<string, string | undefined> = {
  neutral: styles.stNeutral,
  info: styles.stInfo,
  warn: styles.stWarn,
  ok: styles.stOk,
  err: styles.stErr,
};

/**
 * The thumbnail of a publication's clip (`clip.thumbnail_url`), or the design's placeholder when the clip
 * has none. Its URL is temporary: one that no longer loads falls back to the placeholder instead of a
 * broken image.
 */
export function PostThumb({ clip, ...thumb }: { clip: Pick<ScheduledPost['clip'], 'id' | 'thumbnail_url'> } & Omit<VideoThumbProps, 'src' | 'seed' | 'ratio' | 'onImageError'>) {
  const [broken, setBroken] = useState<string | null>(null);
  const url = clip.thumbnail_url && clip.thumbnail_url !== broken ? clip.thumbnail_url : null;
  return <VideoThumb ratio="9:16" seed={clip.id} src={url} onImageError={() => setBroken(clip.thumbnail_url)} {...thumb} />;
}

export function statusIcon(status: PostStatus): IconName {
  return POST_STATUS[status].icon ?? 'edit';
}

/** Publication state as a badge: icon and word, never color alone. */
export function StatusBadge({ status, size }: { status: PostStatus; size?: 'sm' | 'md' }) {
  const meta = POST_STATUS[status];
  return (
    <Badge tone={meta.tone as Tone} icon={statusIcon(status)} size={size}>
      {meta.label}
    </Badge>
  );
}

/** Only the state's icon, in its color (calendar cards: the label is in the card's accessible name). */
export function StatusGlyph({ status, className }: { status: PostStatus; className?: string }) {
  const meta = POST_STATUS[status];
  return (
    <span className={cx(styles.pst, TONE_CLASS[meta.tone], className)} aria-hidden="true">
      <Icon name={statusIcon(status)} size={12} />
    </span>
  );
}

/** "mar 24 nov, 09:00. El precio se defiende. YouTube: Error; TikTok: Publicado" */
export function groupLabel(group: PostGroup): string {
  const states = group.posts.map((post) => `${NETWORKS[post.platform].shortName}: ${POST_STATUS[post.status].label}`).join('; ');
  return `${shortDay(group.at)}, ${formatTime(group.at)}. ${group.title}. ${states}`;
}

/** Network marks of a card, each with its state. */
export function GroupNets({ posts, plain = false }: { posts: ScheduledPost[]; plain?: boolean }) {
  return (
    <span className={styles.pnets} aria-hidden="true">
      {posts.map((post) => (
        <span key={post.id} className={styles.pnet}>
          <span className={plain ? styles.mnet : undefined}>
            <Icon name={NETWORKS[post.platform].icon} size={12} />
          </span>
          <StatusGlyph status={post.status} />
        </span>
      ))}
    </span>
  );
}
