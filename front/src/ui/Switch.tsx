'use client';

import { useId, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Choice.module.css';

export interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Visible label next to the switch. Without it pass ariaLabel. */
  label?: ReactNode;
  description?: ReactNode;
  ariaLabel?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
}

export function Switch({ checked, onChange, label, description, ariaLabel, disabled, id, className }: SwitchProps) {
  const generated = useId();
  const labelId = `${id ?? generated}-label`;
  const control = (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={label ? undefined : ariaLabel}
      aria-labelledby={label ? labelId : undefined}
      disabled={disabled}
      className={styles.switch}
      onClick={() => onChange(!checked)}
    />
  );
  if (!label) return control;
  return (
    <span className={cx(styles.row, description ? styles.rowTop : null, disabled && styles.rowDisabled, className)}>
      {control}
      <span className={styles.text} id={labelId} onClick={() => !disabled && onChange(!checked)}>
        <span>{label}</span>
        {description ? <span className={styles.desc}>{description}</span> : null}
      </span>
    </span>
  );
}
