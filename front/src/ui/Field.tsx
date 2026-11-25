'use client';

import {
  createContext,
  forwardRef,
  useContext,
  useId,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import { cx } from '@/lib/cx';
import { IconButton } from './Button';
import styles from './Field.module.css';
import { Icon, type IconName } from './Icon';

interface FieldControl {
  id: string;
  describedBy?: string;
  invalid: boolean;
}

const FieldContext = createContext<FieldControl | null>(null);

/** Id and ARIA wiring for a control rendered inside a <Field>. Null outside of one. */
export function useFieldControl(): FieldControl | null {
  return useContext(FieldContext);
}

export interface FieldProps {
  label: ReactNode;
  /** Help text under the control. Hidden while there is an error. */
  hint?: ReactNode;
  /** Error message under the control; marks the control as invalid. */
  error?: ReactNode;
  /** Shown after the label, e.g. a "¿Olvidaste tu contraseña?" link or a counter. */
  labelAside?: ReactNode;
  optional?: boolean;
  /** Use when the control is not one of the kit's (it needs the generated id). */
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}

/** Label on top, control, then help or error (kit: .field). */
export function Field({ label, hint, error, labelAside, optional, htmlFor, className, children }: FieldProps) {
  const generated = useId();
  const id = htmlFor ?? generated;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = error ? errorId : hint ? hintId : undefined;
  return (
    <div className={cx(styles.field, className)}>
      <div className={styles.labelRow}>
        <label className={styles.label} htmlFor={id}>
          {label}
          {optional ? <span className={styles.optional}>Opcional</span> : null}
        </label>
        {labelAside}
      </div>
      <FieldContext.Provider value={{ id, describedBy, invalid: Boolean(error) }}>{children}</FieldContext.Provider>
      {error ? (
        <span className={styles.error} id={errorId} role="alert">
          {error}
        </span>
      ) : hint ? (
        <span className={styles.hint} id={hintId}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  /** Leading icon inside the input (search boxes). */
  icon?: IconName;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, icon, className, id, ...rest },
  ref,
) {
  const field = useFieldControl();
  const isInvalid = invalid ?? field?.invalid ?? false;
  const input = (
    <input
      ref={ref}
      id={id ?? field?.id}
      aria-describedby={rest['aria-describedby'] ?? field?.describedBy}
      aria-invalid={isInvalid || undefined}
      className={cx(styles.input, isInvalid && styles.invalid, className)}
      {...rest}
    />
  );
  if (!icon) return input;
  return (
    <span className={styles.withIcon}>
      <Icon name={icon} />
      {input}
    </span>
  );
});

/** Password input with a show/hide toggle (artboards A1, A2, A4). */
export const PasswordInput = forwardRef<HTMLInputElement, Omit<InputProps, 'type' | 'icon'>>(function PasswordInput(
  props,
  ref,
) {
  const [visible, setVisible] = useState(false);
  return (
    <div className={styles.pw}>
      <Input ref={ref} type={visible ? 'text' : 'password'} {...props} />
      <IconButton
        className={styles.pwToggle}
        icon={visible ? 'eye-off' : 'eye'}
        label={visible ? 'Ocultar la contraseña' : 'Mostrar la contraseña'}
        aria-pressed={visible}
        onClick={() => setVisible((value) => !value)}
      />
    </div>
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, className, id, ...rest },
  ref,
) {
  const field = useFieldControl();
  const isInvalid = invalid ?? field?.invalid ?? false;
  return (
    <textarea
      ref={ref}
      id={id ?? field?.id}
      aria-describedby={rest['aria-describedby'] ?? field?.describedBy}
      aria-invalid={isInvalid || undefined}
      className={cx(styles.textarea, isInvalid && styles.invalid, className)}
      {...rest}
    />
  );
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
  /** Shortcut for simple lists; children (<option>) also work. */
  options?: ReadonlyArray<{ value: string; label: string; disabled?: boolean }>;
  /** Disabled first option with no value (e.g. TikTok privacy has no default). */
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { invalid, options, placeholder, className, id, children, ...rest },
  ref,
) {
  const field = useFieldControl();
  const isInvalid = invalid ?? field?.invalid ?? false;
  return (
    <select
      ref={ref}
      id={id ?? field?.id}
      aria-describedby={rest['aria-describedby'] ?? field?.describedBy}
      aria-invalid={isInvalid || undefined}
      className={cx(styles.select, isInvalid && styles.invalid, className)}
      {...rest}
    >
      {placeholder !== undefined ? (
        <option value="" disabled>
          {placeholder}
        </option>
      ) : null}
      {options?.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
      {children}
    </select>
  );
});
