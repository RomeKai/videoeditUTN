'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import { usePlans, useRegister } from '@/api/hooks';
import { errorMessage, fieldError } from '@/lib/error-messages';
import { formatMinutes } from '@/lib/format';
import { AuthCard } from '@/shell/PublicShell';
import { useSession } from '@/shell/session';
import { Banner, Button, Field, Input, Num, PasswordInput, cx } from '@/ui';
import styles from './acceso.module.css';
import { emailError, HOME, MIN_PASSWORD, nameError, newPasswordError, requestErrorMessage, startCooldown } from './lib';
import { TermsCheckbox } from './Terms';

export const EMAIL_TAKEN = 'Ya existe una cuenta con este email.';

/** The email already has an account: `code: "unique"` (proposed), or DRF's own sentence while the code is missing. */
function emailTaken(error: ApiError): boolean {
  return error.fieldCodes.email === 'unique' || /already exists|ya existe/i.test(error.fieldErrors.email?.[0] ?? '');
}

interface Errors {
  name?: string;
  email?: string;
  password?: string;
  terms?: boolean;
}

/** A2 · Crear cuenta (artboard A2-Registro). After creating the account it goes to A3. */
export function RegisterScreen({ email: initialEmail }: { email?: string | null }) {
  const router = useRouter();
  const { status } = useSession();
  const register = useRegister();
  const plans = usePlans();
  const [name, setName] = useState('');
  const [email, setEmail] = useState(initialEmail ?? '');
  const [password, setPassword] = useState('');
  const [terms, setTerms] = useState(false);
  const [errors, setErrors] = useState<Errors>({});

  useEffect(() => {
    if (status === 'authenticated') router.replace(HOME);
  }, [router, status]);

  // The cheapest plan is the one a new account starts with; its limit comes from the API, not from the text.
  const freePlan = plans.data ? [...plans.data].sort((a, b) => a.max_video_duration_seconds - b.max_video_duration_seconds)[0] : undefined;

  const clear = (field: keyof Errors) => setErrors((current) => (current[field] ? { ...current, [field]: undefined } : current));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (register.isPending) return;
    const found: Errors = { name: nameError(name), email: emailError(email), password: newPasswordError(password), terms: !terms };
    setErrors(found);
    if (found.name || found.email || found.password || found.terms) return;
    const cleanEmail = email.trim();
    register.mutate(
      { name: name.trim(), email: cleanEmail, password, terms_accepted: true },
      {
        onSuccess: () => {
          // The verification email has just been sent: A3 starts with the 60 s wait.
          startCooldown(cleanEmail.toLowerCase(), 60);
          router.push(`/verificar?email=${encodeURIComponent(cleanEmail)}`);
        },
        onError: (error) => {
          if (error instanceof ApiError && error.status === 400) {
            setErrors({
              name: fieldError(error, 'name'),
              email: emailTaken(error) ? EMAIL_TAKEN : fieldError(error, 'email'),
              password: fieldError(error, 'password'),
            });
          }
        },
      },
    );
  };

  const error = register.error;
  const fieldProblem = error instanceof ApiError && error.status === 400 && Object.keys(error.fieldErrors).some((key) => ['name', 'email', 'password'].includes(key));
  const formError = !error || fieldProblem ? null : error instanceof ApiError && error.status === 400 ? errorMessage(error, 'Revisá los datos y probá de nuevo.') : requestErrorMessage(error);
  const taken = errors.email === EMAIL_TAKEN;

  return (
    <AuthCard
      title="Crear cuenta"
      description={
        <>
          {freePlan ? (
            <>
              Empezás gratis: videos de hasta <Num>{formatMinutes(freePlan.max_video_duration_seconds)}</Num>.
            </>
          ) : (
            'Empezás gratis.'
          )}{' '}
          <Link className="lnk" href="/precios">
            Ver planes
          </Link>
        </>
      }
      footer={
        <>
          ¿Ya tenés cuenta?{' '}
          <Link className="lnk" href="/login">
            Iniciar sesión
          </Link>
        </>
      }
    >
      <form className={cx(styles.form, styles.phone)} onSubmit={submit} noValidate>
        {formError ? <Banner tone="err">{formError}</Banner> : null}
        <Field label="Nombre" error={errors.name}>
          <Input
            name="name"
            autoComplete="name"
            maxLength={150}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              clear('name');
            }}
          />
        </Field>
        <Field
          label="Email"
          error={
            taken ? (
              <>
                {errors.email}{' '}
                <Link className="lnk" href={`/login?email=${encodeURIComponent(email.trim())}`}>
                  Iniciar sesión
                </Link>
              </>
            ) : (
              errors.email
            )
          }
        >
          <Input
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              clear('email');
            }}
          />
        </Field>
        <Field label="Contraseña" hint={`Usá ${MIN_PASSWORD} caracteres o más.`} error={errors.password}>
          <PasswordInput
            name="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              clear('password');
            }}
          />
        </Field>
        <TermsCheckbox
          checked={terms}
          invalid={Boolean(errors.terms)}
          onChange={(checked) => {
            setTerms(checked);
            clear('terms');
          }}
        />
        <Button variant="primary" type="submit" block loading={register.isPending || register.isSuccess}>
          Crear cuenta
        </Button>
      </form>
    </AuthCard>
  );
}
