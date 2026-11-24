'use client';

import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon, type IconName } from './Icon';
import styles from './SegmentedControl.module.css';
import { Tooltip } from './Tooltip';

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: IconName;
  /** Accessible name when the label is only an icon. */
  ariaLabel?: string;
  /** Blocked by plan/role: shown with a lock and a tooltip, never hidden. */
  lockedReason?: string;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
  options: ReadonlyArray<SegmentOption<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  /** Full width with equal segments (phones). */
  block?: boolean;
  /** 44 px tall on phones. */
  touch?: boolean;
  className?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  block,
  touch,
  className,
}: SegmentedControlProps<T>) {
  return (
    <div
      className={cx(styles.seg, block && styles.block, touch && styles.touch, className)}
      role="group"
      aria-label={ariaLabel}
    >
      {options.map((option) => {
        const blocked = Boolean(option.disabled || option.lockedReason);
        const button = (
          <button
            key={option.value}
            type="button"
            className={styles.item}
            aria-pressed={option.value === value}
            aria-label={option.ariaLabel}
            aria-disabled={blocked || undefined}
            onClick={() => {
              if (!blocked) onChange(option.value);
            }}
          >
            {option.lockedReason ? <Icon name="lock" size={14} /> : option.icon ? <Icon name={option.icon} size={14} /> : null}
            {option.label}
          </button>
        );
        return option.lockedReason ? (
          <Tooltip key={option.value} content={option.lockedReason} multiline>
            {button}
          </Tooltip>
        ) : (
          button
        );
      })}
    </div>
  );
}
