import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon } from './Icon';
import styles from './Patterns.module.css';
import { Skeleton } from './Skeleton';
import { Spinner } from './Spinner';
import { VideoThumb, type AspectRatio } from './VideoThumb';

export interface ClipCardProps {
  title: ReactNode;
  /** Seed for the placeholder hue (clip id). */
  seed: string;
  ratio?: AspectRatio;
  thumbnailUrl?: string | null;
  /** Seconds. */
  duration?: number | null;
  score?: number | null;
  /** Subtitle color of the project / brand kit for the caption bars. */
  captionColor?: string;
  /**
   * ready: thumbnail with score and duration.
   * rendering: skeleton with "Renderizando…".
   * failed: neutral card; red only on the icon ("No se pudo generar y no se cobró. Reintentar cuesta tokens.").
   */
  state?: 'ready' | 'rendering' | 'failed';
  /** Under the title: status badge, networks, etc. */
  meta?: ReactNode;
  /** Shown inside the failed card (the Reintentar button). */
  failedAction?: ReactNode;
  /** Top-right slot over the thumbnail (selection checkbox, menu). */
  corner?: ReactNode;
  /** Overlay on the thumbnail (hover preview, play button). */
  overlay?: ReactNode;
  size?: 'md' | 'sm';
  className?: string;
  children?: ReactNode;
}

/** The single clip card (kit: .clip-card): D3 grid, the strip of D1 (.sm) and the kit specimen. */
export function ClipCard({
  title,
  seed,
  ratio = '9:16',
  thumbnailUrl,
  duration,
  score,
  captionColor,
  state = 'ready',
  meta,
  failedAction,
  corner,
  overlay,
  size = 'md',
  className,
  children,
}: ClipCardProps) {
  return (
    <article className={cx(styles.clipCard, size === 'sm' && styles.clipSm, className)}>
      {state === 'failed' ? (
        <div className={styles.clipErr}>
          <span className={styles.clipErrIcon}>
            <Icon name="error" size={20} />
          </span>
          <span>No se pudo generar y no se cobró. Reintentar cuesta tokens.</span>
          {failedAction}
        </div>
      ) : state === 'rendering' ? (
        <VideoThumb ratio={ratio} seed={seed} captions={false}>
          <Skeleton width="100%" height="100%" radius={0} style={{ position: 'absolute', inset: 0, opacity: 0.5 }} />
          <span className={styles.clipWait} style={{ position: 'relative' }}>
            <Spinner size={12} />
            Renderizando…
          </span>
        </VideoThumb>
      ) : (
        <VideoThumb
          ratio={ratio}
          seed={seed}
          src={thumbnailUrl}
          duration={duration}
          score={score}
          captions={captionColor ?? true}
        >
          {overlay}
        </VideoThumb>
      )}
      {corner ? <span className={styles.clipSelect}>{corner}</span> : null}
      <h3 className={styles.clipTitle}>{title}</h3>
      {meta ? <div className={styles.clipMeta}>{meta}</div> : null}
      {children}
    </article>
  );
}
