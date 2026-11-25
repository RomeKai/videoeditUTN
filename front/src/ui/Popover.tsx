'use client';

import { useCallback, useEffect, useId, useRef, useState, type ReactNode, type Ref } from 'react';
import { cx } from '@/lib/cx';
import { focusableElements, useDismiss } from './overlay';
import styles from './Popover.module.css';

export interface PopoverTriggerProps {
  ref: Ref<HTMLButtonElement>;
  onClick: () => void;
  'aria-expanded': boolean;
  'aria-haspopup': 'dialog' | 'menu';
  'aria-controls': string | undefined;
}

export interface PopoverProps {
  /** Render the trigger: spread the props on a <button> (Button and IconButton forward the ref). */
  trigger: (props: PopoverTriggerProps, state: { open: boolean }) => ReactNode;
  /** Panel content; receives close(). */
  children: ReactNode | ((api: { close: () => void }) => ReactNode);
  /** Accessible name of the panel. */
  ariaLabel: string;
  align?: 'start' | 'end';
  side?: 'bottom' | 'top';
  /** Controlled mode. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
  panelClassName?: string;
}

/**
 * Floating panel anchored to its trigger (kit: .pop): workspace switcher, notifications, filters.
 * Not modal: closes on outside click and on Escape (focus returns to the trigger).
 */
export function Popover({
  trigger,
  children,
  ariaLabel,
  align = 'start',
  side = 'bottom',
  open: controlled,
  onOpenChange,
  className,
  panelClassName,
}: PopoverProps) {
  const [uncontrolled, setUncontrolled] = useState(false);
  const open = controlled ?? uncontrolled;
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const setOpen = useCallback(
    (next: boolean) => {
      if (controlled === undefined) setUncontrolled(next);
      onOpenChange?.(next);
    },
    [controlled, onOpenChange],
  );

  useDismiss(open, [triggerRef, panelRef], (reason) => {
    setOpen(false);
    if (reason === 'escape') triggerRef.current?.focus();
  });

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    (panel.querySelector<HTMLElement>('[data-autofocus]') ?? focusableElements(panel)[0] ?? panel).focus({
      preventScroll: true,
    });
  }, [open]);

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, [setOpen]);

  return (
    <span className={cx(styles.anchor, className)}>
      {trigger(
        {
          ref: triggerRef,
          onClick: () => setOpen(!open),
          'aria-expanded': open,
          'aria-haspopup': 'dialog',
          'aria-controls': open ? id : undefined,
        },
        { open },
      )}
      {open ? (
        <div
          ref={panelRef}
          id={id}
          role="dialog"
          aria-label={ariaLabel}
          tabIndex={-1}
          className={cx(styles.pop, styles[align], side === 'top' && styles.up, panelClassName)}
        >
          {typeof children === 'function' ? children({ close }) : children}
        </div>
      ) : null}
    </span>
  );
}
