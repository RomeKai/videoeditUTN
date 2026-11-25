'use client';

import Link from 'next/link';
import { useId, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Icon, IconButton, Tooltip, useIsClient, type IconName } from '@/ui';
import { useModalLayer } from '@/ui/overlay';
import styles from './ActionSheet.module.css';

export interface SheetAction {
  key: string;
  label: string;
  icon: IconName;
  onSelect?: () => void;
  href?: string;
  /** Text at the right: "1080p · MP4". */
  hint?: string;
  danger?: boolean;
  /** Filled button at the top of the sheet. */
  primary?: boolean;
  separatorBefore?: boolean;
  /** Stays visible, disabled, with the reason. */
  disabledReason?: string;
}

export interface ActionSheetProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  /** Small thumbnail at the left of the title. */
  media?: ReactNode;
  actions: SheetAction[];
}

/**
 * Bottom sheet with the actions of one item, for phones (artboard M6): the
 * actions slide up from the bottom and the rest of the screen dims. Same focus
 * rules as a dialog (trap, Escape, focus back to the opener).
 */
export function ActionSheet({ open, onClose, title, subtitle, media, actions }: ActionSheetProps) {
  const titleId = useId();
  const isClient = useIsClient();
  const ref = useModalLayer<HTMLDivElement>({ open, onClose });
  if (!open || !isClient) return null;
  return createPortal(
    <div
      className={styles.backdrop}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} className={styles.sheet}>
        <span className={styles.grab} aria-hidden="true" />
        <div className={styles.head}>
          {media}
          <div className={styles.headText}>
            <span className={styles.title} id={titleId}>
              {title}
            </span>
            {subtitle ? <span className="t-small muted num">{subtitle}</span> : null}
          </div>
          <IconButton icon="close" label="Cerrar las acciones" size="lg" onClick={onClose} />
        </div>
        {actions.map((action) => {
          const className = [styles.action, action.primary ? styles.primary : '', action.danger ? styles.danger : '', action.disabledReason ? styles.disabled : '']
            .filter(Boolean)
            .join(' ');
          const content = (
            <>
              <Icon name={action.icon} size={action.primary ? 16 : 18} />
              {action.label}
              {action.hint ? <span className={`${styles.hint} num`}>{action.hint}</span> : null}
            </>
          );
          const item =
            action.href && !action.disabledReason ? (
              <Link href={action.href} className={className} onClick={onClose}>
                {content}
              </Link>
            ) : (
              <button
                type="button"
                className={className}
                aria-disabled={action.disabledReason ? true : undefined}
                onClick={() => {
                  if (action.disabledReason) return;
                  onClose();
                  action.onSelect?.();
                }}
              >
                {content}
              </button>
            );
          return (
            <div key={action.key} className={styles.item}>
              {action.separatorBefore ? <div className={styles.separator} aria-hidden="true" /> : null}
              {action.disabledReason ? (
                <Tooltip content={action.disabledReason} className={styles.tip}>
                  {item}
                </Tooltip>
              ) : (
                item
              )}
            </div>
          );
        })}
      </div>
    </div>,
    document.body,
  );
}
