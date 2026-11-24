'use client';

import { forwardRef, useEffect, useRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Choice.module.css';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** Visible label. Without it pass aria-label. */
  label?: ReactNode;
  description?: ReactNode;
  indeterminate?: boolean;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, description, indeterminate = false, className, disabled, ...rest },
  ref,
) {
  const inner = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (inner.current) inner.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <label className={cx(styles.row, description ? styles.rowTop : null, disabled && styles.rowDisabled, className)}>
      <span className={styles.boxWrap}>
        <input
          ref={(node) => {
            inner.current = node;
            if (typeof ref === 'function') ref(node);
            else if (ref) ref.current = node;
          }}
          type="checkbox"
          className={styles.native}
          disabled={disabled}
          {...rest}
        />
        <span className={styles.check} aria-hidden="true" />
      </span>
      {label || description ? (
        <span className={styles.text}>
          {label ? <span>{label}</span> : null}
          {description ? <span className={styles.desc}>{description}</span> : null}
        </span>
      ) : null}
    </label>
  );
});
