import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Card } from './Card';
import styles from './Feedback.module.css';
import { Icon, type IconName } from './Icon';

export interface EmptyStateProps {
  icon?: IconName;
  /** One sentence about what goes here. */
  title: ReactNode;
  description?: ReactNode;
  /** One primary action and nothing else (map 1.8). */
  action?: ReactNode;
  /** Inside another card: no surface of its own. */
  bare?: boolean;
  className?: string;
  children?: ReactNode;
}

/** Empty state: solid card, one sentence and one action. Never a dashed border (that is for drop zones). */
export function EmptyState({ icon, title, description, action, bare = false, className, children }: EmptyStateProps) {
  const content = (
    <>
      {icon ? (
        <span className={styles.emptyIcon}>
          <Icon name={icon} size={20} />
        </span>
      ) : null}
      <h3 className="t-h3">{title}</h3>
      {description ? <p className={cx('t-small muted', styles.emptyText)}>{description}</p> : null}
      {action ? <div className={styles.emptyAction}>{action}</div> : null}
      {children}
    </>
  );
  return bare ? (
    <div className={cx(styles.empty, className)}>{content}</div>
  ) : (
    <Card className={cx(styles.empty, className)}>{content}</Card>
  );
}
