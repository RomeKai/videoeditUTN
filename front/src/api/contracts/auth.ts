import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { NoContent } from './common';

export const TokenPair = z.object({ access: z.string().min(1), refresh: z.string().min(1) });
export type TokenPair = z.infer<typeof TokenPair>;

export const LoginRequest = z.object({ email: z.string().email(), password: z.string().min(1) });
export type LoginRequest = z.infer<typeof LoginRequest>;

export const RegisterRequest = z.object({
  name: z.string().min(1).max(150),
  email: z.string().email(),
  password: z.string().min(8),
  /** Must be true: the backend stamps `User.terms_accepted_at`. */
  terms_accepted: z.literal(true),
});
export type RegisterRequest = z.infer<typeof RegisterRequest>;

export const RegisterResponse = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
  is_email_verified: z.boolean(),
});

export const login = defineEndpoint({
  id: 'auth.login',
  method: 'POST',
  path: '/api/token/',
  summary: 'Inicia sesión con email y contraseña; devuelve el par JWT.',
  request: LoginRequest,
  response: TokenPair,
  auth: false,
  workspaceScoped: false,
  errors: [
    { status: 401, when: 'Credenciales incorrectas. La UI muestra "El email o la contraseña no coinciden", sin decir cuál.' },
    { status: 403, when: 'Propuesto: `code: "email_not_verified"` cuando la cuenta no verificó el email. La UI lleva a A3.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/backend/urls.py · rest_framework_simplejwt TokenObtainPairView (USERNAME_FIELD = email)',
    note: 'Hoy no distingue la cuenta sin verificar (falta el 403 propuesto, hueco 1).',
  },
  screens: ['A1', 'D9'],
});

export const refreshToken = defineEndpoint({
  id: 'auth.refresh',
  method: 'POST',
  path: '/api/token/refresh/',
  summary: 'Renueva el token de acceso. El backend rota el refresh (ROTATE_REFRESH_TOKENS).',
  request: z.object({ refresh: z.string().min(1) }),
  response: z.object({ access: z.string().min(1), refresh: z.string().min(1).optional() }),
  auth: false,
  workspaceScoped: false,
  errors: [{ status: 401, when: 'Refresh vencido o inválido. La UI abre el diálogo D9 (sesión vencida).' }],
  backend: { status: 'exists', source: 'back/backend/urls.py · TokenRefreshView; SIMPLE_JWT en backend/settings/base.py (acceso 25 min)' },
  screens: ['A1', 'D9'],
});

export const register = defineEndpoint({
  id: 'auth.register',
  method: 'POST',
  path: '/api/v1/auth/register/',
  summary: 'Crea la cuenta y manda el email de verificación.',
  request: RegisterRequest,
  response: RegisterResponse,
  successStatus: 201,
  auth: false,
  workspaceScoped: false,
  errors: [{ status: 400, when: 'Validación por campo (email ya usado, contraseña débil). Se muestra debajo de cada campo.' }],
  backend: { status: 'proposed', gap: 1 },
  screens: ['A2', 'A5'],
});

export const verifyEmail = defineEndpoint({
  id: 'auth.verifyEmail',
  method: 'POST',
  path: '/api/v1/auth/verify-email/',
  summary: 'Verifica el email con el token del link y deja la sesión iniciada.',
  request: z.object({ token: z.string().min(1) }),
  response: TokenPair,
  auth: false,
  workspaceScoped: false,
  errors: [{ status: 400, when: 'Token inválido o vencido. La UI ofrece reenviar el email.' }],
  backend: { status: 'proposed', gap: 1 },
  screens: ['A3'],
});

export const resendVerification = defineEndpoint({
  id: 'auth.resendVerification',
  method: 'POST',
  path: '/api/v1/auth/verify-email/resend/',
  summary: 'Reenvía el email de verificación (una vez cada 60 s).',
  request: z.object({ email: z.string().email() }),
  response: NoContent,
  successStatus: 204,
  auth: false,
  workspaceScoped: false,
  errors: [{ status: 429, when: 'Se pidió hace menos de 60 s. La UI deja el botón deshabilitado con la cuenta regresiva.' }],
  backend: { status: 'proposed', gap: 1 },
  screens: ['A3'],
});

export const requestPasswordReset = defineEndpoint({
  id: 'auth.requestPasswordReset',
  method: 'POST',
  path: '/api/v1/auth/password-reset/',
  summary: 'Pide el link para recuperar la contraseña. Siempre responde 204, exista o no el email.',
  request: z.object({ email: z.string().email() }),
  response: NoContent,
  successStatus: 204,
  auth: false,
  workspaceScoped: false,
  errors: [],
  backend: { status: 'proposed', gap: 1 },
  screens: ['A4', 'D9'],
});

export const confirmPasswordReset = defineEndpoint({
  id: 'auth.confirmPasswordReset',
  method: 'POST',
  path: '/api/v1/auth/password-reset/confirm/',
  summary: 'Define la contraseña nueva con el token del link.',
  request: z.object({ token: z.string().min(1), password: z.string().min(8) }),
  response: NoContent,
  successStatus: 204,
  auth: false,
  workspaceScoped: false,
  errors: [{ status: 400, when: 'Token inválido o vencido, o contraseña débil (error en `password`).' }],
  backend: { status: 'proposed', gap: 1 },
  screens: ['A4'],
});

export const logout = defineEndpoint({
  id: 'auth.logout',
  method: 'POST',
  path: '/api/v1/auth/logout/',
  summary: 'Cierra la sesión: invalida el refresh. Con `all_devices`, todos los del usuario.',
  request: z.object({ refresh: z.string().min(1), all_devices: z.boolean().optional() }),
  response: NoContent,
  successStatus: 204,
  workspaceScoped: false,
  errors: [],
  backend: { status: 'proposed', gap: 2, note: 'Necesita la app token_blacklist de SimpleJWT.' },
  screens: ['I1', 'Barra'],
});

export const authEndpoints = [
  login,
  refreshToken,
  register,
  verifyEmail,
  resendVerification,
  requestPasswordReset,
  confirmPasswordReset,
  logout,
] as const;
