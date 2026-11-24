'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent, type ReactNode } from 'react';
import type { InvitationPreview } from '@/api/contracts/workspaces';
import { ApiError } from '@/api/errors';
import { useInvitation, useLogout } from '@/api/hooks';
import { ROLE_LABEL } from '@/lib/permissions';
import { LoadError } from '@/shell/LoadError';
import { useSession } from '@/shell/session';
import { Avatar, Badge, Banner, Button, Card, Field, Input, PasswordInput, Skeleton, SkeletonText, cx, useToast } from '@/ui';
import styles from './acceso.module.css';
import { useJoinWorkspace } from './hooks';
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
  const loginHref = `/login?siguiente=${encodeURIComponent(`/invitacion/${token}`)}`;

  if (invitation.isPending) {
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

  if (invitation.isError) {
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

  const data = invitation.data;
  const member = status === 'authenticated' && me ? me.workspaces.some((workspace) => workspace.name === data.workspace_name) : false;

  if (data.status === 'expired') {
    return (
      <StateCard badge={<Badge tone="warn" dot>Vencida</Badge>} title="Esta invitación venció">
        Pedile a {data.inviter_name} que te mande una nueva.
      </StateCard>
    );
  }

  if (data.status === 'used') {
    if (member) {
      return (
        <StateCard
          badge={<Badge tone="ok" dot>Ya sos miembro</Badge>}
          title={`Ya sos parte de ${data.workspace_name}`}
          action={<EnterWorkspace name={data.workspace_name} />}
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

  let body: ReactNode;
  let footer: ReactNode = null;
  if (status === 'loading') {
    body = (
      <div className={styles.form} aria-busy="true">
        <Skeleton height={36} />
        <Skeleton height={36} />
      </div>
    );
  } else if (status === 'authenticated' && me) {
    body = sameEmail(me.email, data.email) ? <AcceptAsMember token={token} invitation={data} /> : <OtherAccount invitation={data} />;
  } else if (data.has_account) {
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
    body = <CreateAccount token={token} invitation={data} loginHref={loginHref} />;
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
function EnterWorkspace({ name }: { name: string }) {
  const router = useRouter();
  const { me, switchWorkspace } = useSession();
  const [busy, setBusy] = useState(false);
  const enter = async () => {
    const workspace = me?.workspaces.find((item) => item.name === name);
    setBusy(true);
    try {
      if (workspace) await switchWorkspace(workspace.id);
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

/** After accepting: the app opens on the workspace the user has just joined (the hook already made it the active one). */
function useJoin(invitation: InvitationPreview) {
  const router = useRouter();
  const toast = useToast();
  return () => {
    toast.success(`Ya sos parte de ${invitation.workspace_name}`);
    router.replace(HOME);
  };
}

/** Errors of "accept" that are not about a field. A 410 reloads the invitation, which then explains itself. */
function AcceptError({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof ApiError && (error.status === 400 || error.status === 410 || error.status === 401)) return null;
  return <Banner tone="err">{requestErrorMessage(error)}</Banner>;
}

function AcceptAsMember({ token, invitation }: { token: string; invitation: InvitationPreview }) {
  const { me } = useSession();
  const accept = useJoinWorkspace(token);
  const invitationQuery = useInvitation(token);
  const join = useJoin(invitation);
  const [joining, setJoining] = useState(false);
  if (!me) return null;
  // mutateAsync: the answer reloads the invitation (now "used"), which unmounts this form before a mutate() callback could run.
  const submit = async () => {
    try {
      await accept.mutateAsync({});
      setJoining(true);
      join();
    } catch (error) {
      if (error instanceof ApiError && error.status === 410) void invitationQuery.refetch();
    }
  };
  return (
    <div className={styles.form}>
      <AcceptError error={accept.error} />
      <div className={styles.who}>
        <Avatar name={me.name} seed={me.id} />
        <span className={styles.whoText}>
          <span className={styles.strong}>{me.name}</span>
          <span className="t-small muted">{me.email}</span>
        </span>
      </div>
      <Button variant="primary" block loading={accept.isPending || joining} onClick={() => void submit()} className={styles.tall}>
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

function CreateAccount({ token, invitation, loginHref }: { token: string; invitation: InvitationPreview; loginHref: string }) {
  const router = useRouter();
  const accept = useJoinWorkspace(token);
  const invitationQuery = useInvitation(token);
  const join = useJoin(invitation);
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [terms, setTerms] = useState(false);
  const [joining, setJoining] = useState(false);
  const [errors, setErrors] = useState<{ name?: string; password?: string; terms?: boolean }>({});

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (accept.isPending || joining) return;
    const found = { name: nameError(name), password: newPasswordError(password), terms: !terms };
    setErrors(found);
    if (found.name || found.password || found.terms) return;
    try {
      await accept.mutateAsync({ name: name.trim(), password, terms_accepted: true });
      setJoining(true);
      join();
    } catch (error) {
      if (!(error instanceof ApiError)) return;
      if (error.status === 400) setErrors({ name: error.fieldErrors.name?.[0], password: error.fieldErrors.password?.[0] });
      // Someone created the account in the meantime: accepting now needs its session.
      if (error.status === 401) router.push(`${loginHref}&email=${encodeURIComponent(invitation.email)}`);
      if (error.status === 410) void invitationQuery.refetch();
    }
  };

  return (
    <form className={cx(styles.form, styles.phone)} onSubmit={(event) => void submit(event)} noValidate>
      <AcceptError error={accept.error} />
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
      <Button variant="primary" type="submit" block loading={accept.isPending || joining}>
        Crear cuenta y unirme
      </Button>
    </form>
  );
}
