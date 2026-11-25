'use client';

import { createContext, useContext, useId, type CSSProperties, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Choice.module.css';
import { Tooltip } from './Tooltip';

interface RadioGroupContextValue {
  name: string;
  value: string | undefined;
  onChange: (value: string) => void;
  disabled?: boolean;
}

const RadioGroupContext = createContext<RadioGroupContextValue | null>(null);

export interface RadioGroupProps {
  /** Group label (rendered as <legend>). Use ariaLabel for a hidden one. */
  label?: ReactNode;
  ariaLabel?: string;
  value: string | undefined;
  onChange: (value: string) => void;
  name?: string;
  disabled?: boolean;
  /** 'cards' lays RadioCard children in a grid of `columns`. */
  layout?: 'column' | 'row' | 'cards';
  columns?: number;
  className?: string;
  children: ReactNode;
}

export function RadioGroup({
  label,
  ariaLabel,
  value,
  onChange,
  name,
  disabled,
  layout = 'column',
  columns = 3,
  className,
  children,
}: RadioGroupProps) {
  const generated = useId();
  return (
    <fieldset
      className={cx(styles.group, className)}
      aria-label={ariaLabel}
      role="radiogroup"
      disabled={disabled}
    >
      {label ? <legend className={styles.legend}>{label}</legend> : null}
      <div
        className={cx(styles.group, layout === 'row' && styles.groupRow, layout === 'cards' && styles.cards)}
        style={layout === 'cards' ? ({ '--cols': columns } as CSSProperties) : undefined}
      >
        <RadioGroupContext.Provider value={{ name: name ?? generated, value, onChange, disabled }}>
          {children}
        </RadioGroupContext.Provider>
      </div>
    </fieldset>
  );
}

function useRadioGroup(component: string): RadioGroupContextValue {
  const group = useContext(RadioGroupContext);
  if (!group) throw new Error(`<${component}> tiene que ir dentro de <RadioGroup>.`);
  return group;
}

export interface RadioProps {
  value: string;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}

export function Radio({ value, label, description, disabled }: RadioProps) {
  const group = useRadioGroup('Radio');
  const isDisabled = disabled || group.disabled;
  return (
    <label className={cx(styles.row, description ? styles.rowTop : null, isDisabled && styles.rowDisabled)}>
      <span className={styles.boxWrap}>
        <input
          type="radio"
          className={styles.native}
          name={group.name}
          value={value}
          checked={group.value === value}
          disabled={isDisabled}
          onChange={() => group.onChange(value)}
        />
        <span className={styles.radio} aria-hidden="true" />
      </span>
      <span className={styles.text}>
        <span>{label}</span>
        {description ? <span className={styles.desc}>{description}</span> : null}
      </span>
    </label>
  );
}

export interface RadioCardProps {
  value: string;
  title: ReactNode;
  description?: ReactNode;
  /** Drawing or example shown above the title (layout sketches, style samples). */
  media?: ReactNode;
  /** Blocked by plan or by another option: stays visible, cannot be chosen, explains why. */
  lockedReason?: string;
  disabled?: boolean;
  children?: ReactNode;
}

/** Option card: the chosen one gets the --fg border and a tick in the corner (kit: .radio-card). */
export function RadioCard({ value, title, description, media, lockedReason, disabled, children }: RadioCardProps) {
  const group = useRadioGroup('RadioCard');
  const checked = group.value === value;
  const blocked = Boolean(disabled || group.disabled || lockedReason);
  const card = (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      aria-disabled={blocked || undefined}
      className={cx(styles.card, blocked && styles.cardLocked)}
      onClick={() => {
        if (!blocked) group.onChange(value);
      }}
    >
      {media}
      <span className={styles.cardTitle}>{title}</span>
      {description ? <span className={styles.desc}>{description}</span> : null}
      {children}
    </button>
  );
  return lockedReason ? (
    <Tooltip content={lockedReason} multiline>
      {card}
    </Tooltip>
  ) : (
    card
  );
}
