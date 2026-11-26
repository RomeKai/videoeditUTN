'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import { useLogin } from '@/api/hooks';
import { AuthCard } from '@/shell/PublicShell';
import { needsWelcome, useSession } from '@/shell/session';
import { Banner, Button, Field, Input, PasswordInput, cx } from '@/ui';
import styles from './acceso.module.css';
import { emailError, HOME, requestErrorMessage, safeNext } from './lib';

export const WRONG_CREDENTIALS = 'El email o la contraseña no coinciden.';

export interface LoginScreenProps {
  /** `?siguiente=`: where the user was going when the app asked to sign in. */
  next?: string | null;
  /** `?email=`: prefilled when coming from an invitation or from a new password. */
  email?: string | null;
  /** `?verificado=1`: the link of the email verified the account in a browser without its session (A3). */
  verified?: boolean;
}

/** A1 · Iniciar sesión (artboards A1-Login and M12-Movil-login). */
export function LoginScreen({ next, email: initialEmail, verified = false }: LoginScreenProps) {
  const router = useRouter();
  const { status, me, error: sessionError, refetch } = useSession();
  const login = useLogin();
  const [email, setEmail] = useState(initialEmail ?? '');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const target = safeNext(next, { verifyLink: true });

  // With a session there is nothing to do here: after signing in (or when arriving already signed in)
  // go to the page that was asked for, or to the welcome when the account is brand new.
  useEffect(() => {
    if (status !== 'authenticated' || !me) return;
    router.replace(target ?? (needsWelcome(me) ? '/bienvenida' : HOME));
  }, [me, router, status, target]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (login.isPending) return;
    const found = { email: emailError(email), password: password ? undefined : 'Escribí tu contraseña.' };
    setErrors(found);
    if (found.email || found.password) return;
    login.mutate(
      { email: email.trim(), password },
      {
        onError: (error) => {
          // The account exists but never opened the link of the verification email (map A1 → A3).
          if (error instanceof ApiError && error.code === 'email_not_verified') {
            const back = target ? `&siguiente=${encodeURIComponent(target)}` : '';
            router.push(`/verificar?email=${encodeURIComponent(email.trim())}&motivo=login${back}`);
          }
        },
      },
    );
  };

  const error = login.error;
  const unverified = error instanceof ApiError && error.code === 'email_not_verified';
  const formError = !error || unverified ? null : error instanceof ApiError && (error.status === 401 || error.status === 400) ? WRONG_CREDENTIALS : requestErrorMessage(error);
  // The button stays busy between the token and the profile, until the redirect happens.
  const entering = login.isPending || (login.isSuccess && status === 'loading');
  // The password was right (or there already was a session) but the profile did not load: say so instead of waiting forever.
  const profileFailed = status === 'error' && !login.isPending;

  return (
    <AuthCard
      title="Iniciar sesión"
      description="Entrá con tu email y tu contraseña."
      footer={
        <>
          ¿No tenés cuenta?{' '}
          <Link className="lnk" href="/registro">
            Crear cuenta
          </Link>
        </>
      }
    >
      <form className={cx(styles.form, styles.phone)} onSubmit={submit} noValidate aria-describedby={formError || profileFailed ? 'login-error' : undefined}>
        {formError ? (
          <Banner tone="err" id="login-error">
            {formError}
          </Banner>
        ) : profileFailed ? (
          <Banner
            tone="err"
            id="login-error"
            title="Entraste, pero no pudimos cargar tu cuenta"
            action={
              <Button size="sm" variant="secondary" iconLeft="retry" onClick={refetch}>
                Reintentar
              </Button>
            }
          >
            {requestErrorMessage(sessionError)}
          </Banner>
        ) : verified ? (
          <Banner tone="ok">Tu email quedó verificado. Iniciá sesión.</Banner>
        ) : null}
        <Field label="Email" error={errors.email}>
          <Input
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              if (errors.email) setErrors((current) => ({ ...current, email: undefined }));
            }}
          />
        </Field>
        <Field
          label="Contraseña"
          error={errors.password}
          labelAside={
            <Link className={cx('lnk t-small', styles.touchLink)} href="/recuperar">
              ¿Olvidaste tu contraseña?
            </Link>
          }
        >
          <PasswordInput
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              if (errors.password) setErrors((current) => ({ ...current, password: undefined }));
            }}
          />
        </Field>
        <Button variant="primary" type="submit" block loading={entering}>
          Iniciar sesión
        </Button>
      </form>
    </AuthCard>
  );
}
