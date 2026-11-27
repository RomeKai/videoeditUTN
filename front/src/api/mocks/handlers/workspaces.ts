import * as contract from '../../contracts/workspaces';
import { newId, PLATFORMS, type MockDb, type MockWorkspace } from '../db';
import { badRequest, conflict, forbidden, MockHttpError, notFound, route } from '../http';
import { PLAN_IDS } from '../seed';
import { planOf, serializeMember, serializeWorkspace } from '../serialize';
import { roleIn } from '../sim';
import { mockTokens } from '../state';

function workspaceFor(db: MockDb, id: string | undefined, userId: string): { workspace: MockWorkspace; role: NonNullable<ReturnType<typeof roleIn>> } {
  const workspace = db.workspaces.find((item) => item.id === id);
  const role = workspace ? roleIn(db, workspace.id, userId) : null;
  // The backend's answer (back/apps/core/workspace.py · NOT_FOUND_DETAIL): the role is the one in the workspace of the path.
  if (!workspace || !role) throw notFound('No encontramos ese espacio de trabajo.');
  return { workspace, role };
}

// The 403 of a role, as the backend writes it (back/apps/core/permissions.py · DENIED): only `detail`, no `code`.
const adminsOnly = () => new MockHttpError(403, { detail: 'Solo el dueño o un admin del espacio pueden hacer esto. Pedile acceso a un admin.' });
const ownerOnly = () => new MockHttpError(403, { detail: 'Solo el dueño del espacio puede hacer esto.' });
const memberNotFound = () => notFound('No encontramos a ese miembro.');
// What the backend says for a name that is empty once trimmed (the schema already refused '' and more than 100).
const cleanName = (name: string) => {
  const trimmed = name.trim();
  if (!trimmed) throw badRequest({ name: ['Escribí un nombre para el espacio.'] });
  return trimmed;
};

function seats(db: MockDb, workspaceId: string) {
  const plan = planOf(db, workspaceId);
  const members = db.members.filter((member) => member.workspace === workspaceId);
  // A pending invitation takes a seat (map 9.1.8).
  return { members, plan, max: plan?.max_members ?? 1, used: members.length };
}

const isAdmin = (role: string) => role === 'owner' || role === 'admin';

// back/apps/users/views_invitations.py: one answer for every link that does not open a pending invitation.
const INVITATION_NOT_FOUND = 'Esta invitación no existe o ya no sirve.';
const invitationGone = () =>
  new MockHttpError(410, { error: 'La invitación venció, ya se usó o se canceló. Pedile a quien te invitó que te mande una nueva.', code: 'invitation_invalid' });

export const workspacesHandlers = [
  route(contract.listWorkspaces, ({ db, user }) => ({
    data: db.workspaces
      .filter((workspace) => roleIn(db, workspace.id, user.id))
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map((workspace) => serializeWorkspace(db, workspace, user.id)),
  })),

  route(contract.createWorkspace, ({ db, user, body, now }) => {
    const created = new Date(now).toISOString();
    const name = cleanName(body.name);
    // Like the backend (views_workspaces.py · MAX_OWNED_WORKSPACES).
    if (db.workspaces.filter((item) => item.owner === user.id).length >= 10) {
      throw conflict('Llegaste al máximo de 10 espacios de trabajo propios. Borrá uno para crear otro.', 'workspace_limit');
    }
    const workspace: MockWorkspace = { id: newId(), name, owner: user.id, subscription_plan: PLAN_IDS.free, created_at: created, renews_at: null, pending_plan: null };
    db.workspaces.push(workspace);
    db.members.push({ id: newId(), workspace: workspace.id, user: user.id, email: user.email, role: 'admin', status: 'active', joined_at: created, invited_by: null, token: null });
    // Empty, as the backend creates it (apps/payments/signals.py · create_workspace_wallet).
    db.wallets.push({ id: newId(), workspace: workspace.id, available_balance: 0, reserved_balance: 0, updated_at: created });
    for (const platform of PLATFORMS) {
      db.socialAccounts.push({
        workspace: workspace.id,
        platform,
        status: 'disconnected',
        username: null,
        connected_at: null,
        limits: { caption_max_chars: platform === 'YOUTUBE_SHORTS' ? 100 : 2200, hashtags_max: 30, max_duration_seconds: platform === 'TIKTOK' ? 600 : 90, aspect_ratios: ['9:16'] },
        tiktok: null,
      });
    }
    return { data: serializeWorkspace(db, workspace, user.id) };
  }),

  route(contract.getWorkspace, ({ db, user, params }) => {
    const { workspace } = workspaceFor(db, params.id, user.id);
    // Who to ask for access: the owner first, then the admins.
    const admins = db.members
      .filter((member) => member.workspace === workspace.id && member.status === 'active' && member.user !== null)
      .map((member) => ({ member, account: db.users.find((item) => item.id === member.user) }))
      .filter(({ member, account }) => account && (member.user === workspace.owner || member.role === 'admin'))
      .map(({ member, account }) => ({ id: member.user as string, name: account?.name || member.email, role: member.user === workspace.owner ? ('owner' as const) : ('admin' as const) }))
      .sort((a, b) => Number(b.role === 'owner') - Number(a.role === 'owner'));
    return { data: { ...serializeWorkspace(db, workspace, user.id), admins } };
  }),

  route(contract.updateWorkspace, ({ db, user, params, body }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw adminsOnly();
    workspace.name = cleanName(body.name);
    return { data: serializeWorkspace(db, workspace, user.id) };
  }),

  route(contract.deleteWorkspace, ({ db, user, params, body }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (role !== 'owner') throw ownerOnly();
    if (body.confirm_name !== workspace.name) throw badRequest({ confirm_name: ['El nombre no coincide.'] });
    // Like the backend, in its order: never the person's last workspace (first: waiting does not fix it),
    // never with work in progress, posts going out, tokens set aside, a payment waiting for its provider, or tokens left.
    if (!db.workspaces.some((item) => item.id !== workspace.id && roleIn(db, item.id, user.id))) {
      throw conflict('Es tu único espacio de trabajo y no se puede borrar. Creá otro primero, o sumate al de alguien más.', 'last_workspace');
    }
    if (db.projects.some((item) => item.workspace === workspace.id && ['uploaded', 'ingesting', 'rendering'].includes(item.status))) {
      throw conflict(
        'Hay proyectos procesándose o renderizándose en este espacio: esperá a que terminen para borrarlo. Si alguno quedó trabado, borrá ese proyecto primero.',
        'project_processing',
      );
    }
    if (db.posts.some((item) => item.workspace === workspace.id && (item.status === 'QUEUED' || item.status === 'PROCESSING'))) {
      throw conflict('Hay publicaciones saliendo a las redes en este momento: esperá a que terminen para borrar el espacio. Si alguna quedó trabada, se libera sola un rato después de su hora de publicación: probá de nuevo más tarde.', 'posts_in_flight');
    }
    const wallet = db.wallets.find((item) => item.workspace === workspace.id);
    if (wallet && wallet.reserved_balance > 0) {
      throw conflict('El espacio tiene tokens reservados que todavía no se liquidaron. Probá de nuevo en unos minutos.', 'reservation_open');
    }
    if (db.purchases.some((item) => item.workspace === workspace.id && item.status === 'pending')) {
      throw conflict('Hay un pago en curso: terminalo, o esperá a que venza (24 h después de abrirlo).', 'payment_pending');
    }
    if (wallet && wallet.available_balance > 0) {
      const left = String(wallet.available_balance);
      throw new MockHttpError(409, {
        error: `${wallet.available_balance === 1 ? 'Queda 1 token' : `Quedan ${left} tokens`} en este espacio y no se pueden recuperar después de borrarlo. Usalos o escribinos antes de borrarlo.`,
        code: 'balance_left',
        available_balance: left,
      });
    }
    db.workspaces = db.workspaces.filter((item) => item.id !== workspace.id);
    db.members = db.members.filter((item) => item.workspace !== workspace.id);
    db.projects = db.projects.filter((item) => item.workspace !== workspace.id);
    db.posts = db.posts.filter((item) => item.workspace !== workspace.id);
    db.brandKits = db.brandKits.filter((item) => item.workspace !== workspace.id);
    db.wallets = db.wallets.filter((item) => item.workspace !== workspace.id);
    db.transactions = db.transactions.filter((item) => item.workspace !== workspace.id);
    db.purchases = db.purchases.filter((item) => item.workspace !== workspace.id);
    for (const item of db.users) if (item.active_workspace_id === workspace.id) item.active_workspace_id = null;
    return { data: undefined };
  }),

  route(contract.transferWorkspace, ({ db, user, params, body, now }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (role !== 'owner') throw ownerOnly();
    if (body.new_owner_id === user.id) throw badRequest({ new_owner_id: ['Ya sos el dueño de este espacio.'] });
    const target = db.members.find((member) => member.workspace === workspace.id && member.user === body.new_owner_id && member.status === 'active');
    if (!target) throw badRequest({ new_owner_id: ['Tiene que ser un miembro activo del workspace.'] });
    const previous = db.members.find((member) => member.workspace === workspace.id && member.user === user.id);
    if (previous) previous.role = 'admin';
    // Without a row of their own, the previous owner needs a free seat to stay as Admin (the backend's 400).
    else if (seats(db, workspace.id).used >= seats(db, workspace.id).max) {
      throw badRequest({ detail: 'No quedan lugares en el plan para que sigas como admin después de transferir. Quitá a un miembro o cancelá una invitación, y probá de nuevo.' });
    } else {
      db.members.push({ id: newId(), workspace: workspace.id, user: user.id, email: user.email, role: 'admin', status: 'active', joined_at: new Date(now).toISOString(), invited_by: null, token: null });
    }
    target.role = 'admin';
    workspace.owner = body.new_owner_id;
    return { data: serializeWorkspace(db, workspace, user.id) };
  }),

  route(contract.listMembers, ({ db, user, params }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw adminsOnly();
    const info = seats(db, workspace.id);
    return { data: { members: info.members.map((member) => serializeMember(db, member)), max_members: info.max, seats_used: info.used } };
  }),

  route(contract.updateMember, ({ db, user, params, body }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw adminsOnly();
    const member = db.members.find((item) => item.id === params.memberId && item.workspace === workspace.id);
    if (!member) throw memberNotFound();
    if (member.user === workspace.owner) throw badRequest({ role: ['No se puede cambiar el rol del dueño.'] });
    member.role = body.role;
    return { data: serializeMember(db, member) };
  }),

  route(contract.removeMember, ({ db, user, params }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw adminsOnly();
    const member = db.members.find((item) => item.id === params.memberId && item.workspace === workspace.id);
    if (!member) throw memberNotFound();
    if (member.user === workspace.owner) throw badRequest({ detail: 'No se puede quitar al dueño del workspace.' });
    db.members = db.members.filter((item) => item.id !== member.id);
    db.invitations = db.invitations.filter((item) => item.member !== member.id);
    return { data: undefined };
  }),

  route(contract.inviteMembers, ({ db, user, params, body, now }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw forbidden();
    const info = seats(db, workspace.id);
    const emails = [...new Set(body.emails.map((email) => email.toLowerCase()))];
    if (emails.length > 10) throw badRequest({ emails: ['Podés invitar hasta 10 emails por vez.'] });
    const repeated = emails.find((email) => info.members.some((member) => member.email.toLowerCase() === email));
    if (repeated) throw badRequest({ emails: [`${repeated} ya es miembro o tiene una invitación pendiente.`] });
    // Like check_seat_limit: while a downgrade waits, the seats of the smaller plan count.
    const pending = workspace.pending_plan ? db.plans.find((plan) => plan.id === workspace.pending_plan?.id) : null;
    if (pending && workspace.pending_plan && pending.max_members < info.max && info.used + emails.length > pending.max_members) {
      const when = new Date(workspace.pending_plan.effective_at).toLocaleDateString('es-AR', { timeZone: 'America/Argentina/Buenos_Aires' });
      throw new MockHttpError(403, {
        error: `Tu plan pasa a '${pending.name}' el ${when} y ese plan permite ${pending.max_members} miembros. Cancelá el cambio de plan para invitar más gente.`,
        code: 'seats_exceeded',
      });
    }
    // And while a plan payment waits for its checkout: once confirmed, that plan applies at once.
    const paying = db.purchases.find((item) => item.workspace === workspace.id && item.status === 'pending' && item.plan_id);
    const payingPlan = paying ? db.plans.find((plan) => plan.id === paying.plan_id) : null;
    if (payingPlan && payingPlan.max_members < info.max && info.used + emails.length > payingPlan.max_members) {
      throw new MockHttpError(403, {
        error: `Estás pasando al plan '${payingPlan.name}', que permite ${payingPlan.max_members} miembros. Hay un pago en curso: terminalo, o elegí «Seguir con ${info.plan?.name ?? 'tu plan'}» en Planes para cancelarlo.`,
        code: 'seats_exceeded',
      });
    }
    if (info.used + emails.length > info.max) {
      const left = Math.max(0, info.max - info.used);
      throw new MockHttpError(403, {
        error: left === 0 ? 'No quedan lugares en tu plan.' : `Solo ${left === 1 ? 'queda 1 lugar' : `quedan ${left} lugares`} en tu plan.`,
        code: 'seats_exceeded',
      });
    }
    const invited = emails.map((email) => {
      const existing = db.users.find((item) => item.email.toLowerCase() === email);
      const member = {
        id: newId(),
        workspace: workspace.id,
        // Like the backend: a pending row never says whose account the email is.
        user: null,
        email,
        role: body.role,
        status: 'pending' as const,
        joined_at: new Date(now).toISOString(),
        invited_by: user.id,
        token: newId(),
      };
      db.members.push(member);
      db.invitations.push({ token: member.token, member: member.id, workspace_id: workspace.id, workspace_name: workspace.name, inviter_name: user.name, role: body.role, email, status: 'pending', has_account: Boolean(existing) });
      return serializeMember(db, member);
    });
    return { data: { invited } };
  }),

  route(contract.resendInvitation, ({ db, user, params }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw adminsOnly();
    const member = db.members.find((item) => item.id === params.memberId && item.workspace === workspace.id && item.status === 'pending');
    if (!member) throw notFound('La invitación ya no está pendiente.');
    // A new link: the one sent before stops working.
    const previous = db.invitations.find((item) => item.member === member.id);
    db.invitations = db.invitations.filter((item) => item.member !== member.id);
    member.token = newId();
    db.invitations.push({
      token: member.token,
      member: member.id,
      workspace_id: workspace.id,
      workspace_name: workspace.name,
      inviter_name: previous?.inviter_name ?? user.name,
      role: member.role,
      email: member.email,
      status: 'pending',
      has_account: db.users.some((item) => item.email.toLowerCase() === member.email.toLowerCase()),
    });
    return { data: undefined };
  }),

  route(contract.getInvitation, ({ db, params }) => {
    const invitation = db.invitations.find((item) => item.token === params.token);
    // Like the backend: expired, used and unknown links are the same 404.
    if (!invitation || invitation.status !== 'pending') throw notFound(INVITATION_NOT_FOUND);
    const { token, member, status, ...preview } = invitation;
    void token;
    void member;
    void status;
    return { data: { ...preview, status: 'pending' as const } };
  }),

  route(contract.acceptInvitation, ({ db, params, body, maybeUser, now }) => {
    const invitation = db.invitations.find((item) => item.token === params.token);
    const workspace = db.workspaces.find((item) => item.id === invitation?.workspace_id);
    if (!invitation || invitation.status !== 'pending' || !workspace) throw invitationGone();
    let account = maybeUser;
    let tokens: ReturnType<typeof mockTokens> | null = null;
    if (account) {
      // The invitation belongs to its email: no other account accepts it.
      if (account.email.toLowerCase() !== invitation.email.toLowerCase()) {
        throw new MockHttpError(403, {
          error: 'Esta invitación es para otro email. Cerrá sesión y entrá con la cuenta de ese email para aceptarla.',
          code: 'invitation_email_mismatch',
        });
      }
    } else {
      if (db.users.some((item) => item.email.toLowerCase() === invitation.email.toLowerCase())) {
        throw new MockHttpError(401, { detail: 'Ya existe una cuenta con el email de la invitación. Iniciá sesión con ese email para aceptarla.' });
      }
      if (!body.name?.trim()) throw badRequest({ name: ['Escribí tu nombre.'] });
      if (!body.password) throw badRequest({ password: ['Elegí una contraseña.'] });
      if (/^\d+$/.test(body.password)) throw badRequest({ password: ['La contraseña no puede ser solo números.'] });
      if (body.terms_accepted !== true) throw badRequest({ terms_accepted: ['Para crear la cuenta tenés que aceptar los términos.'] });
      account = {
        id: newId(),
        name: body.name,
        email: invitation.email,
        is_email_verified: true,
        tour_completed: true,
        terms_accepted_at: new Date(now).toISOString(),
        pending_email: null,
        onboarding_use_case: null,
        active_workspace_id: workspace.id,
        notification_preferences: { project_ready: true, render_finished: true, project_failed: true, post_failed: true, low_balance: true },
      };
      db.users.push(account);
      tokens = mockTokens(account.id);
    }
    const member = db.members.find((item) => item.id === invitation.member);
    if (member) {
      member.user = account.id;
      member.status = 'active';
      member.token = null;
    } else if (!db.members.some((item) => item.workspace === workspace.id && item.user === account.id)) {
      db.members.push({ id: newId(), workspace: workspace.id, user: account.id, email: account.email, role: invitation.role, status: 'active', joined_at: new Date(now).toISOString(), invited_by: workspace.owner, token: null });
    }
    account.active_workspace_id = workspace.id;
    invitation.status = 'used';
    return { data: { workspace_id: workspace.id, tokens } };
  }),
];
