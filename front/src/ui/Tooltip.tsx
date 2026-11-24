'use client';

import { cloneElement, isValidElement, useId, useState, type ReactElement, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
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
  const child = isValidElement(children)
    ? cloneElement(children as ReactElement<{ 'aria-describedby'?: string }>, {
        'aria-describedby': open ? id : undefined,
      })
    : children;
  return (
    <span
      className={cx(styles.wrap, className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false);
      }}
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
