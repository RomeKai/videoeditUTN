import type { ElementType, HTMLAttributes, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Card.module.css';

export interface CardProps extends HTMLAttributes<HTMLElement> {
  /** Adds the 20 px padding (kit: .card-pad). Leave off when using CardHead/CardFoot or a table inside. */
  padded?: boolean;
  as?: ElementType;
}

/** Solid surface with a 1 px border; no shadow (only floating things have one). */
export function Card({ padded = false, as: Tag = 'div', className, children, ...rest }: CardProps) {
  return (
    <Tag className={cx(styles.card, padded && styles.pad, className)} {...rest}>
      {children}
    </Tag>
  );
}

export function CardHead({
  title,
  description,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx(styles.head, className)}>
      {children ?? (
        <div className={styles.headText}>
          {title ? <h3 className="t-h3">{title}</h3> : null}
          {description ? <p className="t-small muted">{description}</p> : null}
        </div>
      )}
      {actions}
    </div>
  );
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx(styles.pad, className)}>{children}</div>;
}

export function CardFoot({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx(styles.foot, className)}>{children}</div>;
}
