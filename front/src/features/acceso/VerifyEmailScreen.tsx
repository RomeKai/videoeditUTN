'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import { useResendVerification, useVerifyEmail } from '@/api/hooks';
import { useSession } from '@/shell/session';
import { Banner, Button, Card, Field, Icon, Input, Num, Skeleton, cx, useToast } from '@/ui';
import styles from './acceso.module.css';
import {
  dropQueryToken,
  emailError,
  forgetSignupNonce,
  forgetVerifyLink,
  formatCountdown,
  HOME,
  readSignupNonce,
  readVerifyLink,
  rememberNext,
  rememberVerifyLink,
  requestErrorMessage,
  retryAfterSeconds,
  safeNext,
  takeNext,
  takeVerifyLink,
  useCooldown,
  VERIFY_ROUTE,
  type VerifyLink,
} from './lib';

/** Where the login comes back to after signing in. Never the token: a query string ends up in the web host's logs and in the history. */
const BACK_TO_LINK = encodeURIComponent(VERIFY_ROUTE);

/**
 * The page of A3, `/verificar`. The link of the email brings its token in the fragment (`#token=…`, and
 * `&siguiente=…` in an email change), which only the browser sees: it is read once and dropped from the
 * address bar. A link sent before that has it in the query (`?token=…`): it is read too, the fragment first.
 * Coming back from the login (bare `/verificar`), the link is the one this tab kept. The waiting screen
 * (`?email=`) checks no link, and forgets one an earlier visit left.
 */
export function VerifyEmailPage({ email, token, fromLogin, next }: VerifyEmailScreenProps) {
  const [link, setLink] = useState<VerifyLink | null | undefined>(undefined);
  // Taken once: the fragment is gone after the first read (and React may run the effect twice).
  const taken = useRef<VerifyLink | null | undefined>(undefined);

  useEffect(() => {
    if (taken.current === undefined) {
      const fromLink = takeVerifyLink();
      const fromQuery = token ? { token, next: next ?? null } : null;
      if (fromQuery) dropQueryToken();
      taken.current = fromLink ?? fromQuery ?? (email ? null : readVerifyLink());
      if (!taken.current) forgetVerifyLink();
    }
    setLink(taken.current);
    // Another link pasted in this same tab changes only the fragment: no new page load.
    const onHashChange = () => {
      const found = takeVerifyLink();
      if (!found) return;
      taken.current = found;
      setLink(found);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [email, next, token]);

  if (link === undefined) return null;
  return <VerifyEmailScreen key={link?.token ?? ''} email={email} token={link?.token} fromLogin={fromLogin} next={link ? link.next : next} />;
}

export interface VerifyEmailScreenProps {
  /** `?email=`: the account waiting for verification (comes from A2 or from a login that answered "sin verificar"). */
  email?: string | null;
  /** The token of the link of the email (`VerifyEmailPage` reads it). Verifies the account and goes into the welcome. */
  token?: string | null;
  /** `?motivo=login`: the user tried to sign in with an unverified account. */
  fromLogin?: boolean;
  /** `?siguiente=`: where the user was going when the login found the account unverified. */
  next?: string | null;
}

/** A3 · Verificar email (artboard A3-Verificar-email). */
export function VerifyEmailScreen({ email, token, fromLogin = false, next }: VerifyEmailScreenProps) {
  const router = useRouter();
  const { status, me } = useSession();
  const verify = useVerifyEmail();
  const started = useRef(false);
  const { mutate: verifyToken } = verify;

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    // The proof of this browser's sign-up, if there is one: with it the link also signs in.
    verifyToken({ token, signup_nonce: readSignupNonce() }, { onSuccess: () => forgetSignupNonce() });
  }, [token, verifyToken]);

  // Opened outside the browser that signed up: nothing changed. With a session the same link verifies.
  const signedOut = verify.isSuccess && !('access' in verify.data);
  useEffect(() => {
    if (!signedOut || !token) return;
    // The login comes back to `/verificar`, and the link waits in this tab.
    rememberVerifyLink({ token, next: next ?? null });
    router.replace(`/login?motivo=verificar&siguiente=${BACK_TO_LINK}`);
  }, [next, router, signedOut, token]);

  // Verified: back to the page that was asked for (kept while waiting for the email), or into the welcome.
  // The remembered page is only for the account it was stored for, so this waits to know who signed in;
  // if the profile does not load, the link's own email is the only clue.
  const verified = verify.isSuccess && !signedOut;
  // A 403 `login_required` keeps the link for after the login; verified, dead or of another account, it goes.
  const needsLogin = Boolean(token) && verify.error instanceof ApiError && verify.error.status === 403 && verify.error.code === 'login_required';
  const spent = verified || (verify.error instanceof ApiError && !needsLogin);
  useEffect(() => {
    if (needsLogin && token) rememberVerifyLink({ token, next: next ?? null });
    else if (spent) forgetVerifyLink();
  }, [needsLogin, next, spent, token]);
  const verifiedEmail = me?.email ?? (status === 'error' ? (email ?? '') : null);
  const left = useRef(false);
  useEffect(() => {
    if (!verified || verifiedEmail === null || left.current) return;
    left.current = true;
    // Always taken, so it never outlives this verification, even when the link carries its own destination.
    const remembered = takeNext(verifiedEmail);
    router.replace(safeNext(next) ?? remembered ?? '/bienvenida');
  }, [next, router, verified, verifiedEmail]);

  useEffect(() => {
    if (!token) rememberNext(next, email);
  }, [email, next, token]);

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

  // The backend never swaps the session for another account's (403): say why, and apply nothing.
  const denied = token && verify.error instanceof ApiError && verify.error.status === 403 ? verify.error : null;
  if (denied) {
    // After signing in, the login brings the person back to this same link (kept for this tab, with its destination).
    return (
      <Card className={cx(styles.card, styles.verify)}>
        <span className={styles.heroIcon}>
          <Icon name="warning" size={20} />
        </span>
        <div className={styles.title}>
          <h1 className="t-h2">{needsLogin ? 'Iniciá sesión para confirmar el email' : 'Este link es de otra cuenta'}</h1>
          <p className="t-small muted">{denied.message}</p>
        </div>
        <Link className="lnk" href={needsLogin ? `/login?siguiente=${BACK_TO_LINK}` : HOME}>
          {needsLogin ? 'Iniciar sesión' : 'Volver a OneCreator'}
        </Link>
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
