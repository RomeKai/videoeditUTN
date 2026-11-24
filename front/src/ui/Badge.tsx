import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Badge.module.css';
import { Icon, type IconName } from './Icon';

export type Tone = 'neutral' | 'ok' | 'warn' | 'err' | 'info' | 'accent';
export type BadgeTone = Tone | 'outline';

export interface BadgeProps {
  tone?: BadgeTone;
  /** 6 px dot before the text: project, clip and wallet states. */
  dot?: boolean;
  /** 12 px icon instead of the dot: publication states. */
  icon?: IconName;
  size?: 'sm' | 'md';
  className?: string;
  title?: string;
  children: ReactNode;
}

/** Status label (kit: .badge). Every status badge carries a mark (dot or icon); only outline ones go bare. */
export function Badge({ tone = 'neutral', dot = false, icon, size = 'md', className, title, children }: BadgeProps) {
  return (
    <span
      className={cx(
        styles.badge,
        tone !== 'neutral' && styles[tone],
        dot && !icon && styles.dot,
        size === 'sm' && styles.sm,
        className,
      )}
      title={title}
    >
      {icon ? <Icon name={icon} size={12} /> : null}
      {children}
    </span>
  );
}

export function StatusDot({ tone = 'neutral', live = false, label }: { tone?: Tone; live?: boolean; label?: string }) {
  return (
    <span
      className={cx(styles.sdot, tone !== 'neutral' && styles[tone], live && styles.live)}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
