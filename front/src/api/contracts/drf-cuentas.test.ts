import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { retryAfterSeconds } from '@/features/acceso/lib';
import { errorMessage, fieldError } from '@/lib/error-messages';
import { ApiError } from '../errors';
import { register, verifyEmail } from './auth';
import real from './drf/responses.json';

/**
 * The account bodies of drf/responses.json (auth_register, verify_email_signed_out and the register_*, resend_*,
 * password_reset_*, logout_* and session_* errors), parsed with the contract and read with the
 * helpers the screens use. back/apps/users/tests/test_auth_contract_fixture.py builds them with the
 * real endpoints and fails when they stop matching the JSON.
 */
function parse(schema: ZodTypeAny, value: unknown) {
  const result = schema.safeParse(value);
  if (!result.success) throw new Error(result.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`).join('; '));
  return result.data;
}

const error = (key: keyof typeof real.errors, endpoint = 'x') => new ApiError(real.errors[key].status, endpoint, real.errors[key].body);

describe('cuentas: respuestas reales del backend', () => {
  it('registro y verificación siguen el contrato', () => {
    const created = parse(register.response, real.auth_register) as { is_email_verified: boolean; signup_nonce: string };
    expect(created.is_email_verified).toBe(false);
    expect(created.signup_nonce.length).toBeGreaterThanOrEqual(32);
    // In the browser that signed up the link answers the same pair as the login; anywhere else, no session.
    expect(parse(verifyEmail.response, real.token_pair)).toHaveProperty('access');
    expect(parse(verifyEmail.response, real.verify_email_signed_out)).toEqual({ signed_in: false });
  });

  it('registro: la contraseña dice qué regla falla; el email ya usado trae `code: "unique"` y llega solo con lo demás bien', () => {
    const weak = error('register_400_weak', 'auth.register');
    expect(fieldError(weak, 'password')).toBe('Esa contraseña es muy común. Elegí otra.');
    expect(weak.fieldErrors.email).toBeUndefined();
    const taken = error('register_400_taken', 'auth.register');
    expect(taken.fieldCodes.email).toBe('unique');
    expect(fieldError(taken, 'email')).toBe('Ya existe una cuenta con este email.');
    expect(errorMessage(error('register_400_terms', 'auth.register'))).toBe('Para crear la cuenta tenés que aceptar los términos.');
  });

  it('reenviar: el 429 dice cuánto falta, en segundos', () => {
    expect(retryAfterSeconds(error('resend_429', 'auth.resendVerification'))).toBe(60);
  });

  it('recuperar: el link inválido no trae error en `password`; la contraseña débil sí', () => {
    const badLink = error('password_reset_confirm_400_token', 'auth.confirmPasswordReset');
    expect(badLink.fieldErrors.password).toBeUndefined();
    expect(fieldError(badLink, 'token')).toBe('El link no es válido o ya venció.');
    expect(fieldError(error('password_reset_confirm_400_password', 'auth.confirmPasswordReset'), 'password')).toBe('Esa contraseña es muy común. Elegí otra.');
  });

  it('cerrar sesión: el refresh de otra cuenta es un 400 por campo; la sesión cerrada en todos trae su código', () => {
    expect(fieldError(error('logout_400_other_account', 'auth.logout'), 'refresh')).toBe('Ese refresh es de otra cuenta: no se cerró nada.');
    const revoked = error('session_revoked_401');
    expect(revoked.status).toBe(401);
    expect(revoked.code).toBe('session_revoked');
  });
});
