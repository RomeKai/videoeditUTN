'use client';

import Link from 'next/link';
import { useId, useState } from 'react';
import type { MemberRole } from '@/api/contracts/common';
import type { Member } from '@/api/contracts/workspaces';
import { useMembers, useRemoveMember, useResendInvitation, useSubscription, useUpdateMember } from '@/api/hooks';
import { ConfirmDialog } from '@/dialogs';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { formatDate, plural } from '@/lib/format';
import { useIsMobile } from '@/lib/hooks';
import { ROLE_LABEL } from '@/lib/permissions';
import { LoadError } from '@/shell/LoadError';
import { RequireRole } from '@/shell/RequireSession';
import { useSession } from '@/shell/session';
import { Avatar, Badge, Button, Card, DropdownMenu, Icon, IconButton, Skeleton, Tooltip, useToast, type MenuItem } from '@/ui';
import styles from './ajustes.module.css';
import { InviteMemberDialog } from './InviteMemberDialog';
import { membersSummary } from './summaries';

const ASSIGNABLE: MemberRole[] = ['admin', 'editor', 'viewer'];
const OWNER_LOCK = 'El rol de dueño se cambia transfiriendo el workspace';

const ROLE_HELP: Array<[string, string]> = [
  ['Dueño', 'Todo, y además borrar o transferir el workspace.'],
  ['Admin', 'Miembros, redes sociales, tokens y plan, además de lo que hace un editor.'],
  ['Editor', 'Crea proyectos, revisa, renderiza, publica y edita brand kits.'],
  ['Lector', 'Ve proyectos, clips y publicaciones, y descarga clips.'],
];

function displayName(member: Member): string {
  return member.user?.name ?? member.email;
}

interface RowProps {
  member: Member;
  isSelf: boolean;
  mobile: boolean;
  onRole: (member: Member, role: MemberRole) => void;
  onRemove: (member: Member) => void;
  onResend: (member: Member) => void;
  resending: boolean;
}

function RoleControl({ member, isSelf, onRole }: Pick<RowProps, 'member' | 'isSelf' | 'onRole'>) {
  if (member.role === 'owner') {
    return (
      <span className={styles.roleFixed}>
        Dueño
        <Tooltip content={OWNER_LOCK} multiline>
          <span className={styles.lock} tabIndex={0} role="img" aria-label={OWNER_LOCK}>
            <Icon name="lock" size={12} />
          </span>
        </Tooltip>
      </span>
    );
  }
  if (isSelf) return <span className={styles.roleFixed}>{ROLE_LABEL[member.role]}</span>;
  const items: MenuItem[] = ASSIGNABLE.map((role) => ({
    key: role,
    label: ROLE_LABEL[role],
    checked: member.role === role,
    onSelect: () => {
      if (role !== member.role) onRole(member, role);
    },
  }));
  return (
    <DropdownMenu
      ariaLabel={`Rol de ${displayName(member)}`}
      align="start"
      items={items}
      trigger={(props) => (
        <button type="button" className={styles.roleBtn} {...props} aria-label={`Rol de ${displayName(member)}: ${ROLE_LABEL[member.role]}. Cambiar`}>
          {ROLE_LABEL[member.role]}
          <Icon name="chevron-down" size={14} />
        </button>
      )}
    />
  );
}

function MoreMenu({ member, onRemove, large }: { member: Member; onRemove: (member: Member) => void; large?: boolean }) {
  const pending = member.status === 'pending';
  return (
    <DropdownMenu
      ariaLabel={`Acciones para ${displayName(member)}`}
      items={[
        {
          key: 'remove',
          label: pending ? 'Quitar la invitación' : 'Quitar del workspace',
          icon: 'trash',
          danger: true,
          onSelect: () => onRemove(member),
        },
      ]}
      trigger={(props) => (
        <IconButton
          icon="more"
          size={large ? 'lg' : 'md'}
          label={`Más acciones para ${displayName(member)}: ${pending ? 'quitar la invitación' : 'quitar del workspace'}`}
          {...props}
        />
      )}
    />
  );
}

function StatusBadge({ member }: { member: Member }) {
  return member.status === 'pending' ? (
    <Badge tone="warn" dot>
      Invitación pendiente
    </Badge>
  ) : (
    <Badge tone="ok" dot>
      Activo
    </Badge>
  );
}

function Who({ member }: { member: Member }) {
  if (member.status === 'pending' && !member.user) {
    return (
      <span className={styles.pendingAvatar} aria-hidden="true">
        <Icon name="mail" size={14} />
      </span>
    );
  }
  return <Avatar name={displayName(member)} seed={member.user?.id ?? member.email} />;
}

function secondLine(member: Member) {
  if (member.status !== 'pending') return member.email;
  const inviter = member.invited_by?.name.split(' ')[0];
  return (
    <>
      {member.user ? `${member.email} · ` : ''}
      Invitado{inviter ? ` por ${inviter}` : ''} el <span className="num">{formatDate(member.joined_at)}</span>
    </>
  );
}

function MemberRow({ member, isSelf, mobile, onRole, onRemove, onResend, resending }: RowProps) {
  const pending = member.status === 'pending';
  const canAct = member.role !== 'owner' && !isSelf;
  const title = pending && !member.user ? member.email : displayName(member);
  const self = isSelf ? (
    <Badge tone="outline" size="sm">
      Vos
    </Badge>
  ) : null;
  const resend = pending ? (
    <Button variant="ghost" size="sm" onClick={() => onResend(member)} loading={resending} aria-label={`Reenviar la invitación a ${member.email}`}>
      Reenviar
    </Button>
  ) : null;

  if (mobile) {
    return (
      <li className={styles.memItem}>
        <Who member={member} />
        <div className={styles.memWho}>
          <span className={styles.memName}>
            <span className={styles.ellip}>{title}</span>
            {self}
          </span>
          <span className={cx(styles.memMail, !pending && styles.ellip)}>{secondLine(member)}</span>
          <span className={styles.memState}>
            <StatusBadge member={member} />
            {resend}
          </span>
        </div>
        <div className={styles.memCtl}>
          <RoleControl member={member} isSelf={isSelf} onRole={onRole} />
          {canAct ? <MoreMenu member={member} onRemove={onRemove} large /> : null}
        </div>
      </li>
    );
  }
  return (
    <tr>
      <td>
        <div className={styles.who}>
          <Who member={member} />
          <div className={styles.whoText}>
            <span>
              <span className={styles.ellip}>{title}</span>
              {self}
            </span>
            <span>{secondLine(member)}</span>
          </div>
        </div>
      </td>
      <td>
        <RoleControl member={member} isSelf={isSelf} onRole={onRole} />
      </td>
      <td>
        <StatusBadge member={member} />
      </td>
      <td>
        <div className={styles.acts}>
          {resend}
          {canAct ? <MoreMenu member={member} onRemove={onRemove} /> : null}
        </div>
      </td>
    </tr>
  );
}

/** "5 lugares, y ya están ocupados" */
const seatsTaken = (count: number) => `${plural(count, 'lugar', 'lugares')}, y ya ${count === 1 ? 'está ocupado' : 'están ocupados'}`;

function Members() {
  const toast = useToast();
  const titleId = useId();
  const mobile = useIsMobile();
  const { workspace, me } = useSession();
  const workspaceId = workspace?.id ?? '';
  const members = useMembers(workspaceId);
  const subscription = useSubscription();
  const updateMember = useUpdateMember(workspaceId);
  const removeMember = useRemoveMember(workspaceId);
  const resend = useResendInvitation(workspaceId);
  const [inviting, setInviting] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);

  const data = members.data;
  const planName = subscription.data?.plan.name ?? workspace?.plan?.name ?? null;
  const full = data ? data.seats_used >= data.max_members : false;
  const pendingCount = data ? data.members.filter((member) => member.status === 'pending').length : 0;
  // While a plan change waits, `max_members` is its plan's seats: the ones inviting checks.
  const limitedBy = data?.limited_by ?? null;
  const seatsPlan = limitedBy?.plan_name ?? planName;
  const fullReason = full ? `No quedan lugares en el plan${seatsPlan ? ` ${seatsPlan}` : ''}` : undefined;

  const onRole = (member: Member, role: MemberRole) =>
    updateMember.mutate(
      { memberId: member.id, role },
      {
        onSuccess: () => toast.success(`${displayName(member)} ahora es ${ROLE_LABEL[role]}`),
        onError: (error) => toast.error(errorMessage(error, 'No pudimos cambiar el rol')),
      },
    );
  const onResend = (member: Member) =>
    resend.mutate(member.id, {
      onSuccess: () => toast.success(`Reenviamos la invitación a ${member.email}`),
      onError: (error) => toast.error(errorMessage(error, 'No pudimos reenviar la invitación')),
    });
  const confirmRemove = () => {
    if (!removing) return;
    const target = removing;
    removeMember.mutate(target.id, {
      onSuccess: () => {
        setRemoving(null);
        toast.success(target.status === 'pending' ? `Quitaste la invitación a ${target.email}` : `Quitaste a ${displayName(target)} del workspace`);
      },
    });
  };

  const rows = (data?.members ?? []).map((member) => (
    <MemberRow
      key={member.id}
      member={member}
      isSelf={Boolean(member.user && member.user.id === me?.id)}
      mobile={mobile}
      onRole={onRole}
      onRemove={setRemoving}
      onResend={onResend}
      resending={resend.isPending && resend.variables === member.id}
    />
  ));

  return (
    <>
      <section className={styles.group} aria-labelledby={titleId}>
        <div className={styles.head}>
          <div className={styles.headText}>
            <h2 className="t-h3" id={titleId}>
              Miembros
            </h2>
            <p className="t-small muted num">{data ? membersSummary(data) : ' '}</p>
          </div>
          <Button variant="primary" iconLeft="plus" onClick={() => setInviting(true)} disabled={!data} disabledReason={fullReason} aria-describedby={full ? `${titleId}-full` : undefined}>
            Invitar
          </Button>
        </div>
        {members.isError ? <LoadError error={members.error} onRetry={() => void members.refetch()} className={styles.mem} /> : null}
        {data ? (
          mobile ? (
            <ul className={styles.mems}>{rows}</ul>
          ) : (
            <Card className={styles.mem}>
              <table className={styles.table}>
                <caption className="sr-only">Miembros e invitaciones del workspace</caption>
                <thead>
                  <tr>
                    <th>Persona</th>
                    <th style={{ width: 120 }}>Rol</th>
                    <th style={{ width: 176 }}>Estado</th>
                    <th style={{ width: 128 }}>
                      <span className="sr-only">Acciones</span>
                    </th>
                  </tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </Card>
          )
        ) : members.isError ? null : (
          <Card className={styles.mem} aria-busy="true" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 24 }}>
            <span className="sr-only">Cargando los miembros</span>
            {[0, 1, 2, 3].map((item) => (
              <Skeleton key={item} height={20} />
            ))}
          </Card>
        )}
        {data && full && limitedBy ? (
          <p className={styles.note} id={`${titleId}-full`}>
            {limitedBy.reason === 'pending_plan'
              ? `Tu plan pasa a ${limitedBy.plan_name} el ${formatDate(limitedBy.effective_at)} y ese plan tiene ${seatsTaken(data.max_members)}. Para invitar a alguien más, `
              : `Hay un pago en curso para pasar al plan ${limitedBy.plan_name}, que tiene ${seatsTaken(data.max_members)}. Para invitar a alguien más, `}
            <Link className={styles.link} href="/uso/planes">
              {limitedBy.reason === 'pending_plan' ? 'cancelá el cambio de plan' : `elegí «Seguir con ${planName ?? 'tu plan'}» en Planes`}
            </Link>
            .
          </p>
        ) : data && full ? (
          <p className={styles.note} id={`${titleId}-full`}>
            {pendingCount > 0
              ? `${pendingCount === 1 ? 'La invitación pendiente ocupa el último lugar' : 'Las invitaciones pendientes ocupan los últimos lugares'} del plan${planName ? ` ${planName}` : ''}. Para invitar a alguien más, quitá ${pendingCount === 1 ? 'la invitación' : 'una invitación'} o `
              : `Ya usaste ${data.max_members === 1 ? 'el único lugar' : `los ${data.max_members} lugares`} del plan${planName ? ` ${planName}` : ''}. Para invitar a alguien${data.max_members === 1 ? '' : ' más'}, `}
            <Link className={styles.link} href="/uso/planes">
              cambiá de plan
            </Link>
            .
          </p>
        ) : data && data.members.length === 1 ? (
          <p className={styles.note}>Por ahora estás solo. Invitá a tu equipo para revisar y publicar entre varios.</p>
        ) : null}
      </section>

      <section className={styles.group} aria-labelledby={`${titleId}-roles`}>
        <div className={styles.head}>
          <div className={styles.headText}>
            <h2 className="t-h3" id={`${titleId}-roles`}>
              Qué puede hacer cada rol
            </h2>
          </div>
        </div>
        <dl className={styles.roles}>
          {ROLE_HELP.map(([role, text]) => (
            <div key={role}>
              <dt>{role}</dt>
              <dd>{text}</dd>
            </div>
          ))}
        </dl>
      </section>

      <InviteMemberDialog
        open={inviting}
        onClose={() => setInviting(false)}
        workspaceId={workspaceId}
        workspaceName={workspace?.name ?? 'el workspace'}
        planName={planName}
        members={data}
      />
      <ConfirmDialog
        open={Boolean(removing)}
        onClose={() => {
          setRemoving(null);
          removeMember.reset();
        }}
        onConfirm={confirmRemove}
        loading={removeMember.isPending}
        tone="danger"
        title={
          removing
            ? removing.status === 'pending'
              ? `¿Quitar la invitación a ${removing.email}?`
              : `¿Quitar a ${displayName(removing)} de ${workspace?.name ?? 'el workspace'}?`
            : ''
        }
        description={
          removing?.status === 'pending'
            ? 'El link del email deja de funcionar y se libera el lugar del plan.'
            : 'Pierde el acceso al momento. Sus proyectos, clips y publicaciones quedan en el workspace.'
        }
        confirmLabel={removing?.status === 'pending' ? 'Quitar la invitación' : 'Quitar del workspace'}
        error={removeMember.isError ? errorMessage(removeMember.error, 'No pudimos quitarlo.') : undefined}
      />
    </>
  );
}

/** H2 · Ajustes › Miembros. Only Dueño and Admin (the section is not shown to the rest; by URL they get K2). */
export function MembersScreen() {
  return (
    <RequireRole action="members.manage">
      <Members />
    </RequireRole>
  );
}
