import type { CSSProperties } from 'react';
import { cx } from '@/lib/cx';
import styles from './Feedback.module.css';

export interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  /** Pill shape for badges and chips. */
  pill?: boolean;
  radius?: number | string;
  className?: string;
  style?: CSSProperties;
}

/** Loading placeholder with the shape of the content (map 1.8: never a full-screen spinner). */
export function Skeleton({ width = '100%', height = 14, pill, radius, className, style }: SkeletonProps) {
  return (
    <span
      className={cx(styles.skel, className)}
      style={{ width, height, borderRadius: pill ? 999 : radius, ...style }}
      aria-hidden="true"
    />
  );
}

/** A few text lines of decreasing width. */
export function SkeletonText({ lines = 2, className }: { lines?: number; className?: string }) {
  const widths = ['60%', '35%', '48%', '72%'];
  return (
    <span className={cx(styles.skelLines, className)} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} width={widths[index % widths.length]} height={index === 0 ? 14 : 12} />
      ))}
    </span>
  );
}

/** Rows shaped like a list of projects/movements: thumbnail, two lines and a badge. */
export function SkeletonRows({ rows = 4, thumb = true, label = 'Cargando' }: { rows?: number; thumb?: boolean; label?: string }) {
  return (
    <div role="status" aria-label={label} aria-busy="true">
      {Array.from({ length: rows }, (_, index) => (
        <div className={styles.skelRow} key={index}>
          {thumb ? <Skeleton width={96} height={54} /> : null}
          <SkeletonText />
          <Skeleton width={88} height={22} pill />
        </div>
      ))}
    </div>
  );
}
