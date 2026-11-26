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
    { status: 429, when: 'Demasiados intentos seguidos (throttle de DRF). La UI pide esperar y no borra lo escrito.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views.py · LoginView (TokenObtainPairView de SimpleJWT, USERNAME_FIELD = email)',
    note: 'El email entra con cualquier escritura de su dominio (mayúsculas, letras de ancho completo, Unicode o punycode); un dominio que no se puede codificar responde 401 como cualquier email sin cuenta. El 429 sale de 10 pedidos por minuto por IP, o de los intentos fallidos de la cuenta: 10 cada 15 minutos desde la misma IP y 100 por hora en total. Un login correcto los reinicia. `detail` dice en castellano cuánto esperar. Hoy no distingue la cuenta sin verificar (falta el 403 propuesto, hueco 1).',
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
  errors: [
    { status: 401, when: 'Refresh vencido, inválido o revocado (rotado, o la contraseña cambió). La UI abre el diálogo D9 (sesión vencida).' },
    { status: 429, when: 'Más de 30 renovaciones por minuto desde la misma IP.' },
  ],
  backend: { status: 'exists', source: 'back/apps/users/views.py · TokenRefreshThrottledView (TokenRefreshView); SIMPLE_JWT en backend/settings/base.py (acceso 25 min)' },
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
  errors: [
    {
      status: 400,
      when: 'Validación por campo (contraseña débil; email ya usado con `code: "unique"`, que la UI muestra con un link a Iniciar sesión). Se muestra debajo de cada campo.',
    },
  ],
  backend: {
    status: 'proposed',
    gap: 1,
    note: 'Decir "email ya usado" permite averiguar qué emails tienen cuenta. El mapa (A2) lo pide así; para no ayudar a enumerar, el endpoint necesita un límite de intentos por IP. Recuperar contraseña y reenviar la verificación no lo revelan.',
  },
  screens: ['A2', 'A5'],
});

export const verifyEmail = defineEndpoint({
  id: 'auth.verifyEmail',
  method: 'POST',
  path: '/api/v1/auth/verify-email/',
  summary:
    'Verifica el email con el token del link. El de una cuenta nueva deja la sesión iniciada; el de un cambio de email (I1) pide la sesión de esa cuenta. Nunca cambia la sesión por la de otra cuenta.',
  request: z.object({ token: z.string().min(1) }),
  response: TokenPair,
  // The session goes with the link when there is one: the backend compares its account with the link's.
  auth: 'optional',
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'Token inválido, vencido, ya usado o cancelado (error en `token`). La UI ofrece reenviar el email.' },
    {
      status: 403,
      when: '`code: "other_account"`: hay una sesión de otra cuenta. `code: "login_required"`: el link de un cambio de email necesita la sesión de su cuenta. No se aplica nada; la UI muestra `detail` y, en el segundo caso, lleva a iniciar sesión y de vuelta al mismo link.',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views.py · VerifyEmailView; el token: apps/users/email_verification.py',
    note: 'El token dice para qué es (cambio de email o registro). Hoy solo el cambio de email manda el link: el registro todavía no existe (hueco 1). El link de un cambio pasa `pending_email` a `email`; deja de servir si el cambio se cancela.',
  },
  screens: ['A3'],
});

export const resendVerification = defineEndpoint({
  id: 'auth.resendVerification',
  method: 'POST',
  path: '/api/v1/auth/verify-email/resend/',
  summary: 'Reenvía el email de verificación (una vez cada 60 s). Responde 204 exista o no el email y esté o no verificado, como recuperar contraseña.',
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
  backend: {
    status: 'proposed',
    gap: 1,
    note: 'El link del email (N2) tiene que ser `/recuperar?token=<token>&email=<email>`: con el email la pantalla dice de quién es la cuenta e inicia sesión después de guardar ("Guardar y entrar").',
  },
  screens: ['A4', 'D9'],
});

export const confirmPasswordReset = defineEndpoint({
  id: 'auth.confirmPasswordReset',
  method: 'POST',
  path: '/api/v1/auth/password-reset/confirm/',
  summary: 'Define la contraseña nueva con el token del link. Responde 204 sin tokens: la UI inicia sesión con el email del link y la contraseña nueva.',
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
  backend: { status: 'proposed', gap: 2, note: 'La app token_blacklist de SimpleJWT ya está instalada (la usa el cambio de contraseña).' },
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
