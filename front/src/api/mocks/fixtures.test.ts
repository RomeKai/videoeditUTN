import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { Plan, Pricing, TokenPackages, Transaction, Wallet, Subscription } from '../contracts/billing';
import { BrandKit } from '../contracts/brandkits';
import { ClipPreview } from '../contracts/clips';
import { Notification } from '../contracts/notifications';
import { ActivityItem, Clip, KNOWN_PIPELINE_ERROR_CODES, PaperEditData, Project } from '../contracts/projects';
import { ScheduledPost, SocialAccount } from '../contracts/social';
import { Me, NotificationPreferences } from '../contracts/users';
import { InvitationPreview, Member, Workspace } from '../contracts/workspaces';
import { describeProjectStatus } from '@/lib/project-status';
import { ROLE_LABEL } from '@/lib/permissions';
import { SCENARIOS } from './scenarios';
import { PRICING, seedDb, TOKEN_PACKAGES } from './seed';
import { serializeClip, serializeMe, serializeMember, serializePost, serializeProject, serializeSubscription, serializeTransaction, serializeWallet, serializeWorkspace } from './serialize';
import { roleIn } from './sim';

const NOW = new Date('2026-11-24T17:30:00Z').getTime();

function check(schema: ZodTypeAny, value: unknown, what: string): void {
  const result = schema.safeParse(value);
  if (!result.success) {
    const issue = result.error.issues[0];
    throw new Error(`${what}: ${issue?.path.join('.')} · ${issue?.message}`);
  }
}

describe('fixtures: cada escenario cumple los esquemas del contrato', () => {
  it.each(SCENARIOS.map((scenario) => [scenario.id, scenario] as const))('%s', (_id, scenario) => {
    const db = seedDb({ scenario: scenario.id, ...scenario.options }, NOW);

    for (const plan of db.plans) check(Plan, plan, `plan ${plan.name}`);
    check(Pricing, PRICING, 'pricing');
    check(TokenPackages, TOKEN_PACKAGES, 'token packages');

    for (const user of db.users) {
      check(Me, serializeMe(db, user), `me ${user.email}`);
      check(NotificationPreferences, user.notification_preferences, `preferencias ${user.email}`);
    }
    for (const workspace of db.workspaces) {
      check(Workspace, serializeWorkspace(db, workspace, workspace.owner), `workspace ${workspace.name}`);
      check(Wallet, serializeWallet(db, workspace.id, NOW), `wallet ${workspace.name}`);
      check(Subscription, serializeSubscription(db, workspace), `suscripción ${workspace.name}`);
    }
    for (const member of db.members) check(Member, serializeMember(db, member), `miembro ${member.email}`);
    for (const transaction of db.transactions) check(Transaction, serializeTransaction(transaction), `movimiento ${transaction.description}`);
    for (const project of db.projects) {
      check(Project, serializeProject(db, project, { full: true }), `proyecto ${project.title}`);
      check(Project, serializeProject(db, project, { full: false }), `proyecto en lista ${project.title}`);
      for (const clip of project.clips) check(Clip, serializeClip(db, clip), `clip ${clip.title}`);
      const detail = serializeProject(db, project, { full: true });
      check(
        PaperEditData,
        { project_id: project.id, title: project.title, status: project.status, proxy_url: null, transcript: detail.transcript_data, ai_suggestions: detail.clips, metadata: project.metadata },
        `paper-edit ${project.title}`,
      );
    }
    for (const kit of db.brandKits) check(BrandKit, kit, `brand kit ${kit.name}`);
    for (const account of db.socialAccounts) {
      const { workspace, ...wire } = account;
      void workspace;
      check(SocialAccount, wire, `red ${account.platform}`);
    }
    for (const post of db.posts) check(ScheduledPost, serializePost(post), `publicación ${post.id}`);
    for (const notification of db.notifications) {
      const { workspace, user, ...wire } = notification;
      void workspace;
      void user;
      check(Notification, wire, `notificación ${notification.title}`);
    }
    for (const item of db.activity) {
      const { project, ...wire } = item;
      void project;
      check(ActivityItem, wire, `actividad ${item.description}`);
    }
    for (const invitation of db.invitations) {
      const { token, member, ...wire } = invitation;
      void token;
      void member;
      check(InvitationPreview, wire, `invitación ${invitation.token}`);
    }
    check(
      ClipPreview,
      { preview_id: db.plans[0]?.id, clip_id: db.plans[0]?.id, status: 'ready', url: 'mock://previews/p.mp4', expires_at: new Date(NOW).toISOString(), error_code: null, cost_tokens: 0, simplified: { height: 360, face_tracking: false, silence_removal: false } },
      'preview',
    );
  });
});

describe('fixtures: cubren los estados que describe el mapa', () => {
  const all = seedDb({ scenario: 'todos-los-estados', projects: 'all-states' }, NOW);

  it('hay un proyecto por cada etiqueta de la sección 3', () => {
    const labels = new Set(all.projects.map((project) => describeProjectStatus(project).label));
    expect([...labels].sort()).toEqual(
      [
        'En cola',
        'Preparando el video',
        'Transcribiendo',
        'Revisando el contenido',
        'Eligiendo los mejores momentos',
        'En espera',
        'Listo para revisar',
        'Renderizando',
        'Listo',
        'Listo con errores',
        'Error',
      ].sort(),
    );
  });

  it('hay un proyecto fallido por cada pipeline_error_code conocido, y uno desconocido', () => {
    const codes = new Set(all.projects.filter((project) => project.status === 'failed').map((project) => project.pipeline_error_code));
    for (const code of KNOWN_PIPELINE_ERROR_CODES) expect(codes).toContain(code);
    expect(codes).toContain('RuntimeError');
  });

  it('incluye video completo, origen por link, prompt aplicado y fallido, revisión sin sugerencias y devolución', () => {
    expect(all.projects.some((project) => project.project_type === 'single_edit' && project.status === 'completed')).toBe(true);
    // The whole video is one clip that spans the source (8:12 in the seeded one), not a 21 s cut.
    const whole = all.projects.filter((project) => project.project_type === 'single_edit' && project.clips.length > 0);
    for (const project of whole) {
      expect(project.clips).toHaveLength(1);
      expect([project.clips[0]?.start_time, project.clips[0]?.end_time]).toEqual([0, project.metadata.source_media?.duration_s]);
    }
    expect(whole.map((project) => project.clips[0]?.end_time)).toContain(492);
    expect(all.projects.some((project) => project.video_url !== null)).toBe(true);
    expect(all.projects.some((project) => project.metadata.pte?.status === 'applied')).toBe(true);
    expect(all.projects.some((project) => project.metadata.pte?.status === 'failed')).toBe(true);
    expect(all.projects.some((project) => project.status === 'awaiting_approval' && project.clips.length === 0)).toBe(true);
    expect(all.projects.some((project) => project.cost?.state === 'refunded')).toBe(true);
  });

  it('el escenario por defecto tiene clips, publicaciones y movimientos en todos sus estados', () => {
    const db = seedDb({ scenario: 'estudio-norte' }, NOW);
    const clipStatuses = new Set(db.projects.flatMap((project) => project.clips.map((clip) => clip.status)));
    expect([...clipStatuses].sort()).toEqual(['completed', 'draft', 'failed', 'published', 'rendering']);
    expect(new Set(db.posts.map((post) => post.status))).toEqual(new Set(['DRAFT', 'SCHEDULED', 'QUEUED', 'PROCESSING', 'PUBLISHED', 'FAILED']));
    expect(new Set(db.transactions.map((transaction) => transaction.kind))).toEqual(new Set(['purchase', 'reserved', 'charged', 'released', 'pending']));
    expect(db.notifications.some((notification) => notification.read_at === null)).toBe(true);
    expect(db.brandKits.filter((kit) => kit.is_default)).toHaveLength(1);
  });

  it('hay un escenario por rol y por plan, y los de listas vacías, saldo bajo y paginación', () => {
    const roleOf = (id: string) => {
      const scenario = SCENARIOS.find((item) => item.id === id);
      const db = seedDb({ scenario: id, ...scenario?.options }, NOW);
      const user = db.users.find((item) => item.id === db.sessionUserId);
      return user?.active_workspace_id ? roleIn(db, user.active_workspace_id, user.id) : null;
    };
    expect(roleOf('estudio-norte')).toBe('owner');
    expect(roleOf('rol-admin')).toBe('admin');
    expect(roleOf('rol-editor')).toBe('editor');
    expect(roleOf('rol-lector')).toBe('viewer');
    expect(Object.keys(ROLE_LABEL)).toEqual(['owner', 'admin', 'editor', 'viewer']);

    const planOf = (id: string) => {
      const scenario = SCENARIOS.find((item) => item.id === id);
      const db = seedDb({ scenario: id, ...scenario?.options }, NOW);
      return db.plans.find((plan) => plan.id === db.workspaces[0]?.subscription_plan)?.name;
    };
    expect([planOf('plan-gratis'), planOf('plan-creador'), planOf('estudio-norte'), planOf('plan-estudio')]).toEqual(['Gratis', 'Creador', 'Pro', 'Estudio']);

    const empty = seedDb({ scenario: 'workspace-vacio', projects: 'none', brandKits: false, social: {} }, NOW);
    expect(empty.projects).toHaveLength(0);
    expect(empty.brandKits).toHaveLength(0);
    expect(empty.posts).toHaveLength(0);
    expect(empty.notifications).toHaveLength(0);
    expect(empty.socialAccounts.every((account) => account.status === 'disconnected')).toBe(true);

    const low = seedDb({ scenario: 'saldo-bajo', balance: 120 }, NOW);
    const wallet = Wallet.parse(serializeWallet(low, low.workspaces[0]?.id ?? '', NOW));
    expect(wallet.available_balance).toBeLessThan(wallet.low_balance_threshold);

    expect(seedDb({ scenario: 'muchos-proyectos', projects: 'many' }, NOW).projects.length).toBeGreaterThan(50);
    expect(seedDb({ scenario: 'sin-sesion', as: null }, NOW).sessionUserId).toBeNull();
  });
});
