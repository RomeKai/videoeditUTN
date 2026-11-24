import Link from 'next/link';
import type { CSSProperties, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Card } from './Card';
import { Icon } from './Icon';
import styles from './Patterns.module.css';

export interface KeyValueItem {
  label: ReactNode;
  value: ReactNode;
  key?: string;
}

/** Grid of key and value (artboard pattern .kv): applied configuration, result panel… */
export function KeyValue({ items, columns = 2, className }: { items: KeyValueItem[]; columns?: number; className?: string }) {
  return (
    <dl className={cx(styles.kv, className)} style={{ '--cols': columns } as CSSProperties}>
      {items.map((item, index) => (
        <div key={item.key ?? index}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export interface KpiProps {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  hint?: ReactNode;
  /** Button or detail under the hint. */
  children?: ReactNode;
  className?: string;
}

/** Data card (artboard pattern .kpi): big tabular number, unit and one line of help. */
export function Kpi({ label, value, unit, hint, children, className }: KpiProps) {
  return (
    <Card padded className={cx(styles.kpi, className)}>
      <span className="t-label muted">{label}</span>
      <p className={styles.kpiNum}>
        <span className="num">{value}</span>
        {unit ? <span className={styles.kpiUnit}>{unit}</span> : null}
      </p>
      {hint ? <p className="t-small subtle">{hint}</p> : null}
      {children}
    </Card>
  );
}

export interface PlanLockProps {
  /** Name of the plan that includes the feature: "Creador". */
  plan: string;
  /** Hide the link when already on the plans page. */
  showLink?: boolean;
  className?: string;
}

/** What the plan does not include is shown locked, never hidden (map 1.6). */
export function PlanLock({ plan, showLink = true, className }: PlanLockProps) {
  return (
    <span className={cx(styles.lock, className)}>
      <Icon name="lock" size={12} />
      Disponible en el plan {plan}
      {showLink ? (
        <>
          {' · '}
          <Link className="lnk" href="/uso/planes">
            Ver planes
          </Link>
        </>
      ) : null}
    </span>
  );
}

export interface CostLine {
  label: ReactNode;
  /** Muted formula next to the label: "260 s × 0,5 × 1 (720p)". */
  note?: ReactNode;
  value: ReactNode;
  kind?: 'line' | 'total' | 'after';
  key?: string;
}

/** Cost detail (dialog D1, summary of C1): base × resolution + extras − discount, total and balance after. */
export function CostBreakdown({ lines, className }: { lines: CostLine[]; className?: string }) {
  return (
    <dl className={cx(styles.cost, className)}>
      {lines.map((line, index) => (
        <div key={line.key ?? index} className={cx(styles.costRow, line.kind === 'total' && styles.costTotal)}>
          <dt className={line.kind === 'after' ? 'muted' : undefined}>
            {line.label}
            {line.note ? <span className="num subtle">{line.note}</span> : null}
          </dt>
          <dd className={cx('num', line.kind === 'after' && 'muted')}>{line.value}</dd>
        </div>
      ))}
    </dl>
  );
}
