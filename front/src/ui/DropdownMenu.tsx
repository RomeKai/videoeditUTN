'use client';

import Link from 'next/link';
import { Fragment, useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon, type IconName } from './Icon';
import { useDismiss } from './overlay';
import type { PopoverTriggerProps } from './Popover';
import styles from './Popover.module.css';

export interface MenuItem {
  key: string;
  label: ReactNode;
  icon?: IconName;
  /** Text at the right (shortcut, amount). */
  hint?: ReactNode;
  onSelect?: () => void;
  href?: string;
  danger?: boolean;
  /** Not available: stays visible and explains why in a title (role/plan). */
  disabled?: boolean;
  disabledReason?: string;
  /** Renders a menuitemradio with a tick when true. */
  checked?: boolean;
  /** Draws a separator before this item. */
  separatorBefore?: boolean;
  /** Small heading before this item. */
  groupLabel?: string;
}

export interface DropdownMenuProps {
  trigger: (props: PopoverTriggerProps, state: { open: boolean }) => ReactNode;
  items: MenuItem[];
  ariaLabel: string;
  align?: 'start' | 'end';
  side?: 'bottom' | 'top';
  className?: string;
}

/** Action menu (role=menu): arrows, Home/End, Enter/Space, Escape, and closes on outside click. */
export function DropdownMenu({ trigger, items, ariaLabel, align = 'end', side = 'bottom', className }: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const close = useCallback((focusTrigger: boolean) => {
    setOpen(false);
    if (focusTrigger) triggerRef.current?.focus();
  }, []);

  useDismiss(open, [triggerRef, menuRef], (reason) => close(reason === 'escape'));

  const itemNodes = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])') ?? []);

  useEffect(() => {
    if (open) itemNodes()[0]?.focus({ preventScroll: true });
  }, [open]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const nodes = itemNodes();
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (event.key === 'ArrowDown') next = (index + 1) % nodes.length;
    else if (event.key === 'ArrowUp') next = (index - 1 + nodes.length) % nodes.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = nodes.length - 1;
    else if (event.key === 'Tab') {
      close(false);
      return;
    }
    if (next !== null) {
      event.preventDefault();
      nodes[next]?.focus();
    }
  };

  return (
    <span className={cx(styles.anchor, className)}>
      {trigger(
        {
          ref: triggerRef,
          onClick: () => setOpen((value) => !value),
          'aria-expanded': open,
          'aria-haspopup': 'menu',
          'aria-controls': open ? id : undefined,
        },
        { open },
      )}
      {open ? (
        <div
          ref={menuRef}
          id={id}
          role="menu"
          aria-label={ariaLabel}
          className={cx(styles.pop, styles.menu, styles[align], side === 'top' && styles.up)}
          onKeyDown={onKeyDown}
        >
          {items.map((item) => {
            const role = item.checked === undefined ? 'menuitem' : 'menuitemradio';
            const content = (
              <>
                {item.icon ? <Icon name={item.icon} /> : null}
                <span className={styles.itemLabel}>{item.label}</span>
                {item.hint ? <span className={cx(styles.itemHint, 'num')}>{item.hint}</span> : null}
                {item.checked ? <Icon name="check" /> : null}
              </>
            );
            const common = {
              role,
              'aria-checked': item.checked,
              'aria-disabled': item.disabled || undefined,
              title: item.disabled ? item.disabledReason : undefined,
              className: cx(styles.item, item.danger && styles.danger),
              tabIndex: -1,
            } as const;
            return (
              <Fragment key={item.key}>
                {item.separatorBefore ? <div className={styles.sep} role="separator" /> : null}
                {item.groupLabel ? <p className={cx('t-over', styles.groupLabel)}>{item.groupLabel}</p> : null}
                {item.href && !item.disabled ? (
                  <Link href={item.href} {...common} onClick={() => close(false)}>
                    {content}
                  </Link>
                ) : (
                  <button
                    type="button"
                    {...common}
                    onClick={() => {
                      if (item.disabled) return;
                      close(true);
                      item.onSelect?.();
                    }}
                  >
                    {content}
                  </button>
                )}
              </Fragment>
            );
          })}
        </div>
      ) : null}
    </span>
  );
}
