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
   * Null in every pending invitation, whether its email has an account or not: inviting an
   * address never says whose account it is. It arrives once the invitation is accepted.
   */
  user: UserRef.nullable(),
  email: z.string().email(),
  /** `owner` for Workspace.owner; otherwise WorkspaceMember.role. */
  role: Role,
  status: MemberStatus,
  joined_at: IsoDateTime,
  /** Who sent the invitation (`{id, name}`, without the email). Null for the rows nobody invited. */
  invited_by: UserRef.nullable().optional(),
});
export type Member = z.infer<typeof Member>;

/**
 * Why `max_members` is below the current plan's: a scheduled downgrade (`pending_plan`, with the date it
 * applies) or a plan payment waiting for its checkout (`pending_payment`, applied at once when confirmed).
 * `plan_name` is the plan whose seats count.
 */
export const SeatLimit = z.union([
  z.object({ reason: z.literal('pending_plan'), plan_name: z.string(), effective_at: IsoDateTime }),
  z.object({ reason: z.literal('pending_payment'), plan_name: z.string(), effective_at: z.null() }),
]);
export type SeatLimit = z.infer<typeof SeatLimit>;

export const MembersResponse = z.object({
  members: z.array(Member),
  /**
   * The seats inviting checks (back/apps/users/seats.py · seat_limit): SubscriptionPlan.max_members, or
   * fewer while a plan change waits (`limited_by`). A pending invitation takes a seat (map 9.1.8).
   */
  max_members: z.number().int().positive(),
  seats_used: z.number().int().nonnegative(),
  /** Null when `max_members` is the current plan's. */
  limited_by: SeatLimit.nullable(),
});
export type MembersResponse = z.infer<typeof MembersResponse>;

export const InviteRequest = z.object({
  /** One or several (the backend takes up to 10 per request); the UI splits the comma-separated field. */
  emails: z.array(z.string().email()).min(1),
  role: MemberRole,
});
export type InviteRequest = z.infer<typeof InviteRequest>;

/**
 * Always `pending`: a link that expired, was used or was cancelled answers the same 404 as one
 * that never existed, so the answer says nothing about a token to whoever does not hold a live one.
 */
export const InvitationStatus = z.literal('pending');

export const InvitationPreview = z.object({
  workspace_id: Id,
  workspace_name: z.string(),
  /** The inviter's name, never their email: "Alguien del equipo" when the account has no name or is gone. */
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
      when: 'No se borra nada. Cuerpo `{error, code}`, y el `error` se muestra tal cual. Se revisan en este orden. `last_workspace`: es el único workspace del usuario (va primero porque esperar no lo resuelve). `project_processing`: hay un proyecto subido, en ingesta o renderizando (uno trabado se borra primero). `posts_in_flight`: hay una publicación saliendo (`PROCESSING` hace menos de 10 minutos) o en cola (`QUEUED` hasta 15 minutos después de su `publish_at`); una más vieja está trabada y no frena el borrado. `reservation_open`: hay tokens reservados sin liquidar. `payment_pending`: hay una compra de tokens o un pago de plan pendiente (con el checkout abierto o abriéndose); se resuelve cuando el proveedor lo liquida o cuando vence su checkout (`PAYMENTS_CHECKOUT_TTL_SECONDS`, 24 h por defecto). Una compra de tokens no se puede cancelar desde la web; un pago de plan sí, con «Seguir con …» en Planes (`billing.changePlan` con el plan actual). El `error` es uno solo para los dos y dice pagarlo o esperar, con el plazo del setting. `balance_left`: queda saldo disponible (trae `available_balance`); los tokens nunca se borran.',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_workspaces.py · WorkspaceDetailView.delete',
    note: 'Borra para siempre el workspace con sus proyectos, clips y publicaciones (las programadas que no salieron no se publican), brand kits, miembros e invitaciones, sus pagos ya liquidados, y la billetera, ya en cero, con sus movimientos. Las cuentas de los miembros no se tocan. No limpia los archivos en R2 ni el perfil de Ayrshare. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
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
    note: 'Una fila por WorkspaceMember, de la más antigua a la más nueva; la del Dueño sale con `role: "owner"`. `seats_used` cuenta todas las filas, también las pendientes y la del Dueño. `max_members` es el límite que controla invitar (`seats.seat_limit`): el del plan, 1 si el workspace no tiene plan, o el del plan más chico mientras espera una bajada programada o un pago de plan; en esos dos casos `limited_by` dice cuál (`pending_plan` con su fecha, o `pending_payment`), y si no es null. En una invitación pendiente `user` es null (tenga o no cuenta ese email) y `email` es el invitado; `invited_by` es `{id, name}` de quien invitó, o null en las filas que nadie invitó. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
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
    note: 'Borra la fila y libera su lugar del plan; quien fue quitado pasa a recibir 404 en ese workspace. Quitar una invitación pendiente mata su link. Un Admin puede quitarse a sí mismo. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
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
    {
      status: 400,
      when: '`{emails: [oración]}`, siempre una lista plana con una sola oración: falta, alguno no es un email, son más de 10, o alguno ya es miembro o tiene una invitación pendiente (con cualquier mayúscula). `{role: [mensaje]}`: no es admin, editor ni viewer (nadie invita a un Dueño). No se invita a ninguno.',
    },
    {
      status: 403,
      when: 'Sin lugares en el plan (`{error, code: "seats_exceeded"}`: límite del plan, se resuelve subiendo de plan o quitando miembros; el mismo código en `billing.changePlan` es un 409 porque ahí lo que sobra son miembros). El `error` se muestra tal cual: nombra el plan, o el plan más chico de una bajada programada o de un pago en curso. O el rol no puede invitar (Editor, Lector): solo `detail`, sin `code`.',
    },
    { status: 404, when: 'El workspace no existe o el usuario no es dueño ni miembro activo. Mismo `detail` para todos los casos.' },
    { status: 429, when: 'Demasiadas invitaciones seguidas. Invitar y reenviar cuentan juntos: 30 por hora por usuario y 60 por hora por workspace. `detail` dice cuánto esperar.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_invitations.py · WorkspaceInvitationsView.post (InviteSerializer)',
    note: 'Todos o ninguno. Cada email es una fila `pending` que ocupa un lugar del plan desde ese momento. Los emails vuelven normalizados (el dominio en minúsculas) y sin repetidos. La respuesta nunca dice si un email tiene cuenta: `user` es null. El email lleva un link a `/invitacion#token=…` (el token en el fragmento) que vence a los 7 días (`INVITATION_MAX_AGE_SECONDS`) y sirve una vez; no nombra al workspace ni a quien invita (eso lo muestra A5). Si la cola de emails está caída la invitación queda creada igual, y se manda con «Reenviar». El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
  screens: ['D7', 'H2'],
});

export const resendInvitation = defineEndpoint({
  id: 'workspaces.resendInvitation',
  method: 'POST',
  path: '/api/v1/workspaces/:id/invitations/:memberId/resend/',
  summary: 'Reenvía el email de una invitación pendiente, con un link nuevo.',
  response: NoContent,
  successStatus: 204,
  workspaceScoped: false,
  errors: [
    { status: 403, when: 'El rol no administra miembros. Solo `detail`, sin `code`.' },
    { status: 404, when: 'La invitación ya se aceptó o se canceló, `memberId` no es de ese workspace, o el workspace no existe para este usuario.' },
    { status: 429, when: 'Demasiadas invitaciones seguidas (el mismo límite que invitar), o demasiados reenvíos a esa dirección: 3 por hora, los pida quien los pida. `detail` dice cuánto esperar. El link anterior sigue sirviendo.' },
    { status: 502, when: 'No se pudo encolar el email. El link anterior ya no sirve: se reintenta en unos minutos.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_invitations.py · WorkspaceInvitationResendView.post',
    note: 'El link anterior deja de servir en el momento, y el nuevo empieza su propio vencimiento. El rol se mira en el workspace de la ruta, nunca en el de `X-Workspace-Id`.',
  },
  screens: ['H2'],
});

/** The token of the link travels in the body of a POST: a path or a query would leave it in access logs. */
export const InvitationToken = z.object({ token: z.string().min(1) });

export const getInvitation = defineEndpoint({
  id: 'workspaces.invitation',
  method: 'POST',
  path: '/api/v1/invitations/preview/',
  summary: 'Datos públicos de una invitación: quién invita, a qué workspace y con qué rol. Es una lectura: el POST solo lleva el token en el cuerpo.',
  request: InvitationToken,
  response: InvitationPreview,
  auth: false,
  workspaceScoped: false,
  errors: [
    { status: 404, when: 'El token no abre una invitación pendiente: falta, no existe, venció, ya se usó o se canceló. La misma respuesta para todos. La UI muestra "Esta invitación no existe o ya no sirve".' },
    { status: 429, when: 'Demasiados intentos desde la misma IP (60 por hora). `detail` dice cuánto esperar.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_invitations.py · InvitationView.post (InvitationPreviewSerializer)',
    note: 'El token va en el cuerpo y nunca en la ruta: el logger de pedidos de Django y el log de acceso del servidor web escriben las rutas, y un token ahí sería un link que sirve a la vista de quien lee logs. El link del email es `/invitacion#token=…` (en el fragmento, que el navegador no manda). No lee la sesión ni cambia nada. `status` es siempre `pending`. `inviter_name` es el nombre de quien invitó, nunca su email. `has_account` dice si el email invitado ya tiene cuenta: solo lo ve quien tiene el link.',
  },
  screens: ['A5'],
});

export const acceptInvitation = defineEndpoint({
  id: 'workspaces.acceptInvitation',
  method: 'POST',
  path: '/api/v1/invitations/accept/',
  summary: 'Acepta la invitación del `token`. Sin cuenta: la crea con `name` y `password`. Con cuenta: basta con la sesión iniciada.',
  request: InvitationToken.extend({
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
    { status: 400, when: 'Sin sesión, para crear la cuenta: falta `name` o `password`, la contraseña no cumple las reglas del registro (`{password: [oraciones]}`), o `terms_accepted` no es `true`. No se crea nada y el link sigue sirviendo.' },
    { status: 401, when: 'El email ya tiene cuenta y no hay sesión (`detail`): la UI pide iniciar sesión. Con sesión, el cliente manda el token (y lo renueva si hace falta); un token que no sirve también es 401.' },
    { status: 403, when: '`{error, code: "invitation_email_mismatch"}`: la sesión es de una cuenta con otro email; la invitación solo la acepta la cuenta del email invitado. `{error, code: "seats_exceeded"}`: el plan se achicó después de invitar y ya no queda lugar.' },
    { status: 410, when: '`{error, code: "invitation_invalid"}`: el token falta, no existe, venció, ya se usó o la invitación se canceló. La misma respuesta para todos.' },
    { status: 429, when: 'Demasiados intentos desde la misma IP (20 por hora), con sesión o sin ella. `detail` dice cuánto esperar.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_invitations.py · InvitationAcceptView.post (AcceptInvitationSerializer)',
    note: 'El token va en el cuerpo, nunca en la ruta (ver `workspaces.invitation`). Con sesión solo se lee `token`, y la cuenta queda con el email verificado. Sin sesión crea la cuenta del email invitado ya verificada (el link llegó a esa casilla), con su workspace propio como en el registro, y devuelve `tokens`. La cuenta, su workspace y la membresía se guardan juntos o no se guarda nada. El link sirve una sola vez.',
  },
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
