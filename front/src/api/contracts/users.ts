import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { TokenPair } from './auth';
import { Decimal, Id, IsoDateTime, Role } from './common';

/** A workspace as it appears in `/me`: enough for the switcher (J3) and the topbar. */
export const WorkspaceSummary = z.object({
  id: Id,
  name: z.string(),
  /** Role of the current user in it. */
  role: Role,
  plan: z.object({ id: Id, name: z.string() }).nullable(),
  available_balance: Decimal,
});
export type WorkspaceSummary = z.infer<typeof WorkspaceSummary>;

export const OnboardingUseCase = z.enum(['podcast', 'gaming', 'education', 'other']);
export type OnboardingUseCase = z.infer<typeof OnboardingUseCase>;

export const Me = z.object({
  id: Id,
  email: z.string().email(),
  /** Display name. Maps to `User.first_name` + `User.last_name`. */
  name: z.string(),
  is_email_verified: z.boolean(),
  tour_completed: z.boolean(),
  terms_accepted_at: IsoDateTime.nullable(),
  /** Email waiting for verification after a change (I1). */
  pending_email: z.string().email().nullable().optional(),
  onboarding_use_case: OnboardingUseCase.nullable().optional(),
  /** First-steps checklist of B2: what the user already did. */
  onboarding: z
    .object({
      has_project: z.boolean(),
      has_brand_kit: z.boolean(),
      has_social_account: z.boolean(),
      has_invited_member: z.boolean(),
    })
    .optional(),
  active_workspace_id: Id.nullable(),
  workspaces: z.array(WorkspaceSummary),
});
export type Me = z.infer<typeof Me>;

export const UpdateMeRequest = z
  .object({
    name: z.string().min(1).max(150),
    /** Changing it asks to verify again: the new one stays in `pending_email`. The current email, or null, cancels a pending change (and its link). */
    email: z.string().email().nullable(),
    /** Required whenever `email` changes, to confirm it is the owner of the account who changes it (I1). */
    current_password: z.string().min(1),
    tour_completed: z.boolean(),
    onboarding_use_case: OnboardingUseCase,
    active_workspace_id: Id,
  })
  .partial();
export type UpdateMeRequest = z.infer<typeof UpdateMeRequest>;

/** One email switch per notification type of map 1.11. */
export const NotificationPreferences = z.object({
  project_ready: z.boolean(),
  render_finished: z.boolean(),
  project_failed: z.boolean(),
  post_failed: z.boolean(),
  low_balance: z.boolean(),
});
export type NotificationPreferences = z.infer<typeof NotificationPreferences>;

export const getMe = defineEndpoint({
  id: 'users.me',
  method: 'GET',
  path: '/api/v1/me/',
  summary: 'Perfil del usuario con sus workspaces, el rol en cada uno y el workspace activo.',
  response: Me,
  workspaceScoped: false,
  errors: [{ status: 401, when: 'Sin sesión: el cliente renueva el token o abre D9.' }],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views.py · MeView.get (MeSerializer)',
    note: '`active_workspace_id` es el último workspace elegido (`User.last_workspace`) mientras el usuario siga siendo dueño o miembro activo; si no, el más antiguo propio. No lee `X-Workspace-Id`, así que un header viejo nunca da 404. `onboarding` sale del workspace activo; la red cuenta como conectada con el perfil de Ayrshare del workspace.',
  },
  screens: ['Barra', 'B1', 'B2', 'J3', 'I1'],
});

export const updateMe = defineEndpoint({
  id: 'users.updateMe',
  method: 'PATCH',
  path: '/api/v1/me/',
  summary: 'Actualiza nombre, email, tour, uso elegido en la bienvenida y workspace activo.',
  request: UpdateMeRequest,
  response: Me,
  workspaceScoped: false,
  errors: [
    {
      status: 400,
      when: 'Validación por campo: email ya usado, con un dominio que no se puede codificar ("El dominio del email no es válido. Revisá que esté bien escrito.") o rechazado por el servidor de email (error en `email`), o `email` nuevo sin `current_password`, con una incorrecta o en una cuenta sin contraseña (error en `current_password`); `active_workspace_id` de un workspace donde no es miembro activo.',
    },
    { status: 429, when: 'Demasiados cambios de email seguidos (5 por hora). `detail` dice en castellano cuánto esperar.' },
    { status: 502, when: 'El servidor de email no aceptó el email de verificación: no se guardó nada. No es 503, que la UI lee como mantenimiento.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views.py · MeView.patch (UpdateMeSerializer); el link: apps/users/email_verification.py',
    note: 'El backend normaliza el dominio (minúsculas, letras de ancho completo, Unicode a punycode): `ana@Mañana.com` vuelve en `pending_email` como `ana@xn--maana-pta.com`, y otra escritura de un email ya usado cuenta como usado. El email nuevo queda en `pending_email` y le llega un link a `/verificar?token=…&siguiente=/perfil` que vence en 24 h; `auth.verifyEmail` lo aplica.',
  },
  screens: ['B1', 'B2', 'J3', 'I1'],
});

export const changePassword = defineEndpoint({
  id: 'users.changePassword',
  method: 'POST',
  path: '/api/v1/me/password/',
  summary: 'Cambia la contraseña y cierra las demás sesiones. Responde un par JWT nuevo: el cliente lo guarda y sigue con sesión.',
  request: z.object({ current_password: z.string().min(1), new_password: z.string().min(8) }),
  response: TokenPair,
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'Contraseña actual incorrecta o cuenta sin contraseña (`current_password`), o nueva débil (`new_password`). Los mensajes llegan en castellano.' },
    { status: 429, when: 'Demasiados intentos seguidos (5 por hora). `detail` dice en castellano cuánto esperar.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views.py · PasswordChangeView (PasswordChangeSerializer)',
    note: 'Todos los tokens emitidos antes dejan de andar (CHECK_REVOKE_TOKEN y token_blacklist, apps/users/tokens.py).',
  },
  screens: ['I1'],
});

export const getNotificationPreferences = defineEndpoint({
  id: 'users.notificationPreferences',
  method: 'GET',
  path: '/api/v1/me/notification-preferences/',
  summary: 'Qué emails recibe el usuario, por tipo.',
  response: NotificationPreferences,
  workspaceScoped: false,
  errors: [],
  backend: { status: 'exists', source: 'back/apps/users/views.py · NotificationPreferencesView.get (NotificationPreferencesSerializer)' },
  screens: ['I2'],
});

export const updateNotificationPreferences = defineEndpoint({
  id: 'users.updateNotificationPreferences',
  method: 'PATCH',
  path: '/api/v1/me/notification-preferences/',
  summary: 'Prende o apaga los emails por tipo.',
  request: NotificationPreferences.partial(),
  response: NotificationPreferences,
  workspaceScoped: false,
  errors: [],
  backend: { status: 'exists', source: 'back/apps/users/views.py · NotificationPreferencesView.patch (NotificationPreferencesSerializer)' },
  screens: ['I2'],
});

export const usersEndpoints = [getMe, updateMe, changePassword, getNotificationPreferences, updateNotificationPreferences] as const;
