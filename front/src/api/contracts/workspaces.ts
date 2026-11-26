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
  /** Role of the current user in this workspace, so the screen does not need `/me` to decide. The backend always sends it. */
  role: Role.optional(),
  /**
   * Only in the detail: Dueño first, then the active Admins, so a member who cannot open
   * Miembros knows who to ask for tokens or access (artboard R1). `name` is the email when the account has no name.
   */
  admins: z.array(z.object({ id: Id, name: z.string(), role: Role })).optional(),
});
export type Workspace = z.infer<typeof Workspace>;

/** WorkspaceMember.Status */
export const MemberStatus = z.enum(['pending', 'active']);
export type MemberStatus = z.infer<typeof MemberStatus>;

export const Member = z.object({
  id: Id,
  /**
   * Null while the invited email has no account yet. Today the backend always sends it:
   * WorkspaceMember needs a user, so an invitation without an account does not exist yet.
   */
  user: UserRef.nullable(),
  email: z.string().email(),
  /** `owner` for Workspace.owner; otherwise WorkspaceMember.role. */
  role: Role,
  status: MemberStatus,
  joined_at: IsoDateTime,
  /** Always null from the backend today: WorkspaceMember does not record who invited. */
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
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceListView.get (WorkspaceSerializer)',
    note: 'Los workspaces propios y los de una membresía activa, del más antiguo al más nuevo, con `role`. Una invitación pendiente no aparece. El selector (J3) usa la lista de `/me`; este listado es el recurso REST completo.',
  },
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
  errors: [
    { status: 400, when: 'Nombre vacío (o solo espacios) o de más de 100 caracteres: `{name: [mensaje]}`.' },
    { status: 409, when: 'El usuario ya es dueño de 10 workspaces (`code: "workspace_limit"`). Los workspaces ajenos donde es miembro no cuentan.' },
    { status: 429, when: 'Demasiadas creaciones seguidas (10 por hora por usuario; cuentan también los intentos rechazados). `detail` dice cuánto esperar.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceListView.post (apps/users/workspaces.py · create_owned_workspace)',
    note: 'El mismo camino que el registro: plan `DEFAULT_PLAN_NAME` (Gratis; `subscription_plan: null` si ese plan no existe), billetera en 0 y el usuario como Admin activo además de dueño. El nombre se guarda sin espacios a los costados. Tope: 10 workspaces propios por usuario (`MAX_OWNED_WORKSPACES`) y un límite de intentos (`THROTTLE_WORKSPACE_CREATE`).',
  },
  screens: ['J3', 'B1'],
});

export const getWorkspace = defineEndpoint({
  id: 'workspaces.detail',
  method: 'GET',
  path: '/api/v1/workspaces/:id/',
  summary: 'Datos del workspace.',
  response: Workspace,
  workspaceScoped: false,
  errors: [{ status: 404, when: 'El workspace no existe o el usuario no es dueño ni miembro activo (una invitación pendiente no alcanza). Mismo `detail` para todos los casos.' }],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceDetailView.get (WorkspaceDetailSerializer)',
    note: 'Lo lee cualquier rol. Trae `role` y `admins`. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
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
    { status: 400, when: 'Nombre vacío (o solo espacios) o de más de 100 caracteres: `{name: [mensaje]}`.' },
    { status: 403, when: 'El rol no puede renombrar (Editor, Lector). Solo `detail`, sin `code`.' },
    { status: 404, when: 'El workspace no existe o el usuario no es dueño ni miembro activo (una invitación pendiente no alcanza). Mismo `detail` para todos los casos.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceDetailView.patch',
    note: 'Solo cambia `name`. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
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
    { status: 400, when: '`confirm_name` falta o no es exactamente el nombre: `{confirm_name: [mensaje]}`.' },
    { status: 403, when: 'No es el Dueño. Solo `detail`, sin `code`.' },
    { status: 404, when: 'El workspace no existe o el usuario no es dueño ni miembro activo (una invitación pendiente no alcanza). Mismo `detail` para todos los casos.' },
    {
      status: 409,
      when: 'No se borra nada. Cuerpo `{error, code}`, y el `error` se muestra tal cual. `project_processing`: hay un proyecto subido, en ingesta o renderizando (uno trabado se borra primero). `posts_in_flight`: hay una publicación en cola o saliendo. `reservation_open`: hay tokens reservados sin liquidar. `balance_left`: queda saldo disponible (trae `available_balance`); los tokens nunca se borran. `last_workspace`: es el único workspace del usuario.',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceDetailView.delete',
    note: 'Borra para siempre el workspace con sus proyectos, clips y publicaciones (las programadas que no salieron no se publican), brand kits, miembros e invitaciones, y la billetera, ya en cero, con sus movimientos. Las cuentas de los miembros no se tocan. No limpia los archivos en R2 ni el perfil de Ayrshare. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
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
    { status: 400, when: '`new_owner_id` no es un miembro activo (una invitación pendiente no alcanza) o es el propio Dueño: `{new_owner_id: [mensaje]}`. Con `detail`: el Dueño no tenía fila de miembro y no queda lugar en el plan para dejarlo como Admin.' },
    { status: 403, when: 'No es el Dueño. Solo `detail`, sin `code`.' },
    { status: 404, when: 'El workspace no existe o el usuario no es dueño ni miembro activo (una invitación pendiente no alcanza). Mismo `detail` para todos los casos.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceTransferView.post',
    note: 'La respuesta trae `owner` nuevo y `role: "admin"` (el rol de quien transfirió). El plan, la billetera y los lugares no cambian. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
  screens: ['H1'],
});

export const listMembers = defineEndpoint({
  id: 'workspaces.members',
  method: 'GET',
  path: '/api/v1/workspaces/:id/members/',
  summary: 'Miembros e invitaciones pendientes, con los lugares usados del plan.',
  response: MembersResponse,
  workspaceScoped: false,
  errors: [
    { status: 403, when: 'Solo Dueño y Admin ven la sección (para Editor y Lector no aparece). Solo `detail`, sin `code`.' },
    { status: 404, when: 'El workspace no existe o el usuario no es dueño ni miembro activo (una invitación pendiente no alcanza). Mismo `detail` para todos los casos.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceMembersView.get (MembersSerializer)',
    note: 'Una fila por WorkspaceMember, de la más antigua a la más nueva; la del Dueño sale con `role: "owner"`. `seats_used` cuenta todas las filas, también las pendientes y la del Dueño; `max_members` es 1 si el workspace no tiene plan. Hoy `user` siempre viene (no hay invitaciones a emails sin cuenta) e `invited_by` es siempre null. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
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
    { status: 400, when: 'No se puede cambiar el rol del Dueño (solo deja de serlo al transferir), o `role` no es admin, editor ni viewer: `{role: [mensaje]}`.' },
    { status: 403, when: 'El rol no administra miembros. Solo `detail`, sin `code`.' },
    { status: 404, when: 'El workspace no existe para este usuario, o `memberId` no es una fila de ese workspace.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceMemberView.patch',
    note: 'También cambia el rol de una invitación pendiente. Un Admin puede cambiar el rol de otro Admin y el suyo. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
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
    { status: 400, when: 'No se puede quitar al Dueño, lo pida un Admin o el propio Dueño (primero transfiere el workspace): `{detail}`.' },
    { status: 403, when: 'El rol no administra miembros: un Editor o un Lector tampoco puede quitarse a sí mismo. Solo `detail`, sin `code`.' },
    { status: 404, when: 'El workspace no existe para este usuario, o `memberId` no es una fila de ese workspace.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceMemberView.delete',
    note: 'Borra la fila y libera su lugar del plan; quien fue quitado pasa a recibir 404 en ese workspace. Un Admin puede quitarse a sí mismo. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
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
    { status: 403, when: 'Sin lugares en el plan (`code: "seats_exceeded"`: límite del plan, se resuelve subiendo de plan o quitando miembros; el mismo código en `billing.changePlan` es un 409 porque ahí lo que sobra son miembros) o el rol no puede invitar.' },
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
