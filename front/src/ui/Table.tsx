import Link from 'next/link';
import type { HTMLAttributes, ReactNode, TableHTMLAttributes } from 'react';
import { cx } from '@/lib/cx';
import styles from './Table.module.css';

export interface TableProps extends TableHTMLAttributes<HTMLTableElement> {
  dense?: boolean;
  /** Caption for assistive tech (rendered visually hidden). */
  caption?: string;
}

/**
 * Data table (kit: .table). Use plain <thead>/<tbody>/<tr>/<th>/<td> inside.
 * Put it in a <Card> (no padding); it scrolls sideways on narrow screens.
 * Right-align amounts with className="right num".
 */
export function Table({ dense = false, caption, className, children, ...rest }: TableProps) {
  return (
    <div className={styles.wrap}>
      <table className={cx(styles.table, dense && styles.dense, className)} {...rest}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        {children}
      </table>
    </div>
  );
}

export interface ListRowProps extends HTMLAttributes<HTMLElement> {
  /** Makes the whole row a link. */
  href?: string;
  children: ReactNode;
}

/** Row of a list inside a card (kit: .list-row), e.g. a project row. */
export function ListRow({ href, className, children, ...rest }: ListRowProps) {
  if (href) {
    return (
      <Link href={href} className={cx(styles.row, className)}>
        {children}
      </Link>
    );
  }
  return (
    <div className={cx(styles.row, className)} {...rest}>
      {children}
    </div>
  );
}

/** Flexible middle column of a ListRow: title and a muted second line. */
export function ListRowMain({ className, children }: { className?: string; children: ReactNode }) {
  return <span className={cx(styles.rowMain, className)}>{children}</span>;
}
