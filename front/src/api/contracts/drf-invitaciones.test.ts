import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { errorMessage, fieldError } from '@/lib/error-messages';
import { ApiError } from '../errors';
import real from './drf/responses.json';
import { acceptInvitation, getInvitation, inviteMembers } from './workspaces';

/**
 * The invitation bodies of drf/responses.json (invitation_*, invite_* and their errors), parsed with the
 * contract and read with the helpers the screens use. back/apps/users/tests/test_invitations_contract_fixture.py
 * builds them with the real endpoints and fails when they stop matching the JSON.
 */
function parse(schema: ZodTypeAny, value: unknown) {
  const result = schema.safeParse(value);
  if (!result.success) throw new Error(result.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`).join('; '));
  return result.data;
}

const error = (key: keyof typeof real.errors, endpoint = 'x') => new ApiError(real.errors[key].status, endpoint, real.errors[key].body);

describe('invitaciones: respuestas reales del backend', () => {
  it('invitar: la fila pendiente viene sin `user` y con quien invitó, sin su email', () => {
    const { invited } = parse(inviteMembers.response, real.invitation_invited) as { invited: { user: unknown; status: string; invited_by: Record<string, unknown> }[] };
    expect(invited[0]?.user).toBeNull();
    expect(invited[0]?.status).toBe('pending');
    expect(invited[0]?.invited_by).toEqual({ id: expect.any(String), name: 'Dana Dueña' });
  });

  it('el link: datos públicos con el nombre de quien invita, nunca su email', () => {
    const preview = parse(getInvitation.response, real.invitation_preview) as Record<string, unknown>;
    expect(preview.status).toBe('pending');
    expect(JSON.stringify(preview)).not.toContain('duena@');
  });

  it('aceptar: con cuenta nueva trae los tokens; con sesión, null', () => {
    expect(parse(acceptInvitation.response, real.invitation_accepted_new_account)).toHaveProperty('tokens.access');
    expect(parse(acceptInvitation.response, real.invitation_accepted_session)).toMatchObject({ tokens: null });
  });

  it('invitar: los errores de `emails` son una oración plana; sin lugares trae `seats_exceeded`', () => {
    const already = error('invite_400_already_member', 'workspaces.invite');
    expect(already.fieldErrors.emails).toEqual(['JUANA@example.com ya es miembro o tiene una invitación pendiente.']);
    expect(error('invite_400_invalid_email', 'workspaces.invite').fieldErrors.emails).toEqual(['«juana» no es un email válido.']);
    expect(fieldError(error('invite_400_role', 'workspaces.invite'), 'role')).toBe('Elegí un rol: Admin, Editor o Lector.');
    const seats = error('invite_403_seats', 'workspaces.invite');
    expect(seats.code).toBe('seats_exceeded');
    expect(seats.message).toBe("Tu plan 'Equipo' solo permite 4 miembros. Mejora tu suscripción para invitar más gente.");
    // The role 403 has no code: its sentence is the message.
    const role = error('invite_403_role', 'workspaces.invite');
    expect(role.code).toBeUndefined();
    expect(errorMessage(role)).toBe('Solo el dueño o un admin del espacio pueden hacer esto. Pedile acceso a un admin.');
  });

  it('el link que no sirve: 404 al leer y 410 con código al aceptar', () => {
    expect(error('invitation_404', 'workspaces.invitation').status).toBe(404);
    expect(error('invitation_resend_404', 'workspaces.resendInvitation').status).toBe(404);
    const gone = error('invitation_accept_410', 'workspaces.acceptInvitation');
    expect([gone.status, gone.code]).toEqual([410, 'invitation_invalid']);
  });

  it('aceptar: la contraseña débil va en su campo; otra cuenta es un 403 con código; con cuenta y sin sesión, 401', () => {
    const weak = error('invitation_accept_400_weak', 'workspaces.acceptInvitation');
    expect(fieldError(weak, 'password')).toBe('Esa contraseña es muy común. Elegí otra.');
    expect(weak.fieldErrors.name).toBeUndefined();
    const other = error('invitation_accept_403_other_account', 'workspaces.acceptInvitation');
    expect([other.status, other.code]).toEqual([403, 'invitation_email_mismatch']);
    expect(other.message).toMatch(/^Esta invitación es para otro email/);
    expect(error('invitation_accept_401_has_account', 'workspaces.acceptInvitation').status).toBe(401);
  });
});
