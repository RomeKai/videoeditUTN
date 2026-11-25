'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import { useConfirmPasswordReset, useLogin, useRequestPasswordReset } from '@/api/hooks';
import { fieldError } from '@/lib/error-messages';
import { AuthCard } from '@/shell/PublicShell';
import { Banner, Button, Field, Input, Num, PasswordInput, cx, useToast } from '@/ui';
import styles from './acceso.module.css';
import { emailError, formatCountdown, HOME, MIN_PASSWORD, newPasswordError, requestErrorMessage, retryAfterSeconds, useCooldown } from './lib';

export interface RecoverScreenProps {
  /** `?token=`: the link of the email. With it the screen is step 2. */
  token?: string | null;
  /** `?email=`: the account the link belongs to (step 2), or a prefill for step 1. */
  email?: string | null;
}

/** A4 · Recuperar contraseña (artboard A4-Recuperar): step 1 asks for the link, step 2 sets the new password. */
export function RecoverScreen({ token, email }: RecoverScreenProps) {
  return token ? <NewPassword token={token} email={email ?? ''} /> : <RequestLink initialEmail={email ?? ''} />;
}

const backToLogin = (
  <Link className="lnk" href="/login">
    Volver a iniciar sesión
  </Link>
);

function RequestLink({ initialEmail }: { initialEmail: string }) {
  const request = useRequestPasswordReset();
  const [email, setEmail] = useState(initialEmail);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string>();
  const { secondsLeft, start } = useCooldown(`recuperar.${email.trim().toLowerCase()}`);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (request.isPending || secondsLeft > 0) return;
    const problem = emailError(email);
    setFieldError(problem);
    if (problem) return;
    const clean = email.trim();
    request.mutate(clean, {
      onSuccess: () => {
        setSentTo(clean);
        start(60);
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 429) {
          // Already asked a moment ago: the first email is on its way, so the answer is the same.
          setSentTo(clean);
          start(retryAfterSeconds(error));
        }
      },
    });
  };

  const failed = request.isError && !(request.error instanceof ApiError && request.error.status === 429);
  const sent = sentTo !== null && sentTo === email.trim() && !failed;

  return (
    <AuthCard title="Recuperar contraseña" description="Escribí el email de tu cuenta y te mandamos un link para elegir una contraseña nueva." footer={backToLogin}>
      <form className={cx(styles.form, styles.phone)} onSubmit={submit} noValidate>
        {failed ? <Banner tone="err">{requestErrorMessage(request.error)}</Banner> : null}
        {/* The same answer whether the email has an account or not (map A4). */}
        {sent ? <Banner tone="ok">Si el email existe, te mandamos un link. Revisá tu correo.</Banner> : null}
        <Field label="Email" error={fieldError}>
          <Input
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              setFieldError(undefined);
            }}
          />
        </Field>
        <Button variant={sent ? 'secondary' : 'primary'} type="submit" block disabled={secondsLeft > 0} loading={request.isPending}>
          {secondsLeft > 0 ? (
            <>
              Reenviar en <Num>{formatCountdown(secondsLeft)}</Num>
            </>
          ) : sent ? (
            'Reenviar'
          ) : (
            'Mandarme el link'
          )}
        </Button>
      </form>
    </AuthCard>
  );
}

function NewPassword({ token, email }: { token: string; email: string }) {
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirmPasswordReset();
  const login = useLogin();
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [errors, setErrors] = useState<{ password?: string; repeat?: string }>({});

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (confirm.isPending || login.isPending) return;
    const found = {
      password: newPasswordError(password),
      repeat: !repeat ? 'Repetí la contraseña.' : repeat !== password ? 'Las contraseñas no coinciden.' : undefined,
    };
    setErrors(found);
    if (found.password || found.repeat) return;
    confirm.mutate(
      { token, password },
      {
        onSuccess: () => {
          const toLogin = () => {
            toast.success('Contraseña guardada. Entrá con la nueva.');
            router.replace(email ? `/login?email=${encodeURIComponent(email)}` : '/login');
          };
          // "Guardar y entrar": the link says whose account it is, so the new password signs in right away.
          if (!email) return toLogin();
          login.mutate(
            { email, password },
            {
              onSuccess: () => {
                toast.success('Contraseña guardada');
                router.replace(HOME);
              },
              onError: toLogin,
            },
          );
        },
        onError: (error) => {
          const problem = fieldError(error, 'password');
          if (problem) setErrors({ password: problem });
        },
      },
    );
  };

  const error = confirm.error;
  const badLink = error instanceof ApiError && error.status === 400 && !error.fieldErrors.password;
  if (badLink) {
    return (
      <AuthCard title="Este link ya no sirve" description="El link para cambiar la contraseña venció o ya se usó. Pedí uno nuevo: llega en un par de minutos." footer={backToLogin}>
        <Button variant="primary" block href={email ? `/recuperar?email=${encodeURIComponent(email)}` : '/recuperar'}>
          Pedir otro link
        </Button>
      </AuthCard>
    );
  }
  const formError = error && !(error instanceof ApiError && error.status === 400) ? requestErrorMessage(error) : null;

  return (
    <AuthCard
      title="Elegí una contraseña nueva"
      description={
        email ? (
          <>
            Es para la cuenta <span className={styles.strong}>{email}</span>.
          </>
        ) : (
          'Después de guardarla entrás con ella.'
        )
      }
    >
      <form className={cx(styles.form, styles.phone)} onSubmit={submit} noValidate>
        {formError ? <Banner tone="err">{formError}</Banner> : null}
        <Field label="Contraseña nueva" hint={`Usá ${MIN_PASSWORD} caracteres o más.`} error={errors.password}>
          <PasswordInput
            name="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setErrors((current) => ({ ...current, password: undefined }));
            }}
          />
        </Field>
        <Field label="Repetí la contraseña" error={errors.repeat}>
          <PasswordInput
            name="password-repeat"
            autoComplete="new-password"
            value={repeat}
            onChange={(event) => {
              setRepeat(event.target.value);
              setErrors((current) => ({ ...current, repeat: undefined }));
            }}
          />
        </Field>
        <Button variant="primary" type="submit" block loading={confirm.isPending || login.isPending || login.isSuccess}>
          Guardar y entrar
        </Button>
      </form>
    </AuthCard>
  );
}
