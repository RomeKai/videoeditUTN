import * as contract from '../../contracts/auth';
import { KIND, mid, newId } from '../db';
import { badRequest, MockHttpError, route } from '../http';
import { PLAN_IDS } from '../seed';
import { clearLoggedOut, markLoggedOut, mockTokens, userIdFromToken } from '../state';

/**
 * Mock rules of the session:
 * - any known email signs in with any password except the literal "incorrecta";
 * - unknown emails answer 401, like wrong credentials;
 * - scenario "email-sin-verificar" answers 403 `email_not_verified` (proposed: the backend lets an unverified account in,
 *   which A3 needs: a link opened without the nonce asks to sign in and open it again);
 * - the password "limite" answers 429 (too many attempts) and "caida" answers 500, to see A1's error states;
 * - tokens "invalido" / "vencido" fail in verify-email and password-reset/confirm;
 * - verify-email with "cambio-de-email" is the link of an email change (I1): it needs the session of its account;
 *   any other token verifies a new account, and answers 403 when the browser has another account's session. It
 *   works only with the `signup_nonce` of that sign-up (or the session of that account): else `{ signed_in: false }`
 *   and nothing changes;
 * - the sign-up says "ya existe" only when every other field is valid, like the backend;
 * - resend and password reset wait 60 s per email (any case), with or without an account, like the backend;
 * - logout with a refresh of another account answers 400 and closes nothing.
 */

/** The backend's 429 (apps.users.throttles.TooManyAttempts), read by `retryAfterSeconds`. */
function tooSoon(last: number, now: number): MockHttpError | null {
  const wait = 60_000 - (now - last);
  if (wait <= 0) return null;
  return new MockHttpError(429, { detail: `Demasiados intentos. Probá de nuevo en ${Math.ceil(wait / 1000)} segundos.` });
}
export const authHandlers = [
  route(contract.login, ({ db, body }) => {
    if (body.password === 'limite') throw new MockHttpError(429, { detail: 'Request was throttled. Expected available in 60 seconds.' });
    if (body.password === 'caida') throw new MockHttpError(500, { detail: 'Internal server error' });
    const user = db.users.find((item) => item.email.toLowerCase() === body.email.toLowerCase());
    if (!user || body.password === 'incorrecta') {
      throw new MockHttpError(401, { detail: 'No active account found with the given credentials' });
    }
    if (db.flags.unverifiedLogin) {
      throw new MockHttpError(403, { detail: 'La cuenta todavía no verificó su email.', code: 'email_not_verified' });
    }
    db.flags.sessionExpired = false;
    clearLoggedOut();
    return { data: mockTokens(user.id) };
  }),

  route(contract.refreshToken, ({ db, body }) => {
    const userId = userIdFromToken(body.refresh, 'refresh');
    if (db.flags.sessionExpired || !userId || !db.users.some((item) => item.id === userId)) {
      throw new MockHttpError(401, { detail: 'Token is invalid or expired', code: 'token_not_valid' });
    }
    return { data: mockTokens(userId) };
  }),

  route(contract.register, ({ db, body, now }) => {
    if (/^\d+$/.test(body.password)) throw badRequest({ password: ['La contraseña no puede ser solo números.'] });
    // Only once everything else is valid: a sign-up that would create nothing does not say which emails have an account.
    if (db.users.some((item) => item.email.toLowerCase() === body.email.toLowerCase())) {
      throw badRequest({ email: [{ message: 'Ya existe una cuenta con este email.', code: 'unique' }] });
    }
    const id = newId();
    const workspaceId = newId();
    db.users.push({
      id,
      name: body.name,
      email: body.email,
      is_email_verified: false,
      tour_completed: false,
      terms_accepted_at: new Date(now).toISOString(),
      pending_email: null,
      onboarding_use_case: null,
      active_workspace_id: workspaceId,
      notification_preferences: { project_ready: true, render_finished: true, project_failed: true, post_failed: true, low_balance: true },
    });
    // The backend names it after the first name, on the default plan, with an empty wallet.
    db.workspaces.push({ id: workspaceId, name: `Espacio de ${body.name.trim().split(' ')[0]}`, owner: id, subscription_plan: PLAN_IDS.free, created_at: new Date(now).toISOString(), renews_at: null, pending_plan: null });
    db.members.push({ id: newId(), workspace: workspaceId, user: id, email: body.email, role: 'admin', status: 'active', joined_at: new Date(now).toISOString(), invited_by: null, token: null });
    db.wallets.push({ id: newId(), workspace: workspaceId, available_balance: 0, reserved_balance: 0, updated_at: new Date(now).toISOString() });
    // The verification email was just sent: «Reenviar» waits its 60 s from here.
    db.resendAt[body.email.toLowerCase()] = now;
    const nonce = `nonce-${newId()}`;
    db.signupNonces = { ...db.signupNonces, [id]: nonce };
    return { data: { id, email: body.email, is_email_verified: false, signup_nonce: nonce } };
  }),

  route(contract.verifyEmail, ({ db, body, maybeUser }) => {
    if (body.token === 'invalido' || body.token === 'vencido') {
      throw badRequest({ token: ['El link no es válido o ya venció.'] });
    }
    if (body.token === 'cambio-de-email') {
      if (!maybeUser) {
        throw new MockHttpError(403, { detail: 'Para confirmar el email nuevo, iniciá sesión con tu cuenta y volvé a abrir el link.', code: 'login_required' });
      }
      if (!maybeUser.pending_email) throw badRequest({ token: ['El link no es válido o ya venció.'] });
      maybeUser.email = maybeUser.pending_email;
      maybeUser.pending_email = null;
      maybeUser.is_email_verified = true;
      return { data: mockTokens(maybeUser.id) };
    }
    // The link's token names the account; in the mocks it verifies the newest unverified one.
    const user = [...db.users].reverse().find((item) => !item.is_email_verified) ?? db.users.find((item) => item.id === mid(KIND.user, 5));
    if (!user) throw badRequest({ token: ['El link no es válido o ya venció.'] });
    if (maybeUser && maybeUser.id !== user.id) {
      throw new MockHttpError(403, { detail: 'Este link es de otra cuenta. Cerrá la sesión y volvé a abrirlo.', code: 'other_account' });
    }
    const signsIn = Boolean(maybeUser) || (Boolean(body.signup_nonce) && db.signupNonces?.[user.id] === body.signup_nonce);
    // Outside the browser that signed up nothing changes: sign in and open the same link again.
    if (!signsIn) return { data: { signed_in: false as const } };
    user.is_email_verified = true;
    user.pending_email = null;
    if (db.signupNonces) delete db.signupNonces[user.id];
    db.flags.unverifiedLogin = false;
    clearLoggedOut();
    return { data: mockTokens(user.id) };
  }),

  route(contract.resendVerification, ({ db, body, now }) => {
    const key = body.email.toLowerCase();
    const wait = tooSoon(db.resendAt[key] ?? 0, now);
    if (wait) throw wait;
    db.resendAt[key] = now;
    return { data: undefined };
  }),

  // 204 whether the email exists or not; the same 429 within 60 s for any email.
  route(contract.requestPasswordReset, ({ db, body, now }) => {
    const key = `reset:${body.email.toLowerCase()}`;
    const wait = tooSoon(db.resendAt[key] ?? 0, now);
    if (wait) throw wait;
    db.resendAt[key] = now;
    return { data: undefined };
  }),

  route(contract.confirmPasswordReset, ({ body }) => {
    if (body.token === 'invalido' || body.token === 'vencido') throw badRequest({ token: ['El link no es válido o ya venció.'] });
    if (/^\d+$/.test(body.password)) throw badRequest({ password: ['La contraseña no puede ser solo números.'] });
    return { data: undefined };
  }),

  route(contract.logout, ({ body, user }) => {
    // The session of the request is the proof: a refresh of another account closes nothing.
    const owner = userIdFromToken(body.refresh, 'refresh');
    if (owner && owner !== user.id) throw badRequest({ refresh: ['Ese refresh es de otra cuenta: no se cerró nada.'] });
    markLoggedOut();
    return { data: undefined };
  }),
];
