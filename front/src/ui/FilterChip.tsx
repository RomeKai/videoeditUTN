'use client';

import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon, type IconName } from './Icon';
import styles from './Patterns.module.css';

export interface FilterChipProps {
  /** Plural label: "Programados". */
  label: ReactNode;
  count?: number;
  pressed: boolean;
  onPressedChange: (pressed: boolean) => void;
  /** Publication states carry their icon inside the chip. */
  icon?: IconName;
  className?: string;
}

/** Filter chip (kit: .fchip): plural label, count, filled when pressed. */
export function FilterChip({ label, count, pressed, onPressedChange, icon, className }: FilterChipProps) {
  return (
    <button type="button" className={cx(styles.fchip, className)} aria-pressed={pressed} onClick={() => onPressedChange(!pressed)}>
      {icon ? <Icon name={icon} size={12} /> : null}
      {label}
      {count !== undefined ? <span className={styles.count}>{count}</span> : null}
    </button>
  );
}

export interface FilterOption<T extends string> {
  value: T;
  label: string;
  count?: number;
  icon?: IconName;
  /** "Con error" only shows when its count is above zero. */
  hideWhenEmpty?: boolean;
}

export interface FilterChipsProps<T extends string> {
  options: ReadonlyArray<FilterOption<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
}

/** Single-choice group of filter chips. The first option is always "Todos". */
export function FilterChips<T extends string>({ options, value, onChange, ariaLabel, className }: FilterChipsProps<T>) {
  return (
    <div className={cx(styles.fchips, className)} role="group" aria-label={ariaLabel}>
      {options
        .filter((option) => !(option.hideWhenEmpty && !option.count && option.value !== value))
        .map((option) => (
          <FilterChip
            key={option.value}
            label={option.label}
            count={option.count}
            icon={option.icon}
            pressed={option.value === value}
            onPressedChange={() => onChange(option.value)}
          />
        ))}
    </div>
  );
}
