import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { retryAfterSeconds } from '@/features/acceso/lib';
import { errorMessage, fieldError } from '@/lib/error-messages';
import { ApiError } from '../errors';
import { register, verifyEmail } from './auth';
import real from './drf/responses.json';

/**
 * The account bodies of drf/responses.json (auth_register and the register_*, resend_*,
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
    const created = parse(register.response, real.auth_register) as { is_email_verified: boolean };
    expect(created.is_email_verified).toBe(false);
    // The link of the new account answers the same pair as the login.
    parse(verifyEmail.response, real.token_pair);
  });

  it('registro: el email ya usado trae `code: "unique"` y la contraseña dice qué regla falla', () => {
    const taken = error('register_400_taken_and_weak', 'auth.register');
    expect(taken.fieldCodes.email).toBe('unique');
    expect(fieldError(taken, 'email')).toBe('Ya existe una cuenta con este email.');
    expect(fieldError(taken, 'password')).toBe('Esa contraseña es muy común. Elegí otra.');
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
