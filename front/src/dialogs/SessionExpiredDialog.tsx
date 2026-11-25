'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';
import { useLogin } from '@/api/hooks';
import { clearSession, onSessionExpired } from '@/api/session';
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
 * failed are fetched again after signing in.
 */
export function SessionExpiredDialog() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const { me } = useSession();
  const login = useLogin();
  const queryClient = useQueryClient();
  const router = useRouter();

  useEffect(() => onSessionExpired(() => setOpen(true)), []);

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
      title="Tu sesión venció"
      description="Ingresá tu contraseña para seguir. Esta pantalla queda como la dejaste."
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
