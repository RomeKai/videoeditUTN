import type { CSSProperties, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { formatDuration } from '@/lib/format';
import styles from './VideoThumb.module.css';

export type AspectRatio = '16:9' | '9:16' | '1:1';

const RATIO_CLASS: Record<AspectRatio, string> = {
  '16:9': styles.r16x9 ?? '',
  '9:16': styles.r9x16 ?? '',
  '1:1': styles.r1x1 ?? '',
};

/** Muted hues of the design's sample data. */
const HUES = ['#4a4063', '#3d4f4a', '#4f4a3a', '#3c4a5a', '#5a4a3c', '#6b4a3a', '#5a3344', '#3c5266', '#3a3550'] as const;

/** One stable low-chroma hue per item (project or clip id). */
export function thumbHue(seed: string): string {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) hash = (hash * 33 + seed.charCodeAt(index)) >>> 0;
  return HUES[hash % HUES.length] ?? HUES[0];
}

export interface VideoThumbProps {
  ratio?: AspectRatio;
  /** Seed for the placeholder hue (use the project or clip id). */
  seed?: string;
  /** Explicit hue; wins over seed. */
  hue?: string;
  /** Real image, when the backend has one. Without it the design's placeholder is drawn. */
  src?: string | null;
  alt?: string;
  /** Seconds; shown bottom-right as 0:31. */
  duration?: number | null;
  /** Virality score chip (0–100) over the top-left corner. */
  score?: number | null;
  /** Caption bars on vertical clips; the color comes from the project's subtitle color / brand kit. */
  captions?: boolean | string;
  /** Under 64 px wide: hides the silhouette and the caption bars. */
  xs?: boolean;
  width?: number | string;
  className?: string;
  /** Overlay content (play button, "Renderizando…"). */
  children?: ReactNode;
}

/**
 * Video thumbnail. In the design it is a placeholder: one muted hue per video,
 * a silhouette and, on vertical clips, two caption bars. No burnt-in text.
 */
export function VideoThumb({
  ratio = '16:9',
  seed,
  hue,
  src,
  alt = '',
  duration,
  score,
  captions,
  xs = false,
  width,
  className,
  children,
}: VideoThumbProps) {
  const showCaps = ratio === '9:16' && captions !== false && (captions !== undefined || !src);
  const capColor = typeof captions === 'string' ? captions : undefined;
  return (
    <span className={cx(styles.thumb, RATIO_CLASS[ratio], xs && styles.xs, className)} style={{ width }}>
      {src ? (
        // Remote thumbnails come from presigned storage URLs: next/image cannot optimize them.
        // eslint-disable-next-line @next/next/no-img-element
        <img className={styles.img} src={src} alt={alt} loading="lazy" />
      ) : (
        <>
          <span className={styles.frame} style={{ '--f1': hue ?? (seed ? thumbHue(seed) : undefined) } as CSSProperties} />
          {showCaps ? <span className={styles.caps} style={{ '--cap': capColor } as CSSProperties} /> : null}
        </>
      )}
      {score !== undefined && score !== null ? (
        <span className={styles.scoreChip} aria-label={`Puntaje ${score} de 100`}>
          {score}
        </span>
      ) : null}
      {children ? <span className={styles.overlay}>{children}</span> : null}
      {duration !== undefined && duration !== null ? (
        <span className={styles.duration}>{formatDuration(duration)}</span>
      ) : null}
    </span>
  );
}
