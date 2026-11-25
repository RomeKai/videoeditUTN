import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Feedback.module.css';

export interface ProgressBarProps {
  value: number;
  max?: number;
  /** 'info' for anything in progress (upload, render); 'accent' only for scores and review. */
  tone?: 'neutral' | 'info' | 'accent';
  /** Accessible name ("Subiendo"). */
  label: string;
  /** Text at the right: "64 %", "4 de 8". Wraps the bar in a row. */
  valueText?: ReactNode;
  /** Text at the left of the bar. */
  caption?: ReactNode;
  className?: string;
}

export function ProgressBar({ value, max = 100, tone = 'info', label, valueText, caption, className }: ProgressBarProps) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const bar = (
    <div
      className={cx(styles.progress, tone !== 'neutral' && styles[tone], !valueText && !caption && className)}
      role="progressbar"
      aria-valuenow={Math.round(value)}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={label}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  );
  if (!valueText && !caption) return bar;
  return (
    <div className={cx(styles.progressRow, className)}>
      {caption ? <span className="t-small">{caption}</span> : null}
      {bar}
      {valueText ? <span className="num t-small">{valueText}</span> : null}
    </div>
  );
}

export interface StepSegmentsProps {
  total?: number;
  /** Steps already finished. */
  done: number;
  /** Whether the step after the finished ones is running. */
  running?: boolean;
  /** Full width with equal segments (phones). */
  wide?: boolean;
  label?: string;
  className?: string;
}

/** "Paso N de 4": four 14×4 px segments (kit: .segs). Rendering is not counted: it comes after the review. */
export function StepSegments({ total = 4, done, running = true, wide = false, label, className }: StepSegmentsProps) {
  const current = Math.min(total, done + (running ? 1 : 0));
  return (
    <span
      className={cx(styles.segs, wide && styles.segsWide, className)}
      role="img"
      aria-label={label ?? (running ? `Paso ${current} de ${total}` : `${done} de ${total} pasos terminados`)}
    >
      {Array.from({ length: total }, (_, index) => (
        <span key={index} className={index < done ? styles.done : index === done && running ? styles.run : undefined} />
      ))}
    </span>
  );
}
