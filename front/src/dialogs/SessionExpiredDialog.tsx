'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { SessionEndReason } from '@/api/errors';
import { useHasSession, useLogin } from '@/api/hooks';
import {
  clearSession,
  dropExpiredSession,
  getAccessSnapshot,
  getSessionEmail,
  getTokenGeneration,
  isSessionExpired,
  isSessionNeeded,
  onSessionExpired,
  subscribeSession,
  tokenUser,
} from '@/api/session';
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
 *
 * It closes by itself when the session of the same account comes back from another tab (a new pair in the
 * browser's storage), and when it is gone from the browser altogether (signed out in another tab).
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
  /**
   * The session the dialog opened over: its access token, how many pairs this tab had stored by then, and
   * whose it was. Taken once, as the dialog opens: the profile underneath can be read again later with
   * another account's token (another tab signed in as someone else), and it must not become the owner.
   */
  const dead = useRef<{ access: string | null; generation: number; owner: string | null } | null>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const meId = useRef<string | null>(null);
  meId.current = me?.id ?? null;
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
        // (The requests in flight report the same dead session one after another: the first one counts.)
        if (!openRef.current) {
          const access = getAccessSnapshot();
          dead.current = { access, generation: getTokenGeneration(), owner: meId.current ?? tokenUser(access) };
        }
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

  // Another tab stored a new pair (it signed in from the login, or renewed in time): this tab has a live
  // session again, so there is nothing to ask. The screen underneath stays, and what failed on it is read
  // again. A pair this tab stored itself (the form below) is not this case: `useLogin` already reads again.
  //
  // Looked at when the session module says something changed, never on a render: the pair can be in the
  // storage before this tab hears of it, and until then the session still counts as expired here (every
  // request would fail at once, and stay failed). And only for the same account: the screen underneath is
  // that person's, with their workspace and maybe their unsaved changes. Another account's pair leaves the
  // dialog where it is.
  const { reset: resetLogin } = login;
  useEffect(
    () =>
      subscribeSession(() => {
        const was = dead.current;
        if (!openRef.current || !was || isSessionExpired()) return;
        const access = getAccessSnapshot();
        if (access === null || access === was.access || getTokenGeneration() !== was.generation) return;
        if (was.owner === null || tokenUser(access) !== was.owner) return;
        setOpen(false);
        setPassword('');
        resetLogin();
        void queryClient.invalidateQueries();
      }),
    [queryClient, resetLogin],
  );

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
