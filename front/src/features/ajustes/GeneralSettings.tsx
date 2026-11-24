'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useId, useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import { useDeleteWorkspace, useMembers, useTransferWorkspace, useUpdateWorkspace } from '@/api/hooks';
import { ConfirmDialog } from '@/dialogs';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { useBeforeUnload } from '@/lib/hooks';
import { ROLE_LABEL } from '@/lib/permissions';
import { usePermissions, useSession } from '@/shell/session';
import { Avatar, Button, Dialog, Field, Input, Select, useToast } from '@/ui';
import styles from './ajustes.module.css';

const NAME_MAX = 48;

function TransferDialog({ open, onClose, workspaceId, workspaceName }: { open: boolean; onClose: () => void; workspaceId: string; workspaceName: string }) {
  const toast = useToast();
  const { me } = useSession();
  const members = useMembers(workspaceId, { enabled: open });
  const transfer = useTransferWorkspace(workspaceId);
  const [chosen, setChosen] = useState('');
  const candidates = (members.data?.members ?? []).filter((member) => member.status === 'active' && member.user && member.user.id !== me?.id);
  const selected = candidates.find((member) => member.user?.id === chosen) ?? candidates[0];
  useEffect(() => {
    if (!open) {
      setChosen('');
      transfer.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = () => {
    if (!selected?.user) return;
    const name = selected.user.name;
    transfer.mutate(selected.user.id, {
      onSuccess: () => {
        onClose();
        toast.success(`${name} es ahora la persona dueña de ${workspaceName}. Vos quedaste como admin.`);
      },
    });
  };

  return (
    <Dialog
      open={open}
      onClose={transfer.isPending ? () => undefined : onClose}
      dismissable={!transfer.isPending}
      title={`Transferir ${workspaceName}`}
      description="La persona que elijas pasa a ser la dueña. Vos quedás como admin y dejás de poder borrar o transferir el workspace."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={transfer.isPending}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={submit} loading={transfer.isPending} disabled={!selected}>
            Transferir el workspace
          </Button>
        </>
      }
    >
      {members.isLoading ? (
        <p className="t-small muted" aria-busy="true">
          Cargando el equipo…
        </p>
      ) : candidates.length === 0 ? (
        <p className="t-small muted">
          {members.isError
            ? errorMessage(members.error, 'No pudimos cargar el equipo.')
            : 'Todavía no hay nadie más en el workspace. Invitá a alguien desde Miembros y, cuando acepte, vas a poder transferírselo.'}
        </p>
      ) : (
        <Field label="Nuevo dueño" error={transfer.isError ? errorMessage(transfer.error, 'No pudimos transferir el workspace.') : undefined}>
          <Select value={selected?.user?.id ?? ''} onChange={(event) => setChosen(event.target.value)} data-autofocus>
            {candidates.map((member) => (
              <option key={member.id} value={member.user?.id}>
                {member.user?.name} ({ROLE_LABEL[member.role]})
              </option>
            ))}
          </Select>
        </Field>
      )}
    </Dialog>
  );
}

/**
 * H1 · Ajustes › General: the workspace name (Dueño and Admin; read-only for
 * the rest) and the danger zone, which only the Dueño sees.
 */
export function GeneralSettings() {
  const toast = useToast();
  const router = useRouter();
  const { workspace, me, switchWorkspace } = useSession();
  const { can } = usePermissions();
  const workspaceId = workspace?.id ?? '';
  const update = useUpdateWorkspace(workspaceId);
  const remove = useDeleteWorkspace(workspaceId);
  const inputId = useId();
  const saved = workspace?.name ?? '';
  const [name, setName] = useState(saved);
  const [dialog, setDialog] = useState<'transfer' | 'delete' | null>(null);
  useEffect(() => setName(saved), [saved]);

  const canRename = can('workspace.rename');
  const owner = can('workspace.delete');
  const trimmed = name.trim();
  const dirty = canRename && trimmed !== saved;
  const empty = trimmed === '';
  useBeforeUnload(dirty);

  const serverError = update.error instanceof ApiError ? (update.error.fieldErrors.name?.[0] ?? update.error.message) : update.isError ? errorMessage(update.error) : null;
  const error = dirty && empty ? 'El workspace necesita un nombre.' : serverError;

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!dirty || empty) return;
    update.mutate(trimmed, { onSuccess: () => toast.success('Guardado. El workspace ahora se llama ' + trimmed + '.') });
  };

  const confirmDelete = () => {
    remove.mutate(saved, {
      onSuccess: async () => {
        setDialog(null);
        toast.success(`Borraste ${saved}.`);
        const next = me?.workspaces.find((item) => item.id !== workspaceId);
        if (next) {
          await switchWorkspace(next.id).catch(() => undefined);
          router.replace('/proyectos');
        } else {
          window.location.assign('/proyectos');
        }
      },
    });
  };

  if (!workspace) return null;

  return (
    <>
      <section className={styles.group} aria-labelledby={`${inputId}-title`}>
        <div className={styles.head}>
          <div className={styles.headText}>
            <h2 className="t-h3" id={`${inputId}-title`}>
              General
            </h2>
          </div>
        </div>
        <form onSubmit={save} noValidate>
          <div className={styles.row}>
            <div className={styles.label}>
              <label htmlFor={inputId}>Nombre del workspace</label>
              <span className={styles.desc} id={`${inputId}-desc`}>
                Lo ve tu equipo en el selector de workspace y en las invitaciones. Lo cambian el dueño y los admins.
              </span>
              {error ? (
                <span className="t-small" style={{ color: 'var(--err)' }} role="alert" id={`${inputId}-error`}>
                  {error}
                </span>
              ) : null}
            </div>
            <div className={styles.control}>
              <Avatar kind="workspace" name={trimmed || saved} seed={workspace.id} size="sm" />
              <Input
                id={inputId}
                value={name}
                maxLength={NAME_MAX}
                onChange={(event) => {
                  setName(event.target.value);
                  if (update.isError) update.reset();
                }}
                disabled={!canRename}
                invalid={Boolean(error)}
                aria-describedby={error ? `${inputId}-error` : `${inputId}-desc`}
                autoComplete="off"
              />
            </div>
          </div>
          <div className={styles.save}>
            <span className="t-small subtle" role="status">
              {!canRename
                ? 'Pedile a un admin que lo cambie.'
                : dirty
                  ? 'Tenés cambios sin guardar.'
                  : 'No hay cambios para guardar.'}
            </span>
            <Button variant="primary" type="submit" disabled={!dirty || empty} loading={update.isPending}>
              Guardar
            </Button>
          </div>
        </form>
      </section>

      {owner ? (
        <section className={styles.group} aria-labelledby={`${inputId}-danger`}>
          <div className={cx(styles.head, styles.headPlain)}>
            <div className={styles.headText}>
              <h2 className={cx('t-h3', styles.dangerTitle)} id={`${inputId}-danger`}>
                Zona de peligro
              </h2>
              <p className="t-small muted">Solo la persona dueña del workspace ve esta sección.</p>
            </div>
          </div>
          <div className={styles.dangerBox}>
            <div className={styles.row}>
              <div className={styles.label}>
                <span>Transferir el workspace</span>
                <span className={styles.desc}>Otra persona del equipo pasa a ser la dueña. Vos quedás como admin.</span>
              </div>
              <div className={styles.control}>
                <Button variant="secondary" aria-haspopup="dialog" onClick={() => setDialog('transfer')}>
                  Transferir…
                </Button>
              </div>
            </div>
            <div className={styles.row}>
              <div className={styles.label}>
                <span>Borrar el workspace</span>
                <span className={styles.desc}>Se borran sus proyectos, clips, brand kits y publicaciones programadas, para todo el equipo.</span>
              </div>
              <div className={styles.control}>
                <Button variant="secondary" className={styles.dangerLine} aria-haspopup="dialog" onClick={() => setDialog('delete')}>
                  Borrar el workspace…
                </Button>
              </div>
            </div>
          </div>
          <TransferDialog open={dialog === 'transfer'} onClose={() => setDialog(null)} workspaceId={workspace.id} workspaceName={saved} />
          <ConfirmDialog
            open={dialog === 'delete'}
            onClose={() => {
              setDialog(null);
              remove.reset();
            }}
            onConfirm={confirmDelete}
            loading={remove.isPending}
            tone="danger"
            title={`¿Borrar ${saved}?`}
            description="Se borran los proyectos, los clips, los brand kits y las publicaciones programadas, para todo el equipo. No se puede deshacer."
            confirmLabel="Borrar el workspace"
            typeToConfirm={{
              text: saved,
              label: (
                <>
                  Escribí <span style={{ fontWeight: 600 }}>{saved}</span> para confirmar
                </>
              ),
            }}
            error={remove.isError ? errorMessage(remove.error, 'No pudimos borrar el workspace.') : undefined}
          >
            <p className="t-small subtle">El botón se activa cuando el nombre coincide.</p>
          </ConfirmDialog>
        </section>
      ) : null}
    </>
  );
}
