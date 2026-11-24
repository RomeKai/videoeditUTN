'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { Button, type ButtonVariant } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { Field, Input } from '@/ui/Field';
import type { IconName } from '@/ui/Icon';

export interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** 'danger' paints the confirm button red (delete, remove, disconnect). */
  tone?: 'default' | 'danger';
  /** The confirm button shows a spinner and the dialog cannot be dismissed. */
  loading?: boolean;
  icon?: IconName;
  /**
   * Asks to type this text before confirming (deleting a workspace asks for its name).
   */
  typeToConfirm?: { text: string; label: ReactNode };
  /** Error of the action, shown above the buttons. */
  error?: ReactNode;
  children?: ReactNode;
}

/** Generic confirmation (alertdialog). The safe action gets the initial focus. */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancelar',
  tone = 'default',
  loading = false,
  icon,
  typeToConfirm,
  error,
  children,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('');
  useEffect(() => {
    if (!open) setTyped('');
  }, [open]);
  const variant: ButtonVariant = tone === 'danger' ? 'danger' : 'primary';
  const blocked = typeToConfirm ? typed.trim() !== typeToConfirm.text : false;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      role="alertdialog"
      title={title}
      description={description}
      icon={icon}
      iconTone={tone === 'danger' ? 'err' : 'neutral'}
      dismissable={!loading}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading} data-autofocus={typeToConfirm ? undefined : true}>
            {cancelLabel}
          </Button>
          <Button variant={variant} onClick={onConfirm} loading={loading} disabled={blocked}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
      {typeToConfirm ? (
        <Field label={typeToConfirm.label}>
          <Input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" data-autofocus />
        </Field>
      ) : null}
      {error ? (
        <p className="t-small" style={{ color: 'var(--err)' }} role="alert">
          {error}
        </p>
      ) : null}
    </Dialog>
  );
}

export interface UnsavedChangesDialogProps {
  open: boolean;
  /** "Seguir editando": stays on the screen. */
  onStay: () => void;
  /** "Descartar": leaves and loses the changes. */
  onDiscard: () => void;
  /** What is lost: "el recorte y el título nuevos de «Atardecer en Potrerillos»". */
  what?: ReactNode;
}

/** D8: leaving the clip editor or a brand kit with changes. */
export function UnsavedChangesDialog({ open, onStay, onDiscard, what }: UnsavedChangesDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onStay}
      role="alertdialog"
      title="Tenés cambios sin guardar"
      description={what ? <>Si salís ahora, se pierden {what}.</> : 'Si salís ahora, se pierden.'}
      footer={
        <>
          <Button variant="secondary" onClick={onDiscard}>
            Descartar
          </Button>
          <Button variant="primary" onClick={onStay} data-autofocus>
            Seguir editando
          </Button>
        </>
      }
    />
  );
}

/** D5: the video did not pass moderation. Never says which category triggered it. */
export function ContentBlockedDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      role="alertdialog"
      icon="error"
      iconTone="err"
      title="No podemos procesar este video"
      description="Su contenido no cumple nuestras políticas. Si creés que es un error, escribinos."
      footer={
        <Button variant="primary" onClick={onClose} data-autofocus>
          Entendido
        </Button>
      }
    >
      <div>
        <Button size="sm" variant="secondary" iconLeft="mail" href="mailto:soporte@onecreator.app" external>
          Escribir a soporte
        </Button>
      </div>
    </Dialog>
  );
}
