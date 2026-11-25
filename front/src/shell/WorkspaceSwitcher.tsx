'use client';

import { useState, type FormEvent } from 'react';
import { useCreateWorkspace } from '@/api/hooks';
import { errorMessage } from '@/lib/error-messages';
import { formatTokens } from '@/lib/format';
import { ROLE_LABEL } from '@/lib/permissions';
import { Avatar } from '@/ui/Avatar';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { Field, Input } from '@/ui/Field';
import { Icon } from '@/ui/Icon';
import { Popover } from '@/ui/Popover';
import { useToast } from '@/ui/Toast';
import styles from './AppShell.module.css';
import { useGuardedNavigation } from './leaveGuard';
import { useSession } from './session';

/** The rows of the workspace list: shared by the desktop popover and the phone's account menu. */
export function WorkspaceList({ onDone, onCreate }: { onDone: () => void; onCreate: () => void }) {
  const { me, workspace, switchWorkspace } = useSession();
  const navigation = useGuardedNavigation();
  const toast = useToast();
  return (
    <>
      <p className={`t-over ${styles.wsGroup}`}>Tus workspaces</p>
      {(me?.workspaces ?? []).map((item) => {
        const current = item.id === workspace?.id;
        return (
          <button
            key={item.id}
            type="button"
            role="menuitemradio"
            aria-checked={current}
            className={styles.wsRow}
            onClick={() => {
              onDone();
              if (current) return;
              // Asks first when the screen has unsaved changes: after the switch the screen is gone, and they with it.
              navigation.confirm(() =>
                switchWorkspace(item.id).then((current) => {
                  // The person already picked another one: that switch speaks for itself.
                  if (!current) return;
                  toast.success(`Ahora estás en ${item.name}`);
                  navigation.push('/proyectos');
                }),
              );
            }}
          >
            <Avatar name={item.name} kind="workspace" seed={item.id} />
            <span className={styles.wsMain}>
              <span className={styles.wsRowName}>{item.name}</span>
              <span className="t-small muted">
                Plan {item.plan?.name ?? 'Gratis'} · {ROLE_LABEL[item.role]}
              </span>
            </span>
            <span className="num t-small muted">{formatTokens(item.available_balance)}</span>
            <span className={styles.wsCheck}>{current ? <Icon name="check" /> : null}</span>
          </button>
        );
      })}
      <div className={styles.sep} role="separator" />
      <button
        type="button"
        role="menuitem"
        className={styles.wsRow}
        onClick={() => {
          onDone();
          onCreate();
        }}
      >
        <span className={styles.wsPlus}>
          <Icon name="plus" />
        </span>
        <span className={styles.wsMain}>
          <span className={styles.wsRowName}>Crear workspace</span>
        </span>
      </button>
    </>
  );
}

export function CreateWorkspaceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('');
  const create = useCreateWorkspace();
  const { switchWorkspace } = useSession();
  const navigation = useGuardedNavigation();
  const toast = useToast();
  const close = () => {
    setName('');
    create.reset();
    onClose();
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    // Creating it moves the person to the new workspace: with unsaved changes on screen, that is asked first.
    navigation.confirm(() =>
      create.mutateAsync(name.trim()).then(async (workspace) => {
        close();
        await switchWorkspace(workspace.id);
        toast.success(`Creaste ${workspace.name}`);
        navigation.push('/proyectos');
      }),
    );
  };
  return (
    <Dialog
      open={open}
      onClose={close}
      title="Crear workspace"
      description="Un workspace tiene su propio plan, su saldo, sus miembros y sus proyectos."
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="create-workspace-form" loading={create.isPending} disabled={!name.trim()}>
            Crear workspace
          </Button>
        </>
      }
    >
      <form id="create-workspace-form" onSubmit={submit}>
        <Field
          label="Nombre del workspace"
          error={create.isError ? errorMessage(create.error, 'No pudimos crear el workspace.') : undefined}
          hint="Empieza en el plan Gratis."
        >
          <Input value={name} onChange={(event) => setName(event.target.value)} maxLength={100} placeholder="Estudio Norte" data-autofocus />
        </Field>
      </form>
    </Dialog>
  );
}

/** J3 · Selector de workspace in the topbar: the user's workspaces with plan and role, and "Crear workspace". */
export function WorkspaceSwitcher({ onCreate }: { onCreate: () => void }) {
  const { workspace } = useSession();
  if (!workspace) return null;
  return (
    <Popover
      ariaLabel="Workspaces"
      panelClassName={styles.wsPop}
      trigger={(props) => (
        <button type="button" className={styles.wsSwitch} aria-label={`Cambiar de workspace. Actual: ${workspace.name}`} {...props}>
          <Avatar name={workspace.name} kind="workspace" size="sm" seed={workspace.id} />
          <span className={styles.wsName}>{workspace.name}</span>
          {workspace.plan ? (
            <Badge tone="outline" size="sm">
              {workspace.plan.name}
            </Badge>
          ) : null}
          <Icon name="selector" size={14} />
        </button>
      )}
    >
      {({ close }) => (
        <div role="menu" aria-label="Workspaces" style={{ display: 'contents' }}>
          <WorkspaceList onDone={close} onCreate={onCreate} />
        </div>
      )}
    </Popover>
  );
}
