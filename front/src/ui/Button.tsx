'use client';

import Link from 'next/link';
import { forwardRef, type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Button.module.css';
import { Icon, type IconName } from './Icon';
import { Spinner } from './Spinner';
import { Tooltip } from './Tooltip';

export type ButtonVariant = 'primary' | 'accent' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner and blocks clicks; the label stays so the width does not jump. */
  loading?: boolean;
  block?: boolean;
  iconLeft?: IconName;
  iconRight?: IconName;
  /** Renders a link (next/link) with the button look. */
  href?: string;
  /** Opens the link in a new tab (external checkout, social network…). */
  external?: boolean;
  type?: 'button' | 'submit' | 'reset';
  /**
   * Why the action is not available (role or plan). The button stays focusable,
   * announces itself as disabled and shows the reason in a tooltip (map 1.7).
   * For a reason that comes and goes (something is being refreshed), pass `''` while there is
   * none: the button is not blocked, and it keeps its place in the tree (and the focus) when the
   * reason appears.
   */
  disabledReason?: string;
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    block = false,
    iconLeft,
    iconRight,
    href,
    external,
    type = 'button',
    disabled,
    disabledReason,
    className,
    children,
    onClick,
    ...rest
  },
  ref,
) {
  const blocked = Boolean(disabled || disabledReason);
  const classes = cx(
    styles.btn,
    styles[variant],
    size !== 'md' && styles[size],
    block && styles.block,
    blocked && styles.disabled,
    loading && styles.loading,
    className,
  );
  const iconSize = size === 'sm' ? 14 : 16;
  const content = (
    <>
      {loading ? <Spinner size={iconSize} /> : iconLeft ? <Icon name={iconLeft} size={iconSize} /> : null}
      {children}
      {iconRight ? <Icon name={iconRight} size={iconSize} /> : null}
    </>
  );

  let element: ReactNode;
  if (href && !blocked && !loading) {
    element = (
      <Link
        href={href}
        className={classes}
        target={external ? '_blank' : undefined}
        rel={external ? 'noopener noreferrer' : undefined}
        aria-label={rest['aria-label']}
        onClick={onClick as unknown as (event: MouseEvent<HTMLAnchorElement>) => void}
      >
        {content}
      </Link>
    );
  } else {
    element = (
      <button
        ref={ref}
        type={type}
        className={classes}
        // With a reason the button must stay focusable so the tooltip is reachable by keyboard.
        disabled={disabled && !disabledReason}
        aria-disabled={blocked || loading || undefined}
        aria-busy={loading || undefined}
        onClick={(event) => {
          if (blocked || loading) {
            event.preventDefault();
            return;
          }
          onClick?.(event);
        }}
        {...rest}
      >
        {content}
      </button>
    );
  }
  return disabledReason !== undefined ? <Tooltip content={disabledReason}>{element}</Tooltip> : element;
});

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'aria-label'> {
  icon: IconName;
  /** Required accessible name. */
  label: string;
  size?: 'sm' | 'md' | 'lg';
  href?: string;
  disabledReason?: string;
  /** Shows the label as a tooltip on hover and focus. */
  tooltip?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, size = 'md', href, disabled, disabledReason, tooltip = false, className, onClick, type, ...rest },
  ref,
) {
  const blocked = Boolean(disabled || disabledReason);
  const classes = cx(
    styles.iconBtn,
    size === 'sm' && styles.iconSm,
    size === 'lg' && styles.iconLg,
    blocked && styles.disabled,
    className,
  );
  const glyph = <Icon name={icon} size={size === 'sm' ? 14 : size === 'lg' ? 20 : 16} />;
  let element: ReactNode;
  if (href && !blocked) {
    element = (
      <Link href={href} className={classes} aria-label={label}>
        {glyph}
      </Link>
    );
  } else {
    element = (
      <button
        ref={ref}
        type={type === 'submit' ? 'submit' : 'button'}
        className={classes}
        aria-label={label}
        disabled={disabled && !disabledReason}
        aria-disabled={blocked || undefined}
        onClick={(event) => {
          if (blocked) {
            event.preventDefault();
            return;
          }
          onClick?.(event);
        }}
        {...rest}
      >
        {glyph}
      </button>
    );
  }
  const tip = disabledReason ?? (tooltip ? label : undefined);
  return tip ? <Tooltip content={tip}>{element}</Tooltip> : element;
});
