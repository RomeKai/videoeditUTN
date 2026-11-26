// @vitest-environment node
// Node environment: jsdom's FormData and File do not travel through Node's fetch, and the mocks fall back to memory without localStorage.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { apiCall } from '../client';
import { ALL_ENDPOINTS } from '../contracts';
import * as auth from '../contracts/auth';
import * as billing from '../contracts/billing';
import * as brandKits from '../contracts/brandkits';
import * as clips from '../contracts/clips';
import * as notifications from '../contracts/notifications';
import * as projects from '../contracts/projects';
import * as social from '../contracts/social';
import * as system from '../contracts/system';
import * as users from '../contracts/users';
import * as workspaces from '../contracts/workspaces';
import { approveErrorKind } from '@/lib/error-messages';
import { ApiError, SessionExpiredError } from '../errors';
import { API_URL } from '../config';
import { setActiveWorkspaceId, setTokens } from '../session';
import { mockedEndpointIds } from './http';
import { USER_IDS, WORKSPACE_IDS } from './seed';
import { server } from './server';
import { SIM, tick } from './sim';
import { applyScenario, loadDb, mockTokens, saveDb } from './state';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function signIn(scenario: string, user: keyof typeof USER_IDS | null = 'lucia') {
  applyScenario(scenario);
  if (user) {
    setTokens(mockTokens(USER_IDS[user]));
    setActiveWorkspaceId(user === 'nuevo' ? WORKSPACE_IDS.nuevo : WORKSPACE_IDS.estudio);
  } else {
    setTokens(null);
  }
}

/** Moves the simulated pipeline forward without waiting. */
function advance(ms: number) {
  const db = loadDb();
  for (const project of db.projects) if (project._sim) project._sim.startedAt -= ms;
  for (const post of db.posts) if (post._sim) post._sim.startedAt -= ms;
  for (const purchase of db.purchases) if (purchase._settleAt) purchase._settleAt -= ms;
  tick(db, Date.now());
  saveDb(db);
}

const fail = async (promise: Promise<unknown>): Promise<ApiError> => {
  const error = await promise.catch((reason: unknown) => reason);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
};

describe('mocks', () => {
  beforeEach(() => signIn('estudio-norte'));

  it('hay un handler por cada endpoint del contrato', () => {
    expect([...mockedEndpointIds].sort()).toEqual(ALL_ENDPOINTS.map((endpoint) => endpoint.id).sort());
  });

  it('las lecturas del escenario por defecto cumplen el contrato', async () => {
    const me = await apiCall(users.getMe);
    expect(me.name).toBe('Lucía Fernández');
    expect(me.workspaces.map((workspace) => workspace.role)).toEqual(['owner', 'editor']);
    const workspaceId = me.active_workspace_id as string;

    await apiCall(users.getNotificationPreferences);
    await apiCall(workspaces.listWorkspaces);
    await apiCall(workspaces.getWorkspace, { params: { id: workspaceId } });
    const members = await apiCall(workspaces.listMembers, { params: { id: workspaceId } });
    expect(members.seats_used).toBe(4);
    expect(members.max_members).toBe(5);
    expect((await apiCall(billing.listPlans)).map((plan) => plan.name)).toEqual(['Gratis', 'Creador', 'Pro', 'Estudio']);
    expect((await apiCall(billing.getPricing)).base_cost_per_second_720p).toBe(0.5);
    expect((await apiCall(billing.getSubscription)).plan.name).toBe('Pro');
    const wallet = await apiCall(billing.getWallet);
    expect(wallet.available_balance).toBe(4860);
    expect(wallet.reservations.length).toBeGreaterThan(0);
    await apiCall(billing.getUsage, { query: {} });
    expect((await apiCall(billing.listTransactions, { query: { kind: 'purchase' } })).results.every((item) => item.kind === 'purchase')).toBe(true);
    expect(await apiCall(billing.exportTransactions, { query: {} })).toContain('fecha,concepto,tipo,monto');
    expect((await apiCall(billing.listTokenPackages)).packages).toHaveLength(3);

    const list = await apiCall(projects.listProjects, { query: { status_group: 'to_review' } });
    expect(list.results).toHaveLength(1);
    expect(list.status_counts?.all).toBe(8);
    const project = list.results[0];
    if (!project) throw new Error('falta el proyecto para revisar');
    const detail = await apiCall(projects.getProject, { params: { id: project.id } });
    expect(detail.transcript_data?.length).toBeGreaterThan(100);
    const paper = await apiCall(projects.getPaperEditData, { params: { id: project.id } });
    expect(paper.ai_suggestions).toHaveLength(9);
    expect((await apiCall(projects.getProjectActivity, { params: { id: project.id } })).length).toBeGreaterThan(0);

    const clip = paper.ai_suggestions[0];
    if (!clip) throw new Error('falta el clip');
    expect((await apiCall(clips.getClip, { params: { id: clip.id } })).title).toBe(clip.title);
    expect((await apiCall(brandKits.listBrandKits)).filter((kit) => kit.is_default)).toHaveLength(1);
    expect((await apiCall(social.listSocialAccounts)).map((account) => account.status)).toEqual(['connected', 'connected', 'disconnected']);
    const posts = await apiCall(social.listPosts, { query: {} });
    expect(posts.results.length).toBe(8);
    expect((await apiCall(notifications.listNotifications)).unread_count).toBe(2);
    expect((await apiCall(system.getSystemStatus)).maintenance).toBe(false);
    expect((await apiCall(workspaces.getInvitation, { params: { token: 'vencida' } })).status).toBe('expired');
  });

  it('crea un proyecto, lo procesa, se aprueba, se renderiza y se cobra', async () => {
    const before = await apiCall(billing.getWallet);
    const file = new File([new Uint8Array(1024)], 'clase-10min.mp4', { type: 'video/mp4' });
    const created = await apiCall(projects.createProject, { body: { title: 'Clase nueva', source_file: file, max_clips: 3, render_resolution: '720p' } });
    // 600 s × 0,5 × 1 with the 10 % discount of the Pro plan.
    expect(created.cost_details.tokens_reserved).toBe(270);
    expect(created.status).toBe('uploaded');
    expect((await apiCall(billing.getWallet)).available_balance).toBe(before.available_balance - 270);

    advance(SIM.queueMs + SIM.stageMs * 1.5);
    let project = await apiCall(projects.getProject, { params: { id: created.id } });
    expect(project.status).toBe('ingesting');
    expect(project.pipeline_stage).toBe('audio_extracted');

    advance(SIM.stageMs * 4);
    project = await apiCall(projects.getProject, { params: { id: created.id } });
    expect(project.status).toBe('awaiting_approval');
    expect(project.clips).toHaveLength(3);

    const approved = await apiCall(projects.approvePaperEdit, {
      params: { id: created.id },
      body: { approved_segments: project.clips.slice(0, 2).map((clip) => ({ start: clip.start_time, end: clip.end_time, clip_id: clip.id })) },
    });
    expect(approved.final_cost).toBeGreaterThan(0);
    // A second approval finds the project already rendering: the backend answers 400 with the status, not 409.
    const again = await fail(apiCall(projects.approvePaperEdit, { params: { id: created.id }, body: { approved_segments: [{ start: 0, end: 10 }] } }));
    expect(again.status).toBe(400);
    expect(approveErrorKind(again)?.kind).toBe('not_awaiting_approval');

    advance(SIM.clipMs * 3);
    project = await apiCall(projects.getProject, { params: { id: created.id } });
    expect(project.status).toBe('completed');
    expect(project.clips.map((clip) => clip.status)).toEqual(['completed', 'completed']);
    expect(project.cost?.state).toBe('charged');
    const after = await apiCall(billing.getWallet);
    expect(after.reserved_balance).toBe(before.reserved_balance);
    expect(after.available_balance).toBe(before.available_balance - 270 - approved.final_cost);
  });

  it('los errores de subida y del pipeline llegan como en el backend', async () => {
    const upload = (name: string, extra: Record<string, unknown> = {}) =>
      apiCall(projects.createProject, { body: { source_file: new File(['x'], name, { type: 'video/mp4' }), ...extra } });
    expect((await fail(upload('no-video.mp4'))).fieldErrors.source_file?.[0]).toMatch(/no es un video válido/);
    expect((await fail(upload('muy-chico.mp4'))).fieldErrors.source_file?.[0]).toMatch(/demasiado baja/);
    expect((await fail(upload('notas.txt'))).fieldErrors.source_file?.[0]).toMatch(/Solo se permiten archivos de video/);
    expect((await fail(upload('largo-20min.mp4', { project_type: 'single_edit' }))).fieldErrors.project_type?.[0]).toMatch(/hasta 10 min/);
    expect((await fail(apiCall(projects.createProject, { body: { video_url: 'https://youtu.be/abc', project_type: 'single_edit' } }))).fieldErrors.project_type?.[0]).toMatch(/subí el archivo/);
    expect((await fail(apiCall(projects.createProjectFromPrompt, { body: { video_url: 'https://youtu.be/abc', prompt: ' ' } }))).fieldErrors.prompt).toBeTruthy();

    const failing = await upload('sin-voz-5min.mp4');
    advance(SIM.queueMs + SIM.stageMs * 5);
    const project = await apiCall(projects.getProject, { params: { id: failing.id } });
    expect(project.status).toBe('failed');
    expect(project.pipeline_error_code).toBe('AINoSpeechDetectedError');
    expect(project.cost?.state).toBe('refunded');

    const waiting = await upload('demanda-5min.mp4');
    advance(SIM.queueMs + SIM.stageMs * 1.2);
    expect(Object.values((await apiCall(projects.getProject, { params: { id: waiting.id } })).pipeline_stage_status)).toContain('pending_capacity');
  });

  it('multipart: sin `add_subtitles` el proyecto queda con subtítulos (el serializer lo declara con default true); al crear, el cliente lo manda siempre', async () => {
    // The client fills the default, so a screen that says nothing gets subtitles.
    const viaClient = await apiCall(projects.createProject, { body: { video_url: 'https://youtu.be/abc' } });
    expect(viaClient.add_subtitles).toBe(true);
    const off = await apiCall(projects.createProject, { body: { video_url: 'https://youtu.be/abc', add_subtitles: false } });
    expect(off.add_subtitles).toBe(false);
    // A raw multipart form without the field, as any other client would send it.
    const form = new FormData();
    form.append('video_url', 'https://youtu.be/abc');
    const response = await fetch(`${API_URL}/api/v1/projects/`, { method: 'POST', body: form, headers: { Authorization: `Bearer ${mockTokens(USER_IDS.lucia).access}` } });
    expect(response.status).toBe(201);
    expect(((await response.json()) as { add_subtitles: boolean }).add_subtitles).toBe(true);
    // from-prompt leaves it out unless the user chose it, so the text can turn subtitles off.
    const prompted = await apiCall(projects.createProjectFromPrompt, { body: { video_url: 'https://youtu.be/abc', prompt: 'sin subtítulos' } });
    expect(prompted.metadata.pte_explicit ?? []).not.toContain('add_subtitles');
  });

  it('un 400 de validación no deja una reserva: se valida antes de reservar, como DRF', async () => {
    const before = await apiCall(billing.getWallet);
    const error = await fail(apiCall(projects.createProject, { body: { video_url: 'https://youtu.be/abc', selection_criteria: 'x'.repeat(301) } }));
    expect(error.status).toBe(400);
    expect(error.fieldErrors.selection_criteria?.[0]).toMatch(/más de 300 caracteres/);
    const after = await apiCall(billing.getWallet);
    expect(after.available_balance).toBe(before.available_balance);
    expect(after.reserved_balance).toBe(before.reserved_balance);
    expect(after.reservations).toHaveLength(before.reservations.length);
  });

  it('renderizar sin revisar: reserva el render; sin saldo o fuera del plan queda en revisión con el motivo', async () => {
    const upload = (name: string, extra: Record<string, unknown> = {}) =>
      apiCall(projects.createProject, { body: { source_file: new File(['x'], name, { type: 'video/mp4' }), auto_render_bypass: true, max_clips: 3, ...extra } });
    const paid = await upload('auto-10min.mp4');
    advance(SIM.queueMs + SIM.stageMs * 5);
    const rendering = await apiCall(projects.getProject, { params: { id: paid.id } });
    expect(rendering.status).toBe('rendering');
    expect(rendering.metadata.auto_render_reservation_id).toBeTruthy();
    expect(rendering.metadata.auto_render_clip_ids).toHaveLength(3);
    expect(rendering.metadata.auto_render_skipped).toBeUndefined();

    signIn('saldo-bajo');
    // 120 tokens pay the link (10) but not the render of the clips. Twenty clips, so that is true whatever
    // clips the mock suggests: it seeds them from the clock, and with 8 they were short enough to be paid
    // with the 110 tokens left about once in 70 runs (the project then went on to render).
    const poor = await apiCall(projects.createProject, { body: { video_url: 'https://youtu.be/abc', auto_render_bypass: true, max_clips: 20 } });
    advance(SIM.queueMs + SIM.stageMs * 5);
    const waiting = await apiCall(projects.getProject, { params: { id: poor.id } });
    expect(waiting.status).toBe('awaiting_approval');
    expect(waiting.metadata.auto_render_skipped).toBe('insufficient_funds');

    signIn('todos-los-estados');
    const seeded = (await apiCall(projects.listProjects, { query: { status_group: 'to_review', page_size: 100 } })).results;
    expect(seeded.map((project) => project.metadata.auto_render_skipped).filter(Boolean).sort()).toEqual(['insufficient_funds', 'plan_limit']);
  });

  it('aprobar fuera del plan responde el 400 del motor de precios', async () => {
    signIn('plan-gratis');
    const review = (await apiCall(projects.listProjects, { query: { status_group: 'to_review' } })).results[0];
    if (!review) throw new Error('falta el proyecto para revisar');
    // The free plan takes 10 minutes: approving more than that is a plan limit.
    const error = await fail(apiCall(projects.approvePaperEdit, { params: { id: review.id }, body: { approved_segments: [{ start: 0, end: 700 }] } }));
    expect(error.status).toBe(400);
    expect(error.message).toMatch(/^Error en el motor de precios/);
    expect(approveErrorKind(error)).toMatchObject({ kind: 'plan_limit', limit: 'duration' });
  });

  it('una invitación se acepta con la sesión iniciada (el token viaja aunque el endpoint sea público)', async () => {
    signIn('estudio-norte', 'nuevo');
    const accepted = await apiCall(workspaces.acceptInvitation, { params: { token: 'con-cuenta' }, body: {} });
    expect(accepted.tokens).toBeNull();
    expect((await apiCall(users.getMe)).workspaces.map((workspace) => workspace.name)).toContain('Estudio Norte');
    // Without a session the same invitation asks to sign in.
    signIn('estudio-norte', null);
    expect((await fail(apiCall(workspaces.acceptInvitation, { params: { token: 'con-cuenta' }, body: {} }))).status).toBe(401);
  });

  it('saldo insuficiente responde 402 con lo que hacía falta', async () => {
    signIn('saldo-bajo');
    const error = await fail(apiCall(projects.createProject, { body: { source_file: new File(['x'], 'clase-30min.mp4', { type: 'video/mp4' }) } }));
    expect(error.status).toBe(402);
    expect(error.isInsufficientFunds).toBe(true);
    expect(error.requiredTokens).toBe(810);
    // Nothing was created or reserved.
    expect((await apiCall(billing.getWallet)).available_balance).toBe(120);
  });

  it('el plan Gratis responde 403 por duración, resolución, SEO, programar y varias redes', async () => {
    signIn('plan-gratis');
    const upload = (name: string, extra: Record<string, unknown> = {}) =>
      apiCall(projects.createProject, { body: { source_file: new File(['x'], name, { type: 'video/mp4' }), ...extra } });
    const duration = await fail(upload('clase-30min.mp4'));
    expect(duration.status).toBe(403);
    expect(duration.isPlanLimit).toBe(true);
    expect((await fail(upload('corto-2min.mp4', { render_resolution: '4K' }))).message).toMatch(/does not support 4K/);
    expect((await fail(upload('corto-2min.mp4', { use_seo: true }))).message).toMatch(/SEO/);
    expect((await fail(apiCall(billing.estimateCost, { body: { source: 'file', duration_seconds: 1800 } }))).code).toBe('duration');

    const ready = (await apiCall(projects.listProjects, { query: { status_group: 'ready' } })).results.find((project) => project.project_type === 'repurpose');
    const clip = ready?.clips.find((item) => item.status === 'completed');
    if (!clip) throw new Error('falta un clip listo');
    const later = new Date(Date.now() + 86_400_000).toISOString();
    expect((await fail(apiCall(social.createPost, { body: { video_clip: clip.id, platform: 'INSTAGRAM_REELS', publish_at: later } }))).code).toBe('scheduling');
    const bulk = await fail(
      apiCall(social.createPostsBulk, {
        body: {
          posts: [
            { video_clip: clip.id, platform: 'INSTAGRAM_REELS' },
            { video_clip: clip.id, platform: 'TIKTOK', tiktok: { privacy_level: 'SELF_ONLY', allow_comment: false, allow_duet: false, allow_stitch: false, brand_organic: false, branded_content: false } },
          ],
        },
      }),
    );
    expect(bulk.code).toBe('crossposting');
  });

  it('los roles se aplican: el Lector no crea ni ve movimientos; el Editor no administra', async () => {
    signIn('rol-lector', 'diego');
    expect((await apiCall(users.getMe)).workspaces[0]?.role).toBe('viewer');
    const denied = await fail(apiCall(projects.createProject, { body: { video_url: 'https://youtu.be/abc' } }));
    expect(denied.status).toBe(403);
    expect(denied.isPlanLimit).toBe(false);
    expect((await fail(apiCall(billing.listTransactions, { query: {} }))).status).toBe(403);
    await expect(apiCall(billing.getWallet)).resolves.toBeTruthy();

    signIn('rol-editor', 'carla');
    expect((await fail(apiCall(workspaces.listMembers, { params: { id: WORKSPACE_IDS.estudio } }))).status).toBe(403);
    expect((await fail(apiCall(social.connectSocialAccount, { body: { platform: 'YOUTUBE_SHORTS' } }))).status).toBe(403);
    const mine = (await apiCall(projects.listProjects, { query: {} })).results;
    const foreign = mine.find((project) => project.uploaded_by !== USER_IDS.carla);
    const own = mine.find((project) => project.uploaded_by === USER_IDS.carla);
    if (!foreign || !own) throw new Error('faltan proyectos');
    // An Editor deletes only their own projects.
    expect((await fail(apiCall(projects.deleteProject, { params: { id: foreign.id } }))).status).toBe(403);
    await expect(apiCall(projects.deleteProject, { params: { id: own.id } })).resolves.toBeUndefined();
  });

  it('publicar con la misma `Idempotency-Key` no publica dos veces: responde lo que creó el primer pedido', async () => {
    const clip = (await apiCall(projects.listProjects, { query: { status_group: 'ready' } })).results.flatMap((project) => project.clips).find((item) => item.status === 'completed');
    if (!clip) throw new Error('falta un clip listo');
    const count = () => loadDb().posts.filter((post) => post.video_clip === clip.id && post.platform === 'INSTAGRAM_REELS').length;
    const before = count();
    const body = { video_clip: clip.id, platform: 'INSTAGRAM_REELS' as const, caption: 'Una sola vez' };
    const first = await apiCall(social.createPost, { body, idempotencyKey: 'envio-1' });
    // The answer was lost and the person pressed again: same submit, same key.
    const again = await apiCall(social.createPost, { body, idempotencyKey: 'envio-1' });
    expect(again.id).toBe(first.id);
    expect(count()).toBe(before + 1);
    // Another submit is another publication.
    const other = await apiCall(social.createPost, { body, idempotencyKey: 'envio-2' });
    expect(other.id).not.toBe(first.id);
    expect(count()).toBe(before + 2);
    // The batch takes it too.
    const batch = await apiCall(social.createPostsBulk, { body: { posts: [body] }, idempotencyKey: 'lote-1' });
    const batchAgain = await apiCall(social.createPostsBulk, { body: { posts: [body] }, idempotencyKey: 'lote-1' });
    expect(batchAgain.posts.map((post) => post.id)).toEqual(batch.posts.map((post) => post.id));
    expect(count()).toBe(before + 3);
    expect(social.createPost.idempotencyKey).toBe(true);
    expect(social.createPostsBulk.idempotencyKey).toBe(true);
  });

  it('publica un clip, la publicación sale, y una reprogramación tardía responde 409', async () => {
    const ready = (await apiCall(projects.listProjects, { query: { status_group: 'ready' } })).results.find((project) => project.project_type === 'repurpose' && project.status === 'completed');
    const clip = ready?.clips.find((item) => item.status === 'completed');
    if (!clip) throw new Error('falta un clip listo');
    expect((await fail(apiCall(social.createPost, { body: { video_clip: clip.id, platform: 'YOUTUBE_SHORTS' } }))).code).toBe('account_not_connected');
    expect((await fail(apiCall(social.createPost, { body: { video_clip: clip.id, platform: 'TIKTOK' } }))).fieldErrors.tiktok).toBeTruthy();

    const post = await apiCall(social.createPost, { body: { video_clip: clip.id, platform: 'INSTAGRAM_REELS', caption: 'Mirá esto' } });
    expect(post.status).toBe('QUEUED');
    expect(post.seo_status).toBe('pending');
    expect((await fail(apiCall(social.updatePost, { params: { id: post.id }, body: { caption: 'Tarde' } }))).status).toBe(409);
    advance(SIM.postQueuedMs + SIM.postProcessingMs + 1000);
    const published = await apiCall(social.getPost, { params: { id: post.id } });
    expect(published.status).toBe('PUBLISHED');
    expect(published.post_url).toMatch(/^https:/);
    expect((await apiCall(clips.getClip, { params: { id: clip.id } })).status).toBe('published');
  });

  it('compra tokens: pendiente, acreditada, y rechazada en el escenario de pago rechazado', async () => {
    const purchase = await apiCall(billing.createTokenPurchase, { body: { package_id: 'pack-1000', return_url: 'http://localhost:3000/uso/comprar/resultado' } });
    expect(purchase.checkout_url).toContain('/dev/checkout?compra=');
    expect((await apiCall(billing.getTokenPurchase, { params: { id: purchase.id } })).status).toBe('pending');
    advance(SIM.purchaseMs + 500);
    const settled = await apiCall(billing.getTokenPurchase, { params: { id: purchase.id } });
    expect(settled.status).toBe('approved');
    expect(settled.balance_after).toBe(5860);

    signIn('pago-rechazado');
    const rejected = await apiCall(billing.createTokenPurchase, { body: { custom_tokens: 500, return_url: 'http://localhost:3000/uso/comprar/resultado' } });
    await apiCall(billing.getTokenPurchase, { params: { id: rejected.id } });
    advance(SIM.purchaseMs + 500);
    expect((await apiCall(billing.getTokenPurchase, { params: { id: rejected.id } })).status).toBe('rejected');
  });

  it('la sesión: login, credenciales incorrectas, email sin verificar y sesión vencida', async () => {
    signIn('sin-sesion', null);
    expect((await fail(apiCall(auth.login, { body: { email: 'lucia@estudionorte.com', password: 'incorrecta' } }))).status).toBe(401);
    expect((await fail(apiCall(auth.login, { body: { email: 'nadie@ejemplo.com', password: 'x' } }))).status).toBe(401);
    const tokens = await apiCall(auth.login, { body: { email: 'lucia@estudionorte.com', password: 'cualquiera' } });
    setTokens(tokens);
    expect((await apiCall(users.getMe)).email).toBe('lucia@estudionorte.com');

    signIn('email-sin-verificar', null);
    expect((await fail(apiCall(auth.login, { body: { email: 'lucia@estudionorte.com', password: 'x' } }))).code).toBe('email_not_verified');

    signIn('sesion-vencida');
    await expect(apiCall(users.getMe)).rejects.toBeInstanceOf(SessionExpiredError);
  });

  it('cambiar la contraseña devuelve un par nuevo que sirve para seguir con sesión', async () => {
    expect((await fail(apiCall(users.changePassword, { body: { current_password: 'incorrecta', new_password: 'unaclavelarga' } }))).fieldErrors.current_password).toEqual(['La contraseña actual no coincide.']);
    const tokens = await apiCall(users.changePassword, { body: { current_password: 'laDeSiempre', new_password: 'unaclavelarga' } });
    expect(auth.TokenPair.safeParse(tokens).success).toBe(true);
    setTokens(tokens);
    expect((await apiCall(users.getMe)).email).toBe('lucia@estudionorte.com');
  });

  it('miembros: una invitación pendiente ocupa un lugar y el plan corta al llegar al límite', async () => {
    const id = WORKSPACE_IDS.estudio;
    const invited = await apiCall(workspaces.inviteMembers, { params: { id }, body: { emails: ['juan@estudionorte.com'], role: 'editor' } });
    expect(invited.invited[0]?.status).toBe('pending');
    expect((await apiCall(workspaces.listMembers, { params: { id } })).seats_used).toBe(5);
    expect((await fail(apiCall(workspaces.inviteMembers, { params: { id }, body: { emails: ['otra@estudionorte.com'], role: 'viewer' } }))).code).toBe('seats_exceeded');
  });

  it('brand kits: un solo default por workspace, duplicar y borrar', async () => {
    const kits = await apiCall(brandKits.listBrandKits);
    const other = kits.find((kit) => !kit.is_default);
    if (!other) throw new Error('falta un kit');
    await apiCall(brandKits.updateBrandKit, { params: { id: other.id }, body: { is_default: true } });
    expect((await apiCall(brandKits.listBrandKits)).filter((kit) => kit.is_default).map((kit) => kit.id)).toEqual([other.id]);
    const copy = await apiCall(brandKits.duplicateBrandKit, { params: { id: other.id } });
    expect(copy.name).toBe(`Copia de ${other.name}`);
    expect(copy.is_default).toBe(false);
    await apiCall(brandKits.deleteBrandKit, { params: { id: copy.id } });
    expect((await fail(apiCall(brandKits.createBrandKit, { body: { name: 'Malo', primary_color: '#FFFFFF' } }).then(() => apiCall(brandKits.getBrandKit, { params: { id: copy.id } })))).status).toBe(404);
  });

  it('el servidor caído responde 500 en las lecturas y el mantenimiento se avisa', async () => {
    signIn('servidor-caido');
    expect((await fail(apiCall(projects.listProjects, { query: {} }))).status).toBe(500);
    signIn('mantenimiento');
    expect((await apiCall(system.getSystemStatus)).maintenance_window).not.toBeNull();
    // Everything else answers 503, which is what puts the app on the maintenance page.
    expect((await fail(apiCall(projects.listProjects, { query: {} }))).status).toBe(503);
  });
});
