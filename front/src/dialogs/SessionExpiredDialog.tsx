'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { SessionEndReason } from '@/api/errors';
import { useHasSession, useLogin } from '@/api/hooks';
import { clearSession, dropExpiredSession, getSessionEmail, isSessionNeeded, onSessionExpired } from '@/api/session';
import { useGuardedNavigation } from '@/shell/leaveGuard';
import { useSession } from '@/shell/session';
import { Avatar } from '@/ui/Avatar';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { Field, Input, PasswordInput } from '@/ui/Field';
import styles from './Dialogs.module.css';

/**
 * D9 · Sesión vencida: opens when the access token could not be renewed. It
 * asks for the password again without losing the screen; the queries that
 * failed are fetched again after signing in. When the session did not run out
 * but was closed with «cerrar sesión en todos los dispositivos», it says so.
 *
 * It only opens over something worth keeping: a screen that needs the session, or any screen where
 * the session was alive (the profile loaded). A session found dead on arrival at a screen that works
 * without one (the login with an old session still stored, a public page) is dropped instead, and the
 * login says so: a dialog asking to sign in on top of the form to sign in helps nobody.
 * When the profile never loaded, the email is the one remembered from the last time it did.
 */
export function SessionExpiredDialog() {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<SessionEndReason>('expired');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const { me } = useSession();
  const login = useLogin();
  const queryClient = useQueryClient();
  const router = useRouter();

  const known = useRef(false);
  known.current = me !== null;
  useEffect(
    () =>
      onSessionExpired((why) => {
        if (!known.current && !isSessionNeeded()) {
          // Given up in this tab only: another tab may have this dialog open over a screen it is keeping.
          if (dropExpiredSession(why)) queryClient.clear();
          // The stored pair is not the one that was refused (another tab renewed it): read again with it.
          else void queryClient.invalidateQueries();
          return;
        }
        // Not known from the profile: whose session it was the last time the profile loaded.
        if (!known.current) setEmail((typed) => typed || (getSessionEmail() ?? ''));
        setReason(why);
        setOpen(true);
      }),
    [queryClient],
  );

  // The session is gone from the browser (signed out in another tab): there is nothing left to renew, and
  // the screen underneath is already on its way to the login. The dialog does not stay on top of it.
  const hasSession = useHasSession();
  useEffect(() => {
    if (!hasSession) setOpen(false);
  }, [hasSession]);

  const knownEmail = me?.email ?? '';
  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(
      { email: knownEmail || email, password },
      {
        onSuccess: () => {
          setOpen(false);
          setPassword('');
          login.reset();
        },
      },
    );
  };

  const navigation = useGuardedNavigation();
  /**
   * Signing out drops the screen underneath. With unsaved changes on it the person is asked first
   * (D8, like any other way out), and only then the session goes.
   */
  const signOut = (to: '/login' | '/recuperar' = '/login') =>
    navigation.confirm(() => {
      setOpen(false);
      clearSession();
      queryClient.clear();
      // Without a session the app sends whatever screen is underneath to the login on its own, and that
      // redirect would win over a client navigation to the recovery page: that one is a full page load.
      if (to === '/login') router.replace(to);
      else window.location.assign(to);
    });

  return (
    <Dialog
      open={open}
      onClose={() => signOut()}
      dismissable={false}
      title={reason === 'revoked' ? 'Cerraste la sesión en todos tus dispositivos' : 'Tu sesión venció'}
      description={
        reason === 'revoked'
          ? 'Iniciá sesión de nuevo con tu contraseña para seguir. Esta pantalla queda como la dejaste.'
          : 'Ingresá tu contraseña para seguir. Esta pantalla queda como la dejaste.'
      }
      footer={
        <>
          <Button variant="ghost" onClick={() => signOut()}>
            Cerrar sesión
          </Button>
          <Button variant="primary" type="submit" form="session-expired-form" loading={login.isPending}>
            Continuar
          </Button>
        </>
      }
    >
      <form id="session-expired-form" className={styles.form} onSubmit={submit}>
        {me ? (
          <div className={styles.who}>
            <Avatar name={me.name} seed={me.id} />
            <div className={styles.whoMain}>
              <p className={styles.strong}>{me.name}</p>
              <p className="t-small muted">{me.email}</p>
            </div>
          </div>
        ) : (
          <Field label="Email">
            <Input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
          </Field>
        )}
        <Field
          label="Contraseña"
          labelAside={
            // A button that navigates, not a link: it signs out first, and that must wait for the guard's answer.
            <button type="button" className="lnk t-small" onClick={() => signOut('/recuperar')}>
              ¿Olvidaste tu contraseña?
            </button>
          }
          error={login.isError ? 'El email o la contraseña no coinciden.' : undefined}
        >
          <PasswordInput autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required data-autofocus />
        </Field>
      </form>
    </Dialog>
  );
}
