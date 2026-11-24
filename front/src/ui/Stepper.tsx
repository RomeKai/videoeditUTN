'use client';

import { cx } from '@/lib/cx';
import { useFieldControl } from './Field';
import { Icon } from './Icon';
import styles from './Stepper.module.css';

export interface StepperProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** Names of the buttons, e.g. "Un clip menos" / "Un clip más". */
  decrementLabel?: string;
  incrementLabel?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
}

/** Numeric counter with − and + (kit artboard 01: .stepper). Values are clamped to [min, max]. */
export function Stepper({
  value,
  onChange,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
  step = 1,
  decrementLabel = 'Restar',
  incrementLabel = 'Sumar',
  disabled,
  id,
  className,
}: StepperProps) {
  const field = useFieldControl();
  const clamp = (next: number) => Math.min(max, Math.max(min, next));
  return (
    <span className={cx(styles.stepper, className)}>
      <button
        type="button"
        className={styles.btn}
        aria-label={decrementLabel}
        disabled={disabled || value <= min}
        onClick={() => onChange(clamp(value - step))}
      >
        <Icon name="minus" />
      </button>
      <input
        id={id ?? field?.id}
        aria-describedby={field?.describedBy}
        className={cx(styles.input, 'num')}
        inputMode="numeric"
        value={String(value)}
        disabled={disabled}
        onChange={(event) => {
          const parsed = Number(event.target.value.replace(/[^\d-]/g, ''));
          if (Number.isFinite(parsed)) onChange(clamp(parsed));
        }}
      />
      <button
        type="button"
        className={styles.btn}
        aria-label={incrementLabel}
        disabled={disabled || value >= max}
        onClick={() => onChange(clamp(value + step))}
      >
        <Icon name="plus" />
      </button>
    </span>
  );
}
