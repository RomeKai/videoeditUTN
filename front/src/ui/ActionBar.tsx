import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Patterns.module.css';

export interface ActionBarProps {
  /** 14 px line: "6 clips seleccionados · 4:20 · 117 tokens". */
  summary: ReactNode;
  /** 12 px line: "Incluye 10 % de descuento del plan Pro". */
  detail?: ReactNode;
  /** Ghost secondary, then the primary in size lg. Accent only when the action is "Renderizar". */
  children: ReactNode;
  className?: string;
}

/** The only fixed bottom bar: for paid or saving actions (kit: .actionbar). */
export function ActionBar({ summary, detail, children, className }: ActionBarProps) {
  return (
    <div className={cx(styles.actionbar, className)} role="region" aria-label="Acciones">
      <div className={styles.actionbarSummary}>
        <span className={styles.actionbarPrimary}>{summary}</span>
        {detail ? <span className={styles.actionbarSecondary}>{detail}</span> : null}
      </div>
      <div className={styles.actionbarActions}>{children}</div>
    </div>
  );
}
