'use client';

import { useEffect, useState, type FormEvent } from 'react';
import type { MemberRole } from '@/api/contracts/common';
import type { MembersResponse } from '@/api/contracts/workspaces';
import { ApiError } from '@/api/errors';
import { useInviteMembers } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { ROLE_DESCRIPTION, ROLE_LABEL } from '@/lib/permissions';
import { Button, Dialog, Field, Input, Select, useToast } from '@/ui';
import styles from './ajustes.module.css';
import { seatLimitReason } from './summaries';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ROLE_OPTIONS: MemberRole[] = ['editor', 'admin', 'viewer'];
const MAX_PIPS = 15;

/** Splits "a@x.com, b@y.com" (commas, semicolons, spaces or new lines); lower-cased and without repeats. */
export function parseEmails(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).map((item) => item.trim().toLowerCase()).filter(Boolean))];
}

export interface InviteMemberDialogProps {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  workspaceName: string;
  /** Name of the workspace's plan, for "Queda 1 lugar en el plan Pro" (the plan of `limited_by` wins). */
  planName?: string | null;
  members: MembersResponse | undefined;
}

/** D7 · Invitar miembro: one or several emails, a role, and how many seats of the plan are left. */
export function InviteMemberDialog({ open, onClose, workspaceId, workspaceName, planName, members }: InviteMemberDialogProps) {
  const toast = useToast();
  const invite = useInviteMembers(workspaceId);
  const [text, setText] = useState('');
  const [role, setRole] = useState<MemberRole>('editor');
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!open) {
      setText('');
      setRole('editor');
      setTouched(false);
      invite.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const max = members?.max_members ?? 1;
  const used = members?.seats_used ?? 0;
  const active = members?.members.filter((member) => member.status === 'active').length ?? 0;
  const left = Math.max(0, max - used);
  const emails = parseEmails(text);
  const invalid = emails.find((email) => !EMAIL.test(email));
  const existing = emails.find((email) => members?.members.some((member) => member.email.toLowerCase() === email));
  // While a plan change waits, its plan's seats are the ones inviting checks (and `max` already is that).
  const limitedBy = members?.limited_by ?? null;
  const seatsPlan = limitedBy?.plan_name ?? planName;
  const plan = seatsPlan ? ` en el plan ${seatsPlan}` : ' en tu plan';

  const localError =
    emails.length === 0
      ? 'Escribí al menos un email.'
      : invalid
        ? `«${invalid}» no es un email válido.`
        : existing
          ? `${existing} ya es miembro o tiene una invitación pendiente.`
          : emails.length > left
            ? `Escribiste ${emails.length} emails y ${left === 1 ? 'queda 1 lugar' : `quedan ${left} lugares`}${plan}.`
            : null;
  const serverError = invite.isError
    ? invite.error instanceof ApiError && invite.error.fieldErrors.emails?.[0]
      ? invite.error.fieldErrors.emails[0]
      : invite.error instanceof ApiError && invite.error.code === 'seats_exceeded'
        ? invite.error.message
        : errorMessage(invite.error, 'No pudimos enviar la invitación.')
    : null;
  const error = touched ? (localError ?? serverError) : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (localError) return;
    invite.mutate(
      { emails, role },
      {
        onSuccess: ({ invited }) => {
          onClose();
          toast.success(invited.length === 1 ? `Invitación enviada a ${invited[0]?.email ?? emails[0]}` : `${invited.length} invitaciones enviadas`);
        },
      },
    );
  };

  const pips = Math.min(max, MAX_PIPS);
  const adding = localError ? 0 : emails.length;
  const after = left - adding;

  return (
    <Dialog
      open={open}
      onClose={invite.isPending ? () => undefined : onClose}
      dismissable={!invite.isPending}
      title={`Invitar a ${workspaceName}`}
      description="Les llega un email con un link para sumarse."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={invite.isPending}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="invitar-miembro" loading={invite.isPending} disabled={left === 0}>
            {emails.length > 1 ? 'Enviar invitaciones' : 'Enviar invitación'}
          </Button>
        </>
      }
    >
      <form id="invitar-miembro" onSubmit={submit} noValidate style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Field label="Emails" hint="Uno o varios, separados por coma." error={error ?? undefined}>
          <Input
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              if (invite.isError) invite.reset();
            }}
            placeholder="juan@ejemplo.com"
            autoComplete="off"
            inputMode="email"
            data-autofocus
          />
        </Field>
        <Field label="Rol" hint={ROLE_DESCRIPTION[role]}>
          <Select value={role} onChange={(event) => setRole(event.target.value as MemberRole)}>
            {ROLE_OPTIONS.map((value) => (
              <option key={value} value={value}>
                {ROLE_LABEL[value]}
              </option>
            ))}
          </Select>
        </Field>
        <div className={styles.seats}>
          <div className={styles.seatsTop}>
            <span className="t-small" style={{ fontWeight: 500 }}>
              {left === 0 ? 'No quedan lugares' : left === 1 ? 'Queda ' : 'Quedan '}
              {left > 0 ? <span className="num">{left}</span> : null}
              {left === 0 ? '' : left === 1 ? ' lugar' : ' lugares'}
              {plan}
            </span>
            <span className={styles.pips} role="img" aria-label={`${used} de ${max} lugares usados`}>
              {Array.from({ length: pips }, (_, index) => (
                <i key={index} className={cx(index < used && styles.pipOn, index >= used && index < used + adding && styles.pipNew)} />
              ))}
            </span>
          </div>
          <p className="t-label muted" style={{ fontWeight: 400 }}>
            <span className="num">{active}</span> {active === 1 ? 'miembro' : 'miembros'} de <span className="num">{max}</span>.{' '}
            {adding > 0 && after === 0
              ? adding === 1
                ? 'Esta invitación ocupa el último lugar: las pendientes también cuentan.'
                : 'Estas invitaciones ocupan los últimos lugares: las pendientes también cuentan.'
              : 'Las invitaciones pendientes también ocupan un lugar.'}
            {limitedBy ? ` ${seatLimitReason(limitedBy)}` : null}
          </p>
        </div>
      </form>
    </Dialog>
  );
}
