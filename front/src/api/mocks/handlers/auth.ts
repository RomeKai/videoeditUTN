import * as contract from '../../contracts/auth';
import { KIND, mid, newId } from '../db';
import { badRequest, MockHttpError, route } from '../http';
import { PLAN_IDS } from '../seed';
import { clearLoggedOut, markLoggedOut, mockTokens, userIdFromToken } from '../state';

/**
 * Mock rules of the session:
 * - any known email signs in with any password except the literal "incorrecta";
 * - unknown emails answer 401, like wrong credentials;
 * - scenario "email-sin-verificar" (or an unverified user) answers 403 `email_not_verified`;
 * - the password "limite" answers 429 (too many attempts) and "caida" answers 500, to see A1's error states;
 * - tokens "invalido" / "vencido" fail in verify-email and password-reset/confirm.
 */
export const authHandlers = [
  route(contract.login, ({ db, body }) => {
    if (body.password === 'limite') throw new MockHttpError(429, { detail: 'Request was throttled. Expected available in 60 seconds.' });
    if (body.password === 'caida') throw new MockHttpError(500, { detail: 'Internal server error' });
    const user = db.users.find((item) => item.email.toLowerCase() === body.email.toLowerCase());
    if (!user || body.password === 'incorrecta') {
      throw new MockHttpError(401, { detail: 'No active account found with the given credentials' });
    }
    if (db.flags.unverifiedLogin || !user.is_email_verified) {
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
    if (db.users.some((item) => item.email.toLowerCase() === body.email.toLowerCase())) {
      throw badRequest({ email: ['Ya existe una cuenta con este email.'] });
    }
    if (/^\d+$/.test(body.password)) throw badRequest({ password: ['La contraseña no puede ser solo números.'] });
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
    db.workspaces.push({ id: workspaceId, name: 'Mi Espacio', owner: id, subscription_plan: PLAN_IDS.free, created_at: new Date(now).toISOString(), renews_at: null, pending_plan: null });
    db.members.push({ id: newId(), workspace: workspaceId, user: id, email: body.email, role: 'admin', status: 'active', joined_at: new Date(now).toISOString(), invited_by: null, token: null });
    db.wallets.push({ id: newId(), workspace: workspaceId, available_balance: 300, reserved_balance: 0, updated_at: new Date(now).toISOString() });
    db.resendAt[body.email] = now;
    return { data: { id, email: body.email, is_email_verified: false } };
  }),

  route(contract.verifyEmail, ({ db, body }) => {
    if (body.token === 'invalido' || body.token === 'vencido') {
      throw badRequest({ token: ['El link no es válido o ya venció.'] });
    }
    // The link's token names the account; in the mocks it verifies the newest unverified one.
    const user = [...db.users].reverse().find((item) => !item.is_email_verified) ?? db.users.find((item) => item.id === mid(KIND.user, 5));
    if (!user) throw badRequest({ token: ['El link no es válido o ya venció.'] });
    user.is_email_verified = true;
    db.flags.unverifiedLogin = false;
    clearLoggedOut();
    return { data: mockTokens(user.id) };
  }),

  route(contract.resendVerification, ({ db, body, now }) => {
    const last = db.resendAt[body.email] ?? 0;
    if (now - last < 60_000) {
      throw new MockHttpError(429, { detail: `Podés pedirlo de nuevo en ${Math.ceil((60_000 - (now - last)) / 1000)} segundos.` });
    }
    db.resendAt[body.email] = now;
    return { data: undefined };
  }),

  // Always 204: never says whether the email exists.
  route(contract.requestPasswordReset, () => ({ data: undefined })),

  route(contract.confirmPasswordReset, ({ body }) => {
    if (body.token === 'invalido' || body.token === 'vencido') throw badRequest({ token: ['El link no es válido o ya venció.'] });
    if (/^\d+$/.test(body.password)) throw badRequest({ password: ['La contraseña no puede ser solo números.'] });
    return { data: undefined };
  }),

  route(contract.logout, () => {
    markLoggedOut();
    return { data: undefined };
  }),
];
