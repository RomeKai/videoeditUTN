import * as contract from '../../contracts/workspaces';
import { newId, PLATFORMS, type MockDb, type MockWorkspace } from '../db';
import { badRequest, forbidden, MockHttpError, notFound, route } from '../http';
import { PLAN_IDS } from '../seed';
import { planOf, serializeMember, serializeWorkspace } from '../serialize';
import { roleIn } from '../sim';
import { mockTokens } from '../state';

function workspaceFor(db: MockDb, id: string | undefined, userId: string): { workspace: MockWorkspace; role: NonNullable<ReturnType<typeof roleIn>> } {
  const workspace = db.workspaces.find((item) => item.id === id);
  const role = workspace ? roleIn(db, workspace.id, userId) : null;
  if (!workspace || !role) throw notFound();
  return { workspace, role };
}

function seats(db: MockDb, workspaceId: string) {
  const plan = planOf(db, workspaceId);
  const members = db.members.filter((member) => member.workspace === workspaceId);
  // A pending invitation takes a seat (map 9.1.8).
  return { members, max: plan?.max_members ?? 1, used: members.length };
}

const isAdmin = (role: string) => role === 'owner' || role === 'admin';

export const workspacesHandlers = [
  route(contract.listWorkspaces, ({ db, user }) => ({
    data: db.workspaces.filter((workspace) => roleIn(db, workspace.id, user.id)).map((workspace) => serializeWorkspace(db, workspace, user.id)),
  })),

  route(contract.createWorkspace, ({ db, user, body, now }) => {
    const created = new Date(now).toISOString();
    const workspace: MockWorkspace = { id: newId(), name: body.name, owner: user.id, subscription_plan: PLAN_IDS.free, created_at: created, renews_at: null, pending_plan: null };
    db.workspaces.push(workspace);
    db.members.push({ id: newId(), workspace: workspace.id, user: user.id, email: user.email, role: 'admin', status: 'active', joined_at: created, invited_by: null, token: null });
    db.wallets.push({ id: newId(), workspace: workspace.id, available_balance: 300, reserved_balance: 0, updated_at: created });
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
      .map(({ member, account }) => ({ id: member.user as string, name: account?.name ?? member.email, role: member.user === workspace.owner ? ('owner' as const) : ('admin' as const) }))
      .sort((a, b) => Number(b.role === 'owner') - Number(a.role === 'owner'));
    return { data: { ...serializeWorkspace(db, workspace, user.id), admins } };
  }),

  route(contract.updateWorkspace, ({ db, user, params, body }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw forbidden();
    workspace.name = body.name;
    return { data: serializeWorkspace(db, workspace, user.id) };
  }),

  route(contract.deleteWorkspace, ({ db, user, params, body }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (role !== 'owner') throw forbidden();
    if (body.confirm_name !== workspace.name) throw badRequest({ confirm_name: ['El nombre no coincide.'] });
    db.workspaces = db.workspaces.filter((item) => item.id !== workspace.id);
    db.members = db.members.filter((item) => item.workspace !== workspace.id);
    db.projects = db.projects.filter((item) => item.workspace !== workspace.id);
    db.posts = db.posts.filter((item) => item.workspace !== workspace.id);
    db.brandKits = db.brandKits.filter((item) => item.workspace !== workspace.id);
    for (const item of db.users) if (item.active_workspace_id === workspace.id) item.active_workspace_id = null;
    return { data: undefined };
  }),

  route(contract.transferWorkspace, ({ db, user, params, body }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (role !== 'owner') throw forbidden();
    const target = db.members.find((member) => member.workspace === workspace.id && member.user === body.new_owner_id && member.status === 'active');
    if (!target) throw badRequest({ new_owner_id: ['Tiene que ser un miembro activo del workspace.'] });
    const previous = db.members.find((member) => member.workspace === workspace.id && member.user === user.id);
    if (previous) previous.role = 'admin';
    target.role = 'admin';
    workspace.owner = body.new_owner_id;
    return { data: serializeWorkspace(db, workspace, user.id) };
  }),

  route(contract.listMembers, ({ db, user, params }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw forbidden();
    const info = seats(db, workspace.id);
    return { data: { members: info.members.map((member) => serializeMember(db, member)), max_members: info.max, seats_used: info.used } };
  }),

  route(contract.updateMember, ({ db, user, params, body }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw forbidden();
    const member = db.members.find((item) => item.id === params.memberId && item.workspace === workspace.id);
    if (!member) throw notFound();
    if (member.user === workspace.owner) throw badRequest({ role: ['No se puede cambiar el rol del dueño.'] });
    member.role = body.role;
    return { data: serializeMember(db, member) };
  }),

  route(contract.removeMember, ({ db, user, params }) => {
    const { workspace, role } = workspaceFor(db, params.id, user.id);
    if (!isAdmin(role)) throw forbidden();
    const member = db.members.find((item) => item.id === params.memberId && item.workspace === workspace.id);
    if (!member) throw notFound();
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
    const repeated = emails.find((email) => info.members.some((member) => member.email.toLowerCase() === email));
    if (repeated) throw badRequest({ emails: [`${repeated} ya es miembro o tiene una invitación pendiente.`] });
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
        user: existing?.id ?? null,
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
    if (!isAdmin(role)) throw forbidden();
    const member = db.members.find((item) => item.id === params.memberId && item.workspace === workspace.id && item.status === 'pending');
    if (!member) throw notFound('La invitación ya no está pendiente.');
    return { data: undefined };
  }),

  route(contract.getInvitation, ({ db, params }) => {
    const invitation = db.invitations.find((item) => item.token === params.token);
    if (!invitation) throw notFound('Esta invitación no existe.');
    const { token, member, ...preview } = invitation;
    void token;
    void member;
    return { data: preview };
  }),

  route(contract.acceptInvitation, ({ db, params, body, maybeUser, now }) => {
    const invitation = db.invitations.find((item) => item.token === params.token);
    if (!invitation) throw notFound('Esta invitación no existe.');
    if (invitation.status !== 'pending') throw new MockHttpError(410, { error: 'La invitación venció o ya se usó.', code: invitation.status });
    const workspace = db.workspaces.find((item) => item.id === invitation.workspace_id);
    if (!workspace) throw notFound();
    let account = maybeUser;
    let tokens: ReturnType<typeof mockTokens> | null = null;
    if (!account) {
      if (invitation.has_account) throw new MockHttpError(401, { detail: 'Iniciá sesión para aceptar la invitación.' });
      if (!body.name) throw badRequest({ name: ['Este campo es obligatorio.'] });
      if (!body.password) throw badRequest({ password: ['Este campo es obligatorio.'] });
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
