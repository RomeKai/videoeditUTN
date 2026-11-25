import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon, type IconName } from './Icon';
import { Crumbs, type Crumb } from './Page';
import styles from './ProjectHeader.module.css';

export interface ProjectHeaderMeta {
  icon?: IconName;
  content: ReactNode;
  /** Stable key when content is not a string. */
  key?: string;
}

export interface ProjectHeaderProps {
  crumbs?: Crumb[];
  /** <VideoThumb>; hidden in compact mode. */
  thumb?: ReactNode;
  title: ReactNode;
  /** Status badge next to the title (<ProjectStatusBadge>). */
  badge?: ReactNode;
  /** Line under the title: the status text of map section 3. */
  statusText?: ReactNode;
  /** Source, duration, creator, date, cost… */
  meta?: ProjectHeaderMeta[];
  /** One primary action plus the "more" menu. The Clips tab has no primary action. */
  actions?: ReactNode;
  /** Work screens (D2 review, D4 editor): no thumbnail, smaller title. */
  compact?: boolean;
  className?: string;
}

/** The single project header (kit: .proj-head), shared by Resumen, Clips, Transcripción, Actividad, D2, D4 and D5. */
export function ProjectHeader({
  crumbs,
  thumb,
  title,
  badge,
  statusText,
  meta,
  actions,
  compact = false,
  className,
}: ProjectHeaderProps) {
  return (
    <div className={cx(styles.wrap, className)}>
      {crumbs ? <Crumbs items={crumbs} /> : null}
      <div className={cx(styles.head, compact && styles.compact)}>
        {thumb ? <div className={styles.thumb}>{thumb}</div> : compact ? null : <span />}
        <div className={styles.body}>
          <div className={styles.title}>
            <h1 className={compact ? 't-h2' : 't-h1'}>{title}</h1>
            {badge}
          </div>
          {statusText ? <p className={styles.status}>{statusText}</p> : null}
          {meta && meta.length > 0 ? (
            <div className={styles.meta}>
              {meta.map((item, index) => (
                <span key={item.key ?? index}>
                  {item.icon ? <Icon name={item.icon} size={14} /> : null}
                  {item.content}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </div>
  );
}
