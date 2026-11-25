import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { TokenPair } from './auth';
import { Id, IsoDateTime, MemberRole, NoContent, Role, UserRef } from './common';

export const Workspace = z.object({
  id: Id,
  name: z.string(),
  owner: Id,
  subscription_plan: Id.nullable(),
  created_at: IsoDateTime,
  /** Proposed: role of the current user, so the screen does not need `/me` to decide. */
  role: Role.optional(),
  /**
   * Proposed, only in the detail: Dueño and Admins, so a member who cannot open
   * Miembros knows who to ask for tokens or access (artboard R1).
   */
  admins: z.array(z.object({ id: Id, name: z.string(), role: Role })).optional(),
});
export type Workspace = z.infer<typeof Workspace>;

/** WorkspaceMember.Status */
export const MemberStatus = z.enum(['pending', 'active']);
export type MemberStatus = z.infer<typeof MemberStatus>;

export const Member = z.object({
  id: Id,
  /** Null while the invited email has no account yet. */
  user: UserRef.nullable(),
  email: z.string().email(),
  /** `owner` for Workspace.owner; otherwise WorkspaceMember.role. */
  role: Role,
  status: MemberStatus,
  joined_at: IsoDateTime,
  invited_by: UserRef.nullable().optional(),
});
export type Member = z.infer<typeof Member>;

export const MembersResponse = z.object({
  members: z.array(Member),
  /** SubscriptionPlan.max_members. A pending invitation takes a seat (map 9.1.8). */
  max_members: z.number().int().positive(),
  seats_used: z.number().int().nonnegative(),
});
export type MembersResponse = z.infer<typeof MembersResponse>;

export const InviteRequest = z.object({
  /** One or several; the UI splits the comma-separated field. */
  emails: z.array(z.string().email()).min(1),
  role: MemberRole,
});
export type InviteRequest = z.infer<typeof InviteRequest>;

export const InvitationStatus = z.enum(['pending', 'expired', 'used']);

export const InvitationPreview = z.object({
  /** To tell whether the signed-in user already belongs to it (names are not unique). */
  workspace_id: Id,
  workspace_name: z.string(),
  inviter_name: z.string(),
  role: MemberRole,
  email: z.string().email(),
  status: InvitationStatus,
  /** True: the email already has an account, so the screen asks to sign in instead of creating one. */
  has_account: z.boolean(),
});
export type InvitationPreview = z.infer<typeof InvitationPreview>;

export const listWorkspaces = defineEndpoint({
  id: 'workspaces.list',
  method: 'GET',
  path: '/api/v1/workspaces/',
  summary: 'Workspaces del usuario.',
  response: z.array(Workspace),
  workspaceScoped: false,
  errors: [],
  backend: { status: 'proposed', gap: 2, note: 'El selector (J3) usa la lista de `/me`; este listado es el recurso REST completo.' },
  screens: ['J3'],
});

export const createWorkspace = defineEndpoint({
  id: 'workspaces.create',
  method: 'POST',
  path: '/api/v1/workspaces/',
  summary: 'Crea un workspace con el usuario como dueño (plan Gratis y billetera vacía).',
  request: z.object({ name: z.string().min(1).max(100) }),
  response: Workspace,
  successStatus: 201,
  workspaceScoped: false,
  errors: [{ status: 400, when: 'Nombre vacío o demasiado largo.' }],
  backend: { status: 'proposed', gap: 2 },
  screens: ['J3', 'B1'],
});

export const getWorkspace = defineEndpoint({
  id: 'workspaces.detail',
  method: 'GET',
  path: '/api/v1/workspaces/:id/',
  summary: 'Datos del workspace.',
  response: Workspace,
  workspaceScoped: false,
  errors: [{ status: 404, when: 'No existe o el usuario no es miembro.' }],
  backend: { status: 'proposed', gap: 2 },
  screens: ['H1'],
});

export const updateWorkspace = defineEndpoint({
  id: 'workspaces.update',
  method: 'PATCH',
  path: '/api/v1/workspaces/:id/',
  summary: 'Renombra el workspace (Dueño y Admin).',
  request: z.object({ name: z.string().min(1).max(100) }),
  response: Workspace,
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'Nombre vacío o demasiado largo.' },
    { status: 403, when: 'El rol no puede renombrar (Editor, Lector).' },
  ],
  backend: { status: 'proposed', gap: [2, 3] },
  screens: ['H1', 'B1'],
});

export const deleteWorkspace = defineEndpoint({
  id: 'workspaces.delete',
  method: 'DELETE',
  path: '/api/v1/workspaces/:id/',
  summary: 'Borra el workspace. Solo el Dueño, escribiendo el nombre para confirmar.',
  request: z.object({ confirm_name: z.string().min(1) }),
  response: NoContent,
  successStatus: 204,
  workspaceScoped: false,
  errors: [
    { status: 400, when: '`confirm_name` no coincide con el nombre.' },
    { status: 403, when: 'No es el Dueño.' },
  ],
  backend: { status: 'proposed', gap: [2, 3] },
  screens: ['H1'],
});

export const transferWorkspace = defineEndpoint({
  id: 'workspaces.transfer',
  method: 'POST',
  path: '/api/v1/workspaces/:id/transfer/',
  summary: 'Transfiere el workspace a otro miembro activo. El dueño anterior queda como Admin.',
  request: z.object({ new_owner_id: Id }),
  response: Workspace,
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'El usuario no es miembro activo.' },
    { status: 403, when: 'No es el Dueño.' },
  ],
  backend: { status: 'proposed', gap: [2, 3] },
  screens: ['H1'],
});

export const listMembers = defineEndpoint({
  id: 'workspaces.members',
  method: 'GET',
  path: '/api/v1/workspaces/:id/members/',
  summary: 'Miembros e invitaciones pendientes, con los lugares usados del plan.',
  response: MembersResponse,
  workspaceScoped: false,
  errors: [{ status: 403, when: 'Solo Dueño y Admin ven la sección (para Editor y Lector no aparece).' }],
  backend: { status: 'proposed', gap: 13, note: 'Modelo: WorkspaceMember (apps/users/models.py).' },
  screens: ['H2', 'D7', 'H1'],
});

export const updateMember = defineEndpoint({
  id: 'workspaces.updateMember',
  method: 'PATCH',
  path: '/api/v1/workspaces/:id/members/:memberId/',
  summary: 'Cambia el rol de un miembro.',
  request: z.object({ role: MemberRole }),
  response: Member,
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'No se puede cambiar el rol del Dueño.' },
    { status: 403, when: 'El rol no administra miembros.' },
  ],
  backend: { status: 'proposed', gap: [3, 13] },
  screens: ['H2'],
});

export const removeMember = defineEndpoint({
  id: 'workspaces.removeMember',
  method: 'DELETE',
  path: '/api/v1/workspaces/:id/members/:memberId/',
  summary: 'Quita a un miembro o cancela una invitación pendiente.',
  response: NoContent,
  successStatus: 204,
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'No se puede quitar al Dueño.' },
    { status: 403, when: 'El rol no administra miembros.' },
  ],
  backend: { status: 'proposed', gap: [3, 13] },
  screens: ['H2'],
});

export const inviteMembers = defineEndpoint({
  id: 'workspaces.invite',
  method: 'POST',
  path: '/api/v1/workspaces/:id/invitations/',
  summary: 'Invita por email con un rol. Crea WorkspaceMember en `pending` y manda el email.',
  request: InviteRequest,
  response: z.object({ invited: z.array(Member) }),
  successStatus: 201,
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'Email inválido o ya es miembro (error en `emails`).' },
    { status: 403, when: 'Sin lugares en el plan (`code: "seats_exceeded"`) o el rol no puede invitar.' },
  ],
  backend: { status: 'proposed', gap: 13 },
  screens: ['D7', 'H2'],
});

export const resendInvitation = defineEndpoint({
  id: 'workspaces.resendInvitation',
  method: 'POST',
  path: '/api/v1/workspaces/:id/invitations/:memberId/resend/',
  summary: 'Reenvía el email de una invitación pendiente.',
  response: NoContent,
  successStatus: 204,
  workspaceScoped: false,
  errors: [{ status: 404, when: 'La invitación ya se aceptó o se canceló.' }],
  backend: { status: 'proposed', gap: 13 },
  screens: ['H2'],
});

export const getInvitation = defineEndpoint({
  id: 'workspaces.invitation',
  method: 'GET',
  path: '/api/v1/invitations/:token/',
  summary: 'Datos públicos de una invitación: quién invita, a qué workspace y con qué rol.',
  response: InvitationPreview,
  auth: false,
  workspaceScoped: false,
  errors: [{ status: 404, when: 'El token no existe. La UI muestra "Esta invitación no existe".' }],
  backend: { status: 'proposed', gap: 13, note: 'Vencida o usada responden 200 con `status`, para poder explicar el motivo.' },
  screens: ['A5'],
});

export const acceptInvitation = defineEndpoint({
  id: 'workspaces.acceptInvitation',
  method: 'POST',
  path: '/api/v1/invitations/:token/accept/',
  summary: 'Acepta la invitación. Sin cuenta: la crea con `name` y `password`. Con cuenta: basta con la sesión iniciada.',
  request: z.object({
    name: z.string().min(1).max(150).optional(),
    password: z.string().min(8).optional(),
    terms_accepted: z.boolean().optional(),
  }),
  response: z.object({
    workspace_id: Id,
    /** Present when the account was created here: the session starts right away. */
    tokens: TokenPair.nullable(),
  }),
  // Optional: the token travels when there is a session (an existing account accepts with it) and is not needed to create the account.
  auth: 'optional',
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'Faltan `name` o `password` para crear la cuenta.' },
    { status: 401, when: 'El email ya tiene cuenta y no hay sesión: la UI pide iniciar sesión. Con sesión, el cliente manda el token (y lo renueva si hace falta).' },
    { status: 410, when: 'Invitación vencida o ya usada.' },
  ],
  backend: { status: 'proposed', gap: 13 },
  screens: ['A5'],
});

export const workspacesEndpoints = [
  listWorkspaces,
  createWorkspace,
  getWorkspace,
  updateWorkspace,
  deleteWorkspace,
  transferWorkspace,
  listMembers,
  updateMember,
  removeMember,
  inviteMembers,
  resendInvitation,
  getInvitation,
  acceptInvitation,
] as const;
