'use client';

import { forwardRef, type CSSProperties, type InputHTMLAttributes, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { useFieldControl } from './Field';
import styles from './Slider.module.css';

export interface SliderProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'onChange'> {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** Text for the value shown at the right (e.g. "1,5×"). */
  formatValue?: (value: number) => ReactNode;
}

export const Slider = forwardRef<HTMLInputElement, SliderProps>(function Slider(
  { value, onChange, min = 0, max = 100, step = 1, formatValue, className, id, ...rest },
  ref,
) {
  const field = useFieldControl();
  const fill = max > min ? ((value - min) / (max - min)) * 100 : 0;
  return (
    <span className={cx(styles.wrap, className)}>
      <input
        ref={ref}
        type="range"
        id={id ?? field?.id}
        aria-describedby={field?.describedBy}
        className={styles.slider}
        style={{ '--fill': `${fill}%` } as CSSProperties}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        {...rest}
      />
      {formatValue ? <span className={cx(styles.value, 'num')}>{formatValue(value)}</span> : null}
    </span>
  );
});
