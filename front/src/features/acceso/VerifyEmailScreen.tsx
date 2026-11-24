'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import { useResendVerification, useVerifyEmail } from '@/api/hooks';
import { useSession } from '@/shell/session';
import { Banner, Button, Card, Field, Icon, Input, Num, Skeleton, cx, useToast } from '@/ui';
import styles from './acceso.module.css';
import { emailError, formatCountdown, HOME, requestErrorMessage, retryAfterSeconds, useCooldown } from './lib';

export interface VerifyEmailScreenProps {
  /** `?email=`: the account waiting for verification (comes from A2 or from a login that answered "sin verificar"). */
  email?: string | null;
  /** `?token=`: the link of the email. Verifies the account and goes into the welcome. */
  token?: string | null;
  /** `?motivo=login`: the user tried to sign in with an unverified account. */
  fromLogin?: boolean;
}

/** A3 · Verificar email (artboard A3-Verificar-email). */
export function VerifyEmailScreen({ email, token, fromLogin = false }: VerifyEmailScreenProps) {
  const router = useRouter();
  const { status, me } = useSession();
  const verify = useVerifyEmail();
  const started = useRef(false);
  const { mutate: verifyToken } = verify;

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    verifyToken(token, { onSuccess: () => router.replace('/bienvenida') });
  }, [router, token, verifyToken]);

  // Without a link to check, a verified account that is already signed in has nothing to do here.
  useEffect(() => {
    if (!token && status === 'authenticated' && me?.is_email_verified) router.replace(HOME);
  }, [me, router, status, token]);

  if (token && !verify.isError) {
    return (
      <Card className={cx(styles.card, styles.verify)} aria-busy="true">
        <span className={styles.heroIcon}>
          <Icon name="mail" size={20} />
        </span>
        <div className={styles.title}>
          <h1 className="t-h2">Verificando tu email</h1>
          <p className="t-small muted">Un momento: estamos revisando el link.</p>
        </div>
        <Skeleton height={32} />
      </Card>
    );
  }

  const expired = Boolean(token);
  return (
    <>
      <Card className={cx(styles.card, styles.verify)}>
        <span className={styles.heroIcon}>
          <Icon name={expired ? 'warning' : 'mail'} size={20} />
        </span>
        <div className={styles.title}>
          <h1 className="t-h2">{expired ? 'Este link ya no sirve' : 'Te mandamos un link a tu email'}</h1>
          <p className="t-small muted">
            {expired ? (
              verify.error instanceof ApiError && verify.error.status === 400 ? (
                'El link de verificación venció o ya se usó. Pedí uno nuevo y abrilo desde tu correo.'
              ) : (
                requestErrorMessage(verify.error)
              )
            ) : email ? (
              <>
                Abrí el link que enviamos a <span className={styles.strong}>{email}</span> para verificar tu cuenta. Podés cerrar esta pestaña: el link te lleva directo a la bienvenida.
              </>
            ) : (
              'Abrí el link que te enviamos para verificar tu cuenta. Podés cerrar esta pestaña: el link te lleva directo a la bienvenida.'
            )}
          </p>
        </div>
        {fromLogin && !expired ? (
          <Banner tone="warn">Tu cuenta todavía no está verificada. Para entrar, abrí el link del email o pedí uno nuevo.</Banner>
        ) : null}
        <hr className="divider" />
        <Resend email={email ?? ''} />
      </Card>
      <p className={cx(styles.alt, styles.below)}>
        ¿Te equivocaste de email?{' '}
        <Link className="lnk" href="/registro">
          Cambiarlo
        </Link>
      </p>
    </>
  );
}

/** "Reenviar", available once every 60 s. Asks for the email when the URL did not bring it. */
function Resend({ email: knownEmail }: { email: string }) {
  const toast = useToast();
  const resend = useResendVerification();
  const [typed, setTyped] = useState('');
  const [fieldError, setFieldError] = useState<string>();
  const email = (knownEmail || typed).trim();
  const { secondsLeft, start } = useCooldown(email.toLowerCase() || 'sin-email');

  const send = (event?: FormEvent) => {
    event?.preventDefault();
    if (resend.isPending || secondsLeft > 0) return;
    const problem = emailError(email);
    setFieldError(problem);
    if (problem) return;
    resend.mutate(email, {
      onSuccess: () => {
        start(60);
        toast.success('Te mandamos otro email. Puede tardar un par de minutos en llegar.');
      },
      onError: (error) => {
        // Asked too soon: the answer says how long is left.
        if (error instanceof ApiError && error.status === 429) start(retryAfterSeconds(error));
      },
    });
  };

  const failed = resend.isError && !(resend.error instanceof ApiError && resend.error.status === 429);
  const button = (
    <Button variant="secondary" size="sm" type={knownEmail ? 'button' : 'submit'} onClick={knownEmail ? () => send() : undefined} disabled={secondsLeft > 0} loading={resend.isPending}>
      {secondsLeft > 0 ? (
        <>
          Reenviar en <Num>{formatCountdown(secondsLeft)}</Num>
        </>
      ) : (
        'Reenviar'
      )}
    </Button>
  );

  return (
    <>
      {failed ? <Banner tone="err">{requestErrorMessage(resend.error)}</Banner> : null}
      {knownEmail ? (
        <div className={styles.resend}>
          <span className="t-small muted">¿No te llegó? Revisá el correo no deseado.</span>
          {button}
        </div>
      ) : (
        <form className={styles.form} onSubmit={send} noValidate>
          <Field label="Email de tu cuenta" hint="¿No te llegó? Revisá el correo no deseado, o pedí otro link." error={fieldError}>
            <Input
              type="email"
              name="email"
              autoComplete="email"
              inputMode="email"
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
                setFieldError(undefined);
              }}
            />
          </Field>
          <div className={styles.resend}>
            <span />
            {button}
          </div>
        </form>
      )}
    </>
  );
}
