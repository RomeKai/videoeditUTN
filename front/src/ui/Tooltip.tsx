'use client';

import { cloneElement, isValidElement, useId, useRef, useState, type ReactElement, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { useDismiss } from './overlay';
import styles from './Tooltip.module.css';

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: 'top' | 'bottom';
  align?: 'center' | 'start' | 'end';
  /** Lets long texts wrap (max 280 px). */
  multiline?: boolean;
  className?: string;
}

/** Text-only hint shown on hover and on keyboard focus. The trigger gets aria-describedby. */
export function Tooltip({ content, children, side = 'top', align = 'center', multiline, className }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  // Escape hides the tooltip (and only the tooltip) without moving the pointer or the focus.
  useDismiss(open && Boolean(content), [ref], (reason) => {
    if (reason === 'escape') setOpen(false);
  });
  const child = isValidElement(children)
    ? cloneElement(children as ReactElement<{ 'aria-describedby'?: string }>, {
        'aria-describedby': open ? id : undefined,
      })
    : children;
  return (
    <span
      ref={ref}
      className={cx(styles.wrap, className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {child}
      {open && content ? (
        <span
          role="tooltip"
          id={id}
          className={cx(
            styles.tip,
            styles[side],
            align !== 'center' && styles[align],
            multiline && styles.wrapText,
          )}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}
