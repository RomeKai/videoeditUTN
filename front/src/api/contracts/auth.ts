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
  /** Kept by this browser and sent with the link of the email: only here does the link work without a session. */
  signup_nonce: z.string().min(1),
});

/** A link of a new account opened outside the browser that signed up: nothing changed; sign in and open it again. */
export const SignedOut = z.object({ signed_in: z.literal(false) });

export const VerifyEmailRequest = z.object({ token: z.string().min(1), signup_nonce: z.string().min(1).optional() });
export type VerifyEmailRequest = z.infer<typeof VerifyEmailRequest>;

export const VerifyEmailResult = z.union([TokenPair, SignedOut]);
export type VerifyEmailResult = z.infer<typeof VerifyEmailResult>;

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
    note: 'El email entra con cualquier escritura de su dominio (mayúsculas, letras de ancho completo, Unicode o punycode); un dominio que no se puede codificar responde 401 como cualquier email sin cuenta. Sin Redis los límites siguen, contados por proceso. El 429 sale de 10 pedidos por minuto por IP, o de los intentos fallidos de la cuenta: 10 cada 15 minutos desde la misma IP y 100 por hora en total. Un login correcto los reinicia. `detail` dice en castellano cuánto esperar. Hoy no distingue la cuenta sin verificar (falta el 403 propuesto, hueco 1).',
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
    {
      status: 401,
      when: 'Refresh vencido, inválido o revocado (rotado, la contraseña cambió, o se cerró la sesión en todos los dispositivos: `code: "session_revoked"`). La UI abre el diálogo D9 (sesión vencida).',
    },
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
      when: 'Validación por campo, en castellano. Contraseña que no cumple las reglas (`password`, una frase por regla). `terms_accepted` falta o no es `true`. Dominio que no se puede codificar, o dirección que el servidor de email rechaza (en `email`). Email ya usado (con cualquier escritura de su dominio): `email: [{ message, code: "unique" }]`, que la UI muestra con un link a Iniciar sesión; llega solo cuando todo lo demás está bien, así un registro que no crearía nada no dice qué emails tienen cuenta. Se muestra debajo de cada campo.',
    },
    { status: 429, when: 'Más de 20 registros por hora desde la misma IP (`THROTTLE_REGISTER`). `detail` dice en castellano cuánto esperar.' },
    { status: 502, when: 'El servidor de email no aceptó el email de verificación. No se crea nada: se puede probar de nuevo.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_auth.py · RegisterView (RegisterSerializer); el link: apps/users/email_verification.py · send_signup_verification',
    note: 'Crea la cuenta sin verificar, su workspace con el plan por defecto (la señal de User con `create_owned_workspace`: billetera y el usuario como Admin activo; se llama "Espacio de <nombre>") y manda el link de A3 (`/verificar#token=…`, vence en 24 h; el token va en el fragmento, como el de recuperar y el de la invitación: el navegador no lo manda y no queda en el log de accesos del host web. La web lo lee, lo saca de la barra de direcciones y lo manda en el cuerpo de `auth.verifyEmail`. Un link ya enviado con `?token=` sigue sirviendo hasta vencer: la web lee los dos y prefiere el fragmento). El email se guarda normalizado (dominio en punycode y minúsculas): la respuesta trae el guardado. `signup_nonce` es de este navegador: la web lo guarda y lo manda con el link (`auth.verifyEmail`); el backend guarda solo su hash. Decir "email ya usado" permite averiguar qué emails tienen cuenta: el mapa (A2) lo pide así, y por eso el límite por IP. «Reenviar» de A3 espera 60 s desde el registro. Hasta verificar, el login igual entra (falta el 403 propuesto de `auth.login`).',
  },
  screens: ['A2', 'A5'],
});

export const verifyEmail = defineEndpoint({
  id: 'auth.verifyEmail',
  method: 'POST',
  path: '/api/v1/auth/verify-email/',
  summary:
    'Verifica el email con el token del link. El de una cuenta nueva verifica e inicia la sesión en el navegador que se registró (`signup_nonce`) o con la sesión de esa cuenta; en otro no cambia nada. El de un cambio de email (I1) pide la sesión de esa cuenta. Nunca cambia la sesión por la de otra cuenta.',
  request: VerifyEmailRequest,
  response: VerifyEmailResult,
  // The session goes with the link when there is one: the backend compares its account with the link's.
  auth: 'optional',
  workspaceScoped: false,
  errors: [
    { status: 400, when: 'Token inválido, vencido, ya usado o cancelado, o un email pendiente con un dominio que no se puede codificar (error en `token`). La UI ofrece reenviar el email.' },
    {
      status: 403,
      when: '`code: "other_account"`: hay una sesión de otra cuenta. `code: "login_required"`: el link de un cambio de email necesita la sesión de su cuenta. No se aplica nada; la UI muestra `detail` y, en el segundo caso, lleva a iniciar sesión y de vuelta al mismo link.',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views.py · VerifyEmailView; el token: apps/users/email_verification.py',
    note: 'El token dice para qué es (cambio de email o registro). El de registro lo mandan `auth.register` y `auth.resendVerification`. Con el `signup_nonce` del registro (vale 24 h desde el registro) o con la sesión de esa cuenta, verifica, descarta un `pending_email` pedido antes y devuelve el par JWT. Si no, responde `{ signed_in: false }`, no cambia nada y el link sigue sirviendo: la UI guarda el link en `sessionStorage` (solo esa pestaña) y lleva a `/login?motivo=verificar&siguiente=/verificar` ("Iniciá sesión para terminar de verificar tu email"): el token nunca viaja en la query. Con la sesión, el mismo link verifica. Así, quien se registró con un email ajeno no consigue una cuenta verificada (`may_simulate` de los pagos simulados confía en `is_email_verified`) ni le inicia la sesión a su dueño: el dueño, que no sabe esa contraseña, entra recuperándola, lo que verifica el email y cierra las sesiones del ocupante. Por eso el login tiene que seguir dejando entrar a una cuenta sin verificar: el 403 propuesto de `auth.login` rompería este camino. El link de un cambio pasa `pending_email` a `email` y devuelve el par; deja de servir si el cambio se cancela. Los dos sirven una vez y vencen en 24 h.',
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
  errors: [
    { status: 400, when: 'El email falta o no tiene forma de email (la UI lo revisa antes).' },
    {
      status: 429,
      when: 'Se pidió hace menos de 60 s para ese email (o se registró hace menos de 60 s), exista o no la cuenta; o más de 20 emails por hora desde la misma IP, contando los de recuperar contraseña. `detail`: "Demasiados intentos. Probá de nuevo en N segundos." La UI deja el botón deshabilitado con la cuenta regresiva.',
    },
    { status: 502, when: 'La cola de emails no responde. Igual para cualquier email: no dice si tiene cuenta.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_auth.py · ResendVerificationView; el envío: apps/users/tasks.py · send_account_email',
    note: 'Solo manda el email a una cuenta activa sin verificar. Lo manda el worker de Celery: puede tardar si la cola está ocupada. El pedido hace lo mismo con cuenta o sin ella (una consulta y una tarea), así la respuesta y su tiempo no dicen nada.',
  },
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
  errors: [
    { status: 400, when: 'El email falta o no tiene forma de email (la UI lo revisa antes).' },
    {
      status: 429,
      when: 'Se pidió hace menos de 60 s para ese email, exista o no la cuenta; o más de 20 emails por hora desde la misma IP, contando los de reenviar la verificación. La UI lo toma como enviado y muestra la espera.',
    },
    { status: 502, when: 'La cola de emails no responde. Igual para cualquier email.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_auth.py · PasswordResetView; el link: apps/users/email_verification.py · send_password_reset',
    note: 'El link del email (N2) es `/recuperar#token=<token>&email=<email>`, en el fragmento: el navegador no lo manda, así no queda en los logs del host de la web. La pantalla lo lee de `location.hash` y lo saca de la URL. Con el email de la cuenta dice de quién es e inicia sesión después de guardar ("Guardar y entrar"). Vence en 24 h (`PASSWORD_RESET_TIMEOUT`), sirve una vez y deja de servir con cualquier cambio de contraseña. Una cuenta sin contraseña usable (login social) también lo recibe: así crea una. Lo manda el worker; el pedido hace lo mismo con cuenta o sin ella.',
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
  errors: [
    {
      status: 400,
      when: 'Token inválido, vencido o ya usado (error en `token`: "El link no es válido o ya venció."), o contraseña que no cumple las reglas (error en `password`). El token se revisa primero.',
    },
    { status: 429, when: 'Más de 20 intentos por hora desde la misma IP.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_auth.py · PasswordResetConfirmView (PasswordResetTokenGenerator de Django)',
    note: 'Como `POST /me/password/`, cierra todas las sesiones de la cuenta (los tokens de la contraseña vieja dan 401). El link llegó al email de la cuenta, así que también la deja verificada y descarta lo que dejó quien tenía la contraseña antes: un `pending_email` y el `signup_nonce` del registro.',
  },
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
  errors: [
    { status: 400, when: '`refresh` inválido, vencido, de otro tipo o de otra cuenta que la de la sesión. No se revoca nada.' },
    { status: 429, when: 'Más de 60 por hora para el mismo usuario.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/users/views_auth.py · LogoutView; apps/users/tokens.py · revoke_sessions',
    note: 'Pide la sesión (Bearer) y el refresh tiene que ser de esa misma cuenta. Un refresh ya revocado responde 204: si el token de acceso había vencido, el cliente renovó el par para mandar el pedido, y `useLogout` cierra después también el refresh nuevo. Con `all_devices` se revocan todos los refresh y también los tokens de acceso ya emitidos, en el momento: en otro dispositivo el próximo pedido da 401 `code: "session_revoked"`.',
  },
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
