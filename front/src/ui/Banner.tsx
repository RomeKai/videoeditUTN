import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Feedback.module.css';
import { Icon, type IconName } from './Icon';

export type BannerTone = 'neutral' | 'info' | 'warn' | 'err' | 'ok';

const TONE_ICON: Record<BannerTone, IconName> = {
  neutral: 'info',
  info: 'info',
  warn: 'warning',
  err: 'error',
  ok: 'check-circle',
};

export interface BannerProps {
  tone?: BannerTone;
  title?: ReactNode;
  children?: ReactNode;
  /** Button or link at the right (e.g. Reintentar). */
  action?: ReactNode;
  /** Override the tone's icon (e.g. 'lock'). */
  icon?: IconName;
  /** role=alert for errors that just happened; status otherwise. */
  live?: 'alert' | 'status' | 'off';
  className?: string;
  id?: string;
}

/**
 * Inline notice (kit: .callout). One icon per tone: info = circle with "i", warn = triangle,
 * err = circle with "!", ok = circle with a tick. "ok" keeps the neutral surface, as in the kit.
 */
export function Banner({ tone = 'neutral', title, children, action, icon, live, className, id }: BannerProps) {
  const role = live === 'off' ? undefined : (live ?? (tone === 'err' ? 'alert' : 'status'));
  const oneLine = !title || !children;
  return (
    <div
      id={id}
      role={role}
      className={cx(
        styles.callout,
        (tone === 'info' || tone === 'warn' || tone === 'err') && styles[tone],
        oneLine && styles.calloutLine,
        className,
      )}
    >
      <span className={cx(styles.calloutIcon, tone !== 'neutral' && styles[tone])}>
        <Icon name={icon ?? TONE_ICON[tone]} />
      </span>
      <div className={styles.calloutBody}>
        {title ? <p className={styles.calloutTitle}>{title}</p> : null}
        {children ? <div className={title ? styles.calloutText : 't-small'}>{children}</div> : null}
      </div>
      {action ? <div className={styles.calloutAction}>{action}</div> : null}
    </div>
  );
}

export interface FixedBannerProps {
  icon?: IconName;
  tone?: 'warn' | 'info';
  /** Three fading dots after the text ("Reintentando"). */
  pulsing?: boolean;
  children: ReactNode;
  className?: string;
}

/** Full-width strip above the app (K4 offline, maintenance notice). It never covers the content. */
export function FixedBanner({ icon = 'warning', tone = 'warn', pulsing = false, children, className }: FixedBannerProps) {
  return (
    <div className={cx(styles.fixedBanner, tone === 'info' && styles.infoBanner, className)} role="status">
      <Icon name={icon} />
      <span>{children}</span>
      {pulsing ? (
        <span className={styles.pulse} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      ) : null}
    </div>
  );
}
