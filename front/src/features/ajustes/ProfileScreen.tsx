'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useId, useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import { useChangePassword, useLogout, useUpdateMe } from '@/api/hooks';
import { ConfirmDialog } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { useBeforeUnload } from '@/lib/hooks';
import { useSession } from '@/shell/session';
import { Banner, Button, Field, Input, PasswordInput, ThemePreferenceControl, useToast } from '@/ui';
import styles from './ajustes.module.css';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PASSWORD_MIN = 8;

function fieldError(error: unknown, field: string): string | undefined {
  return error instanceof ApiError ? error.fieldErrors[field]?.[0] : undefined;
}

function EmailForm({ current, onDone }: { current: string; onDone: () => void }) {
  const toast = useToast();
  const update = useUpdateMe();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [touched, setTouched] = useState(false);
  const value = email.trim().toLowerCase();
  const emailError = !EMAIL.test(value) ? 'Escribí un email válido.' : value === current.toLowerCase() ? 'Ese ya es tu email.' : undefined;
  const passwordError = password === '' ? 'Escribí tu contraseña para confirmar.' : undefined;
  const known = fieldError(update.error, 'email') ?? fieldError(update.error, 'current_password');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (emailError || passwordError) return;
    update.mutate(
      { email: value, current_password: password },
      {
        onSuccess: () => {
          toast.success(`Te mandamos un link a ${value} para verificarlo`);
          onDone();
        },
      },
    );
  };

  return (
    <form className={styles.sub} onSubmit={submit} noValidate>
      <Field
        label="Email nuevo"
        hint="Te mandamos un link para verificarlo. Hasta que lo abras, seguís con el actual."
        error={(touched ? emailError : undefined) ?? fieldError(update.error, 'email')}
      >
        <Input type="email" autoComplete="email" placeholder="vos@ejemplo.com" value={email} onChange={(event) => setEmail(event.target.value)} data-autofocus />
      </Field>
      <Field label="Tu contraseña" error={(touched ? passwordError : undefined) ?? fieldError(update.error, 'current_password')}>
        <PasswordInput autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
      </Field>
      {update.isError && !known ? (
        <p className={`t-small ${styles.subFull}`} style={{ color: 'var(--err)' }} role="alert">
          {errorMessage(update.error, 'No pudimos cambiar el email.')}
        </p>
      ) : null}
      <div className={styles.subActions}>
        <Button variant="ghost" size="sm" onClick={onDone} disabled={update.isPending}>
          Cancelar
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={update.isPending}>
          Enviar link de verificación
        </Button>
      </div>
    </form>
  );
}

function PasswordForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const change = useChangePassword();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [touched, setTouched] = useState(false);
  const currentError = current === '' ? 'Escribí tu contraseña actual.' : undefined;
  const nextError = next.length < PASSWORD_MIN ? `Tiene que tener ${PASSWORD_MIN} caracteres como mínimo.` : next === current ? 'Elegí una contraseña distinta a la actual.' : undefined;
  const repeatError = repeat !== next ? 'Las contraseñas no coinciden.' : undefined;
  const known = fieldError(change.error, 'current_password') ?? fieldError(change.error, 'new_password');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (currentError || nextError || repeatError) return;
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          toast.success('Contraseña cambiada');
          onDone();
        },
      },
    );
  };

  return (
    <form className={styles.sub} onSubmit={submit} noValidate>
      <Field label="Contraseña actual" error={(touched ? currentError : undefined) ?? fieldError(change.error, 'current_password')}>
        <PasswordInput autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} data-autofocus />
      </Field>
      <Field label="Contraseña nueva" error={(touched ? nextError : undefined) ?? fieldError(change.error, 'new_password')}>
        <PasswordInput autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} />
      </Field>
      <Field label="Repetí la contraseña nueva" error={touched ? repeatError : undefined}>
        <PasswordInput autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} />
      </Field>
      {change.isError && !known ? (
        <p className={`t-small ${styles.subFull}`} style={{ color: 'var(--err)' }} role="alert">
          {errorMessage(change.error, 'No pudimos cambiar la contraseña.')}
        </p>
      ) : null}
      <div className={styles.subActions}>
        <Button variant="ghost" size="sm" onClick={onDone} disabled={change.isPending}>
          Cancelar
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={change.isPending}>
          Cambiar contraseña
        </Button>
      </div>
    </form>
  );
}

/** I1 · Perfil: name, email (asks to verify again), password, theme and closing the session. */
export function ProfileScreen() {
  const toast = useToast();
  const router = useRouter();
  const id = useId();
  const { me } = useSession();
  const update = useUpdateMe();
  const logout = useLogout();
  const saved = me?.name ?? '';
  const [name, setName] = useState(saved);
  const [open, setOpen] = useState<'email' | 'password' | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  useEffect(() => setName(saved), [saved]);

  const trimmed = name.trim();
  const dirty = trimmed !== saved;
  const empty = trimmed === '';
  useBeforeUnload(dirty);
  const nameError = dirty && empty ? 'Escribí tu nombre.' : update.isError ? (fieldError(update.error, 'name') ?? errorMessage(update.error, 'No pudimos guardar tu nombre.')) : null;

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!dirty || empty) return;
    update.mutate({ name: trimmed }, { onSuccess: () => toast.success('Guardado') });
  };
  const leave = (allDevices: boolean) => logout.mutate({ allDevices }, { onSettled: () => router.replace('/login') });

  if (!me) return null;

  return (
    <>
      <section className={styles.group} aria-labelledby={`${id}-datos`}>
        <div className={styles.head}>
          <div className={styles.headText}>
            <h2 className="t-h3" id={`${id}-datos`}>
              Datos
            </h2>
          </div>
        </div>
        <form onSubmit={save} noValidate id={`${id}-form`}>
          <div className={styles.row}>
            <div className={styles.label}>
              <label htmlFor={`${id}-name`}>Nombre</label>
              <span className={styles.desc}>Así te ve tu equipo en los proyectos y en Miembros.</span>
              {nameError ? (
                <span className="t-small" style={{ color: 'var(--err)' }} role="alert">
                  {nameError}
                </span>
              ) : null}
            </div>
            <div className={styles.control}>
              <Input
                id={`${id}-name`}
                value={name}
                maxLength={150}
                autoComplete="name"
                invalid={Boolean(nameError)}
                onChange={(event) => {
                  setName(event.target.value);
                  if (update.isError) update.reset();
                }}
              />
            </div>
          </div>
        </form>
        <div className={styles.item}>
          <div className={styles.row}>
            <div className={styles.label}>
              <span>Email</span>
              <span className={styles.desc}>Con este email entrás y recibís los avisos. Si lo cambiás, te pedimos verificarlo de nuevo.</span>
            </div>
            <div className={styles.control}>
              <span className={styles.value}>{me.email}</span>
              <Button variant="secondary" size="sm" aria-expanded={open === 'email'} onClick={() => setOpen(open === 'email' ? null : 'email')}>
                Cambiar email
              </Button>
            </div>
          </div>
          {me.pending_email && open !== 'email' ? (
            <Banner tone="info" className={styles.pendingEmail} title={`Falta verificar ${me.pending_email}`}>
              Abrí el link que te mandamos a ese email. Hasta entonces seguís entrando con {me.email}.
            </Banner>
          ) : null}
          {open === 'email' ? <EmailForm current={me.email} onDone={() => setOpen(null)} /> : null}
        </div>
        <div className={styles.item}>
          <div className={styles.row}>
            <div className={styles.label}>
              <span>Contraseña</span>
              <span className={styles.desc}>
                Tiene que tener <span className="num">{PASSWORD_MIN}</span> caracteres como mínimo.
              </span>
            </div>
            <div className={styles.control}>
              <Button variant="secondary" size="sm" aria-expanded={open === 'password'} onClick={() => setOpen(open === 'password' ? null : 'password')}>
                Cambiar contraseña
              </Button>
            </div>
          </div>
          {open === 'password' ? <PasswordForm onDone={() => setOpen(null)} /> : null}
        </div>
        <div className={styles.save}>
          <span className="t-small subtle" role="status">
            {dirty ? 'Tenés cambios sin guardar.' : 'No hay cambios para guardar.'}
          </span>
          <Button variant="primary" type="submit" form={`${id}-form`} disabled={!dirty || empty} loading={update.isPending && !open}>
            Guardar
          </Button>
        </div>
      </section>

      <section className={styles.group} aria-labelledby={`${id}-app`}>
        <div className={styles.head}>
          <div className={styles.headText}>
            <h2 className="t-h3" id={`${id}-app`}>
              Apariencia
            </h2>
          </div>
        </div>
        <div className={styles.row}>
          <div className={styles.label}>
            <span>Tema</span>
            <span className={styles.desc}>Sistema sigue el tema de tu dispositivo. Se guarda al elegir y vale para todos tus workspaces.</span>
          </div>
          <div className={styles.control}>
            <ThemePreferenceControl />
          </div>
        </div>
      </section>

      <section className={styles.group} aria-labelledby={`${id}-ses`}>
        <div className={styles.head}>
          <div className={styles.headText}>
            <h2 className="t-h3" id={`${id}-ses`}>
              Sesión
            </h2>
          </div>
        </div>
        <div className={styles.row}>
          <div className={styles.label}>
            <span>Cerrar sesión</span>
            <span className={styles.desc}>Salís de OneCreator en este navegador.</span>
          </div>
          <div className={styles.control}>
            <Button variant="secondary" size="sm" iconLeft="logout" onClick={() => leave(false)} loading={logout.isPending && !confirmAll}>
              Cerrar sesión
            </Button>
          </div>
        </div>
        <div className={styles.row}>
          <div className={styles.label}>
            <span>Cerrar sesión en todos los dispositivos</span>
            <span className={styles.desc}>Salís también de tu teléfono y de cualquier otro navegador.</span>
          </div>
          <div className={styles.control}>
            <Button variant="secondary" size="sm" onClick={() => setConfirmAll(true)}>
              Cerrar sesión en todos
            </Button>
          </div>
        </div>
      </section>

      <ConfirmDialog
        open={confirmAll}
        onClose={() => setConfirmAll(false)}
        onConfirm={() => leave(true)}
        loading={logout.isPending}
        title="¿Cerrar sesión en todos los dispositivos?"
        description="Vas a tener que volver a entrar con tu email y tu contraseña en cada uno, también en este navegador."
        confirmLabel="Cerrar sesión en todos"
      />
    </>
  );
}
