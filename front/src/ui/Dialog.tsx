'use client';

import { useId, useSyncExternalStore, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cx } from '@/lib/cx';
import { IconButton } from './Button';
import styles from './Dialog.module.css';
import { Icon, type IconName } from './Icon';
import { useModalLayer } from './overlay';

const subscribe = () => () => {};
/** True only in the browser after hydration: portals cannot render on the server. */
export function useIsClient(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** Sentence under the title. */
  description?: ReactNode;
  /** Icon tile above the title (D2 warn, D5 err…). */
  icon?: IconName;
  iconTone?: 'neutral' | 'warn' | 'err' | 'ok';
  /** sm 400 · md 480 (kit default) · lg 640 · xl 880 px. */
  size?: 'sm' | 'md' | 'lg' | 'xl';
  /** alertdialog for confirmations and errors that interrupt. */
  role?: 'dialog' | 'alertdialog';
  /** Buttons, right aligned; the primary action goes last. */
  footer?: ReactNode;
  /** False: Escape and the backdrop do not close it. */
  dismissable?: boolean;
  className?: string;
  children?: ReactNode;
}

/**
 * Modal dialog (kit: .modal over .backdrop). Focus moves inside on open and is
 * trapped; Escape closes it; focus goes back to the opener on close.
 * Mark the element that should get the initial focus with `data-autofocus`.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  icon,
  iconTone = 'neutral',
  size = 'md',
  role = 'dialog',
  footer,
  dismissable = true,
  className,
  children,
}: DialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const isClient = useIsClient();
  const ref = useModalLayer<HTMLDivElement>({ open, onClose, dismissable });
  if (!open || !isClient) return null;
  return createPortal(
    <div
      className={styles.backdrop}
      onMouseDown={(event) => {
        if (dismissable && event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cx(styles.modal, size !== 'md' && styles[size], className)}
      >
        <div className={styles.body}>
          {icon ? (
            <span className={cx(styles.icon, iconTone !== 'neutral' && styles[iconTone])}>
              <Icon name={icon} size={20} />
            </span>
          ) : null}
          <div className={styles.head}>
            <h2 className="t-h3" id={titleId}>
              {title}
            </h2>
            {description ? (
              <p className="muted" id={descriptionId}>
                {description}
              </p>
            ) : null}
          </div>
          {children}
        </div>
        {footer ? <div className={styles.foot}>{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  /** Sticky footer: summary and actions. */
  footer?: ReactNode;
  wide?: boolean;
  dismissable?: boolean;
  className?: string;
  children?: ReactNode;
}

/** Side panel (kit: .sheet): Publicar, publication detail. Full screen on phones. Same focus rules as Dialog. */
export function Drawer({ open, onClose, title, description, footer, wide = false, dismissable = true, className, children }: DrawerProps) {
  const titleId = useId();
  const isClient = useIsClient();
  const ref = useModalLayer<HTMLDivElement>({ open, onClose, dismissable });
  if (!open || !isClient) return null;
  return createPortal(
    <div
      className={styles.drawerBackdrop}
      onMouseDown={(event) => {
        if (dismissable && event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cx(styles.sheet, wide && styles.sheetWide, className)}
      >
        <div className={styles.sheetHead}>
          <div className={styles.sheetHeadText}>
            <h2 className="t-h3" id={titleId}>
              {title}
            </h2>
            {description ? <p className="t-small muted">{description}</p> : null}
          </div>
          <IconButton icon="close" label="Cerrar" onClick={onClose} />
        </div>
        <div className={styles.sheetBody}>{children}</div>
        {footer ? <div className={styles.sheetFoot}>{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
