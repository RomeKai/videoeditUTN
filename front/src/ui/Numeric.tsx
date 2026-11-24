'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import {
  formatDate,
  formatDateTime,
  formatDuration,
  formatRelative,
  formatTime,
  formatTimecode,
  formatTokens,
  type DateInput,
} from '@/lib/format';

/** Amounts, prices, durations, dates and counters: Geist Sans with tabular figures (kit: .num). */
export function Num({ className, children }: { className?: string; children: ReactNode }) {
  return <span className={cx('num', className)}>{children}</span>;
}

export interface TokensProps {
  value: number | string;
  /** Appends "tokens". Default true. */
  unit?: boolean;
  /** "+1.755" / "−763,20" and green for income (movements). */
  signed?: boolean;
  className?: string;
}

/** Token amount: up to 2 decimals, none when whole ("120 tokens", "12,50 tokens"). */
export function Tokens({ value, unit = true, signed = false, className }: TokensProps) {
  const positive = signed && Number(value) > 0;
  return (
    <span className={cx('num', className)} style={positive ? { color: 'var(--ok)' } : undefined}>
      {formatTokens(value, { unit, signed })}
    </span>
  );
}

/** Timecode of editing surfaces only (transcript, timeline, in/out): Geist Mono (kit: .timecode). */
export function Timecode({ seconds, tenths, className }: { seconds: number; tenths?: boolean; className?: string }) {
  return <span className={cx('timecode', className)}>{formatTimecode(seconds, { tenths })}</span>;
}

/** Duration in Geist Sans: "1:05:32" or "0:41". */
export function Duration({ seconds, className }: { seconds: number | string | null | undefined; className?: string }) {
  return <span className={cx('num', className)}>{formatDuration(seconds)}</span>;
}

export interface DateTimeProps {
  value: DateInput;
  /** datetime: "24 nov 2026, 14:30" · date: "24 nov 2026" · time: "14:30" · relative: "hace 12 min". */
  format?: 'datetime' | 'date' | 'time' | 'relative';
  className?: string;
}

/**
 * Date in the browser's time zone (the backend sends UTC). Rendered as <time> with the ISO value.
 * The text is filled in after mount so server and client markup match whatever the time zone.
 */
export function DateTime({ value, format = 'datetime', className }: DateTimeProps) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    if (format !== 'relative') return;
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, [format]);

  const date = value instanceof Date ? value : new Date(value);
  const valid = !Number.isNaN(date.getTime());
  let text = '';
  if (now && valid) {
    if (format === 'date') text = formatDate(date);
    else if (format === 'time') text = formatTime(date);
    else if (format === 'relative') text = formatRelative(date, now);
    else text = formatDateTime(date);
  }
  return (
    <time
      className={cx('num', className)}
      dateTime={valid ? date.toISOString() : undefined}
      title={now && valid && format === 'relative' ? formatDateTime(date) : undefined}
      suppressHydrationWarning
    >
      {text || ' '}
    </time>
  );
}
