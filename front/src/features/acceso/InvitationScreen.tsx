'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState, type FormEvent, type ReactNode } from 'react';
import type { InvitationPreview } from '@/api/contracts/workspaces';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/errors';
import { useAcceptInvitation, useInvitation, useLogout } from '@/api/hooks';
import { clearSession } from '@/api/session';
import { errorMessage, fieldError } from '@/lib/error-messages';
import { ROLE_LABEL } from '@/lib/permissions';
import { LoadError } from '@/shell/LoadError';
import { useSession } from '@/shell/session';
import { Avatar, Badge, Banner, Button, Card, Field, Input, PasswordInput, Skeleton, SkeletonText, cx, useToast } from '@/ui';
import styles from './acceso.module.css';
import { HOME, nameError, newPasswordError, requestErrorMessage } from './lib';
import { TermsCheckbox } from './Terms';

/** What each role can do, in one sentence (matrix of map 1.7). */
const ROLE_SUMMARY: Record<InvitationPreview['role'], string> = {
  admin: 'Como Admin podés crear, revisar y publicar, y además administrar miembros, redes y facturación.',
  editor: 'Como Editor podés crear, revisar y publicar. No administrás miembros ni facturación.',
  viewer: 'Como Lector podés ver los proyectos y descargar los clips. No creás ni publicás.',
};

const sameEmail = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** A5 · Aceptar invitación (artboard A5-Invitacion). */
export function InvitationScreen({ token }: { token: string }) {
  const invitation = useInvitation(token);
  const { status, me } = useSession();
  // Accepting changes what this screen is made of (the invitation becomes "used", a session appears).
  // While the user is on the way to the app the card stays as it was, instead of flashing its other states.
  const [joining, setJoining] = useState<{ data: InvitationPreview; mode: Mode } | null>(null);
  const [sessionLost, setSessionLost] = useState(false);
  const loginHref = `/login?siguiente=${encodeURIComponent(`/invitacion/${token}`)}`;

  if (!joining && invitation.isPending) {
    return (
      <Card className={styles.card} aria-busy="true">
        <span className="sr-only">Cargando la invitación</span>
        <div className={styles.invite}>
          <Skeleton width={44} height={44} radius={8} />
          <SkeletonText lines={2} />
        </div>
        <SkeletonText lines={2} />
        <hr className="divider" />
        <Skeleton height={36} />
        <Skeleton height={36} />
      </Card>
    );
  }

  if (!joining && (invitation.isError || !invitation.data)) {
    if (invitation.error instanceof ApiError && invitation.error.status === 404) {
      return (
        <StateCard badge={<Badge dot>No existe</Badge>} title="Esta invitación no existe">
          Revisá que el link esté completo, o pedile a quien te invitó que te mande una nueva.
        </StateCard>
      );
    }
    return (
      <Card className={styles.card}>
        <div className={styles.title}>
          <h1 className="t-h2">Invitación</h1>
        </div>
        <LoadError error={invitation.error} onRetry={() => void invitation.refetch()} fullPageForMissing={false} />
      </Card>
    );
  }

  const data = joining?.data ?? (invitation.data as InvitationPreview);
  const member = status === 'authenticated' && me ? me.workspaces.some((workspace) => workspace.id === data.workspace_id) : false;

  if (!joining && data.status === 'expired') {
    return (
      <StateCard badge={<Badge tone="warn" dot>Vencida</Badge>} title="Esta invitación venció">
        Pedile a {data.inviter_name} que te mande una nueva.
      </StateCard>
    );
  }

  if (!joining && data.status === 'used') {
    if (member) {
      return (
        <StateCard
          badge={<Badge tone="ok" dot>Ya sos miembro</Badge>}
          title={`Ya sos parte de ${data.workspace_name}`}
          action={<EnterWorkspace id={data.workspace_id} name={data.workspace_name} />}
        >
          No hace falta aceptar nada más: la invitación ya se usó.
        </StateCard>
      );
    }
    return (
      <StateCard badge={<Badge dot>Ya usada</Badge>} title="Esta invitación ya se usó">
        Si ya sos parte de {data.workspace_name},{' '}
        <Link className="lnk" href={status === 'authenticated' ? HOME : '/login'}>
          {status === 'authenticated' ? 'andá a tus proyectos' : 'iniciá sesión'}
        </Link>
        .
      </StateCard>
    );
  }

  const mode: Mode =
    joining?.mode ??
    (status === 'loading'
      ? 'loading'
      : status === 'authenticated' && me
        ? sameEmail(me.email, data.email)
          ? 'accept'
          : 'other'
        : data.has_account
          ? 'login'
          : 'create');
  const flow = { token, invitation: data, loginHref, onJoining: (active: boolean) => setJoining(active ? { data, mode } : null), onSessionLost: () => setSessionLost(true) };

  let body: ReactNode;
  let footer: ReactNode = null;
  if (mode === 'loading') {
    body = (
      <div className={styles.form} aria-busy="true">
        <Skeleton height={36} />
        <Skeleton height={36} />
      </div>
    );
  } else if (mode === 'accept') {
    body = <AcceptAsMember {...flow} />;
  } else if (mode === 'other') {
    body = <OtherAccount invitation={data} />;
  } else if (mode === 'login') {
    body = (
      <div className={styles.form}>
        <p className="t-small muted">
          Ya tenés una cuenta con <span className={styles.strong}>{data.email}</span>. Iniciá sesión con ese email para sumarte.
        </p>
        <Button variant="primary" block href={`${loginHref}&email=${encodeURIComponent(data.email)}`} className={styles.tall}>
          Iniciar sesión para aceptar
        </Button>
      </div>
    );
  } else {
    body = <CreateAccount {...flow} />;
    footer = (
      <>
        ¿Ya tenés cuenta?{' '}
        <Link className="lnk" href={loginHref}>
          Iniciá sesión para aceptar
        </Link>
      </>
    );
  }

  return (
    <Card className={styles.card}>
      <div className={styles.invite}>
        <Avatar name={data.workspace_name} kind="workspace" className={styles.inviteAvatar} />
        <div className={styles.inviteTitle}>
          <span className="t-over">Invitación</span>
          <h1 className="t-h3">
            {data.inviter_name} te invitó a {data.workspace_name} como {ROLE_LABEL[data.role]}
          </h1>
        </div>
      </div>
      <p className="t-small muted">{ROLE_SUMMARY[data.role]}</p>
      <hr className="divider" />
      {sessionLost && mode !== 'accept' ? (
        <Banner tone="warn" title="Tu sesión se cerró">
          Iniciá sesión de nuevo para aceptar la invitación.
        </Banner>
      ) : null}
      {body}
      {footer ? (
        <>
          <hr className="divider" />
          <p className={styles.alt}>{footer}</p>
        </>
      ) : null}
    </Card>
  );
}

function StateCard({ badge, title, action, children }: { badge: ReactNode; title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <Card className={cx(styles.card, styles.state)}>
      {badge}
      <h1 className={cx('t-h3', styles.stateTitle)}>{title}</h1>
      <p className="t-small muted">{children}</p>
      {action}
    </Card>
  );
}

/** Opens the app on the workspace of the invitation. */
function EnterWorkspace({ id, name }: { id: string; name: string }) {
  const router = useRouter();
  const { switchWorkspace } = useSession();
  const [busy, setBusy] = useState(false);
  const enter = async () => {
    setBusy(true);
    try {
      await switchWorkspace(id, { to: null });
    } catch {
      // The app opens on the workspace it had: the switcher is one click away.
    }
    router.replace(HOME);
  };
  return (
    <Button variant="primary" block loading={busy} onClick={() => void enter()} className={cx(styles.tall, styles.stateAction)}>
      Ir a {name}
    </Button>
  );
}

/** How long to wait for the workspace switch before opening the app anyway. An object so tests can shorten it. */
export const joinOptions = { switchTimeoutMs: 8000 };

/** After accepting: the app opens on the workspace the user has just joined. */
function useJoin(invitation: InvitationPreview) {
  const router = useRouter();
  const toast = useToast();
  const { switchWorkspace } = useSession();
  return async (workspaceId: string) => {
    // The switch stores the workspace at once and then saves it in the profile. Joined is joined: if that
    // request fails or never answers, the app opens anyway instead of leaving this card busy forever.
    await Promise.race([
      switchWorkspace(workspaceId, { to: null }).catch(() => undefined),
      new Promise<void>((resolve) => window.setTimeout(resolve, joinOptions.switchTimeoutMs)),
    ]);
    toast.success(`Ya sos parte de ${invitation.workspace_name}`);
    router.replace(HOME);
  };
}

/**
 * Every error of "accept" that is not shown under a field. A 410 is the exception: it reloads the
 * invitation, which then explains by itself that it expired or was used.
 */
function AcceptError({ error, loginHref, email }: { error: unknown; loginHref: string; email: string }) {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 410) return null;
    if (error.status === 401) {
      return (
        <Banner
          tone="err"
          title="Hace falta iniciar sesión"
          action={
            <Button size="sm" variant="secondary" href={`${loginHref}&email=${encodeURIComponent(email)}`}>
              Iniciar sesión
            </Button>
          }
        >
          Tu sesión se cerró o este email ya tiene una cuenta. Entrá con {email} para aceptar la invitación.
        </Banner>
      );
    }
    if (error.status === 400) {
      // Field errors are under their fields; anything else of a 400 is said here.
      if (fieldError(error, 'name') || fieldError(error, 'password')) return null;
      return <Banner tone="err">{errorMessage(error, 'No pudimos aceptar la invitación. Revisá los datos y probá de nuevo.')}</Banner>;
    }
  }
  return <Banner tone="err">{requestErrorMessage(error)}</Banner>;
}

type Mode = 'loading' | 'accept' | 'other' | 'login' | 'create';

interface FlowProps {
  token: string;
  invitation: InvitationPreview;
  loginHref: string;
  /** Tells the screen to keep this card while the app opens (and to let go if accepting fails). */
  onJoining: (active: boolean) => void;
  /** The session turned out to be dead and was closed: the screen explains it next to "Iniciar sesión". */
  onSessionLost: () => void;
}

function AcceptAsMember({ token, invitation, loginHref, onJoining, onSessionLost }: FlowProps) {
  const queryClient = useQueryClient();
  const { me } = useSession();
  const accept = useAcceptInvitation(token);
  const invitationQuery = useInvitation(token);
  const join = useJoin(invitation);
  const [busy, setBusy] = useState(false);
  const shown = useRef(me);
  // Kept while joining: the profile is refetched (and may blink) on the way to the app.
  if (me) shown.current = me;
  const who = shown.current;
  if (!who) return null;
  const submit = async () => {
    if (busy) return;
    setBusy(true);
    onJoining(true);
    try {
      const result = await accept.mutateAsync({});
      await join(result.workspace_id);
    } catch (error) {
      onJoining(false);
      setBusy(false);
      if (!(error instanceof ApiError)) return;
      if (error.status === 410) void invitationQuery.refetch();
      if (error.status === 401) {
        // The client already renewed the token and tried again before giving this answer, so the
        // session is dead. Its tokens must not stay: the login would see "a session" and bounce
        // straight back here. Closed, the card offers to sign in again.
        clearSession();
        queryClient.clear();
        onSessionLost();
      }
    }
  };
  return (
    <div className={styles.form}>
      <AcceptError error={accept.error} loginHref={loginHref} email={invitation.email} />
      <div className={styles.who}>
        <Avatar name={who.name} seed={who.id} />
        <span className={styles.whoText}>
          <span className={styles.strong}>{who.name}</span>
          <span className="t-small muted">{who.email}</span>
        </span>
      </div>
      <Button variant="primary" block loading={busy} onClick={() => void submit()} className={styles.tall}>
        Aceptar invitación
      </Button>
    </div>
  );
}

/** Signed in with an account that is not the invited one: the invitation belongs to another email. */
function OtherAccount({ invitation }: { invitation: InvitationPreview }) {
  const { me } = useSession();
  const logout = useLogout();
  if (!me) return null;
  return (
    <div className={styles.form}>
      <Banner tone="warn" title="Entraste con otra cuenta">
        Estás como <span className={styles.strong}>{me.email}</span> y esta invitación es para <span className={styles.strong}>{invitation.email}</span>. Para aceptarla, cerrá sesión
        y entrá con ese email.
      </Banner>
      <div className={styles.actions}>
        <Button variant="primary" block loading={logout.isPending} onClick={() => logout.mutate({})} className={styles.tall}>
          Cerrar sesión y seguir
        </Button>
        <Button variant="ghost" block href={HOME} className={styles.tall}>
          Seguir como {me.name}
        </Button>
      </div>
    </div>
  );
}

function CreateAccount({ token, invitation, loginHref, onJoining }: Omit<FlowProps, 'onSessionLost'>) {
  const accept = useAcceptInvitation(token);
  const invitationQuery = useInvitation(token);
  const join = useJoin(invitation);
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [terms, setTerms] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<{ name?: string; password?: string; terms?: boolean }>({});

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const found = { name: nameError(name), password: newPasswordError(password), terms: !terms };
    setErrors(found);
    if (found.name || found.password || found.terms) return;
    setBusy(true);
    onJoining(true);
    try {
      const result = await accept.mutateAsync({ name: name.trim(), password, terms_accepted: true });
      await join(result.workspace_id);
    } catch (error) {
      onJoining(false);
      setBusy(false);
      if (!(error instanceof ApiError)) return;
      if (error.status === 400) setErrors({ name: fieldError(error, 'name'), password: fieldError(error, 'password') });
      if (error.status === 410) void invitationQuery.refetch();
    }
  };

  return (
    <form className={cx(styles.form, styles.phone)} onSubmit={(event) => void submit(event)} noValidate>
      <AcceptError error={accept.error} loginHref={loginHref} email={invitation.email} />
      <p className="t-small muted">Todavía no tenés cuenta con este email. Creala acá mismo para sumarte.</p>
      <Field label="Email" hint="La invitación es para este email.">
        <Input type="email" value={invitation.email} disabled readOnly className={styles.readonly} />
      </Field>
      <Field label="Nombre" error={errors.name}>
        <Input
          name="name"
          autoComplete="name"
          maxLength={150}
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setErrors((current) => ({ ...current, name: undefined }));
          }}
        />
      </Field>
      <Field label="Contraseña" error={errors.password}>
        <PasswordInput
          name="password"
          autoComplete="new-password"
          placeholder="8 caracteres o más"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
            setErrors((current) => ({ ...current, password: undefined }));
          }}
        />
      </Field>
      <TermsCheckbox
        checked={terms}
        invalid={Boolean(errors.terms)}
        onChange={(checked) => {
          setTerms(checked);
          setErrors((current) => ({ ...current, terms: undefined }));
        }}
      />
      <Button variant="primary" type="submit" block loading={busy}>
        Crear cuenta y unirme
      </Button>
    </form>
  );
}
