import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { Decimal, Id, IsoDateTime, NoContent, Role } from './common';

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
    /** Changing it asks to verify again: the new one stays in `pending_email`. */
    email: z.string().email(),
    /** Proposed: required whenever `email` changes, to confirm it is the owner of the account who changes it (I1). */
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
  backend: { status: 'proposed', gap: 2, note: 'Los campos salen de User, Workspace, WorkspaceMember y Wallet (apps/users/models.py).' },
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
  errors: [{ status: 400, when: 'Validación por campo: email ya usado, o `email` nuevo sin `current_password` o con una incorrecta (error en `current_password`).' }],
  backend: { status: 'proposed', gap: 2 },
  screens: ['B1', 'B2', 'J3', 'I1'],
});

export const changePassword = defineEndpoint({
  id: 'users.changePassword',
  method: 'POST',
  path: '/api/v1/me/password/',
  summary: 'Cambia la contraseña del usuario.',
  request: z.object({ current_password: z.string().min(1), new_password: z.string().min(8) }),
  response: NoContent,
  successStatus: 204,
  workspaceScoped: false,
  errors: [{ status: 400, when: 'Contraseña actual incorrecta (`current_password`) o nueva débil (`new_password`).' }],
  backend: { status: 'proposed', gap: 2 },
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
  backend: { status: 'proposed', gap: 16 },
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
  backend: { status: 'proposed', gap: 16 },
  screens: ['I2'],
});

export const usersEndpoints = [getMe, updateMe, changePassword, getNotificationPreferences, updateNotificationPreferences] as const;
