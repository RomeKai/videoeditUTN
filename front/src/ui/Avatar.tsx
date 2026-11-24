import { cx } from '@/lib/cx';
import { initials } from '@/lib/format';
import styles from './Avatar.module.css';

const PALETTE = ['var(--av-lf)', 'var(--av-ms)', 'var(--av-cr)', 'var(--av-dp)', 'var(--av-ws)', '#8a5a2b'] as const;

/** Stable color per name (or id), from the kit's avatar palette. */
export function avatarColor(seed: string): string {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  return PALETTE[hash % PALETTE.length] ?? PALETTE[0];
}

export interface AvatarProps {
  name: string;
  /** People are circles, workspaces are rounded squares. */
  kind?: 'person' | 'workspace';
  size?: 'xs' | 'sm' | 'md' | 'lg';
  /** Seed for the color when the name can change (use the id). */
  seed?: string;
  /** Fixed background (overrides the derived one). */
  color?: string;
  /** Shows the name next to the avatar. */
  showName?: boolean;
  className?: string;
}

export function Avatar({ name, kind = 'person', size = 'md', seed, color, showName = false, className }: AvatarProps) {
  const mark = (
    <span
      className={cx(
        styles.avatar,
        size !== 'md' && styles[size],
        kind === 'workspace' && styles.ws,
        styles.colored,
        !showName && className,
      )}
      style={{ background: color ?? avatarColor(seed ?? name) }}
      aria-hidden={showName ? true : undefined}
      role={showName ? undefined : 'img'}
      aria-label={showName ? undefined : name}
    >
      {initials(name)}
    </span>
  );
  if (!showName) return mark;
  return (
    <span className={cx(styles.inline, className)}>
      {mark}
      <span>{name}</span>
    </span>
  );
}
