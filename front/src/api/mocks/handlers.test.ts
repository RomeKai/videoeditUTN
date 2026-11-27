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
import { reservationStateOf, serveActivity, SIM, tick } from './sim';
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
    expect(members.max_members).toBe(10);
    expect((await apiCall(billing.listPlans)).map((plan) => plan.name)).toEqual(['Gratis', 'Creador', 'Pro', 'Estudio']);
    expect((await apiCall(billing.getPricing)).base_cost_per_second_720p).toBe(0.5);
    expect((await apiCall(billing.getSubscription)).plan.name).toBe('Pro');
    const wallet = await apiCall(billing.getWallet);
    expect(wallet.available_balance).toBe(4860);
    expect(wallet.reservations.length).toBeGreaterThan(0);
    await apiCall(billing.getUsage, { query: {} });
    expect((await apiCall(billing.listTransactions, { query: { kind: 'purchase' } })).results.every((item) => item.kind === 'purchase')).toBe(true);
    expect(await apiCall(billing.exportTransactions, { query: {} })).toContain('fecha,concepto,proyecto,tipo,monto');
    expect((await apiCall(billing.listTokenPackages)).packages).toHaveLength(3);

    const list = await apiCall(projects.listProjects, { query: { status_group: 'to_review' } });
    expect(list.results).toHaveLength(1);
    expect(list.status_counts).toEqual({ all: 8, in_progress: 3, to_review: 1, ready: 3, failed: 1 });
    // As the backend: the counts follow the search, never the filter or the page; a group that does not exist is a 400.
    const searched = await apiCall(projects.listProjects, { query: { search: '  VLOG ', status_group: 'failed', page_size: 1 } });
    expect(searched).toMatchObject({ count: 0, results: [], status_counts: { all: 1, ready: 1, failed: 0 } });
    // A row is the project without the transcript and the selection log (plain fetch: the client would drop
    // what the schema does not name); the detail keeps them.
    const rawList = (await (await fetch(`${API_URL}/api/v1/projects/`, { headers: { Authorization: `Bearer ${mockTokens(USER_IDS.lucia).access}` } })).json()) as {
      results: Array<{ id: string; metadata: Record<string, unknown> }>;
    };
    expect(rawList.results).toHaveLength(8);
    for (const row of rawList.results) {
      expect(row).not.toHaveProperty('transcript_data');
      expect(row).not.toHaveProperty('ai_rationale_log');
      expect(row.metadata).not.toHaveProperty('full_text');
    }
    const rawDetail = (await (await fetch(`${API_URL}/api/v1/projects/${list.results[0]?.id}/`, { headers: { Authorization: `Bearer ${mockTokens(USER_IDS.lucia).access}` } })).json()) as Record<string, unknown>;
    expect(rawDetail).toHaveProperty('transcript_data');
    expect(rawDetail).toHaveProperty('ai_rationale_log');
    // (Plain fetch again: the client does not let such a query out.)
    for (const query of ['status_group=archived', 'ordering=-title']) {
      const response = await fetch(`${API_URL}/api/v1/projects/?${query}`, { headers: { Authorization: `Bearer ${mockTokens(USER_IDS.lucia).access}` } });
      expect([query, response.status]).toEqual([query, 400]);
    }
    expect((await fail(apiCall(projects.listProjects, { query: { page: 9 } }))).data).toEqual({ detail: 'Página inválida.' });
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
    expect((await apiCall(workspaces.getInvitation, { body: { token: 'valida' } })).status).toBe('pending');
    // Like the backend: an expired, a used and an unknown link are the same 404.
    for (const token of ['vencida', 'usada', 'no-existe']) {
      const gone = await fail(apiCall(workspaces.getInvitation, { body: { token } }));
      expect([gone.status, gone.message]).toEqual([404, 'Esta invitación no existe o ya no sirve.']);
    }
  });

  it('notificaciones: leer una o todas solo toca las propias del workspace activo, como el backend', async () => {
    const before = await apiCall(notifications.listNotifications);
    const first = before.results.find((item) => item.read_at === null);
    if (!first) throw new Error('falta una notificación sin leer');

    // From another workspace of the same person it does not exist, and "read all" there leaves it alone.
    setActiveWorkspaceId(WORKSPACE_IDS.canal);
    expect((await fail(apiCall(notifications.markNotificationRead, { params: { id: first.id } }))).status).toBe(404);
    await apiCall(notifications.markAllNotificationsRead);
    setActiveWorkspaceId(WORKSPACE_IDS.estudio);
    expect((await apiCall(notifications.listNotifications)).unread_count).toBe(before.unread_count);

    const read = await apiCall(notifications.markNotificationRead, { params: { id: first.id } });
    expect(read.read_at).not.toBeNull();
    // Reading it again keeps the first time.
    expect((await apiCall(notifications.markNotificationRead, { params: { id: first.id } })).read_at).toBe(read.read_at);
    expect((await apiCall(notifications.listNotifications)).unread_count).toBe(before.unread_count - 1);
    await apiCall(notifications.markAllNotificationsRead);
    expect((await apiCall(notifications.listNotifications)).unread_count).toBe(0);
  });

  it('crea un proyecto, lo procesa, se aprueba, se renderiza y se cobra', async () => {
    const before = await apiCall(billing.getWallet);
    const file = new File([new Uint8Array(1024)], 'clase-10min.mp4', { type: 'video/mp4' });
    const created = await apiCall(projects.createProject, { body: { title: 'Clase nueva', source_file: file, max_clips: 3, render_resolution: '720p' } });
    // 600 s × 0,5 × 1 with the 10 % discount of the Pro plan.
    expect(created.cost_details.tokens_reserved).toBe(270);
    expect(created.status).toBe('uploaded');
    expect((await apiCall(billing.getWallet)).available_balance).toBe(before.available_balance - 270);
    /** [type, amount, reservation_state] of the token items of the activity, oldest first. */
    const movements = async () =>
      (await apiCall(projects.getProjectActivity, { params: { id: created.id } }))
        .filter((item) => item.amount !== null)
        .reverse()
        .map((item) => [item.type, item.amount, item.reservation_state]);
    expect(await movements()).toEqual([['tokens_reserved', -270, 'reserved']]);

    advance(SIM.queueMs + SIM.stageMs * 1.5);
    let project = await apiCall(projects.getProject, { params: { id: created.id } });
    expect(project.status).toBe('ingesting');
    expect(project.pipeline_stage).toBe('audio_extracted');
    // The stage that is running has its start and no duration; the finished one, both.
    expect(project.stage_timings?.transcribed).toMatchObject({ finished_at: null, duration_s: null });
    expect(project.stage_timings?.transcribed?.started_at).toEqual(expect.any(String));
    expect(project.stage_timings?.audio_extracted?.duration_s).toBe(SIM.stageMs / 1000);

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
    // Each reservation says how it ended, read from its transaction, and so does its charge.
    expect(await movements()).toEqual([
      ['tokens_reserved', -270, 'charged'],
      ['tokens_charged', -270, 'charged'],
      ['tokens_reserved', -approved.final_cost, 'charged'],
      ['tokens_charged', -approved.final_cost, 'charged'],
    ]);
  });

  it('la actividad dice cómo terminó cada reserva: cobrada en parte, devuelta o desconocida', () => {
    expect(reservationStateOf({ status: 'RESERVED', amount: 20, reserved_amount: 20 })).toBe('reserved');
    expect(reservationStateOf({ status: 'COMPLETED', amount: 20, reserved_amount: 20 })).toBe('charged');
    expect(reservationStateOf({ status: 'COMPLETED', amount: 20, reserved_amount: null })).toBe('charged');
    expect(reservationStateOf({ status: 'COMPLETED', amount: 8, reserved_amount: 12 })).toBe('partially_charged');
    expect(reservationStateOf({ status: 'FAILED', amount: 20, reserved_amount: 20 })).toBe('refunded');

    signIn('estudio-norte');
    const db = loadDb();
    const project = db.projects[0];
    const transaction = db.transactions.find((item) => item.status === 'RESERVED' || item.status === 'COMPLETED');
    if (!project || !transaction) throw new Error('faltan datos');
    Object.assign(transaction, { status: 'COMPLETED', amount: 8, reserved_amount: 12 });
    const base = { project: project.id, actor: null, created_at: '2026-11-27T10:00:00Z', description: 'x' };
    db.activity = [
      { ...base, id: 'a', type: 'tokens_reserved', amount: -12, _transaction: transaction.id },
      { ...base, id: 'b', type: 'tokens_refunded', amount: 4, _transaction: transaction.id },
      // Not a token item, a transaction that is gone, and an item of no known transaction: served as they are.
      { ...base, id: 'c', type: 'render_finished', amount: null, _transaction: transaction.id },
      { ...base, id: 'd', type: 'tokens_charged', amount: -8, _transaction: 'gone' },
      { ...base, id: 'e', type: 'tokens_reserved', amount: -5 },
    ];
    const served = Object.fromEntries(serveActivity(db, project.id).map((item) => [item.id, item]));
    expect(served.a?.reservation_state).toBe('partially_charged');
    expect(served.b?.reservation_state).toBe('partially_charged');
    for (const id of ['c', 'd', 'e']) expect(served[id]).not.toHaveProperty('reservation_state');
    // What is only for the mock never leaves it.
    expect(Object.values(served).some((item) => '_transaction' in item || 'project' in item)).toBe(false);
  });

  it('los errores de subida y del pipeline llegan como en el backend', async () => {
    const upload = (name: string, extra: Record<string, unknown> = {}) =>
      apiCall(projects.createProject, { body: { source_file: new File(['x'], name, { type: 'video/mp4' }), ...extra } });
    expect((await fail(upload('no-video.mp4'))).fieldErrors.source_file?.[0]).toMatch(/no es un video válido/);
    expect((await fail(upload('muy-chico.mp4'))).fieldErrors.source_file?.[0]).toMatch(/demasiado baja/);
    expect((await fail(upload('notas.txt'))).fieldErrors.source_file?.[0]).toMatch(/Solo se permiten archivos de video/);
    // Over the cap: a 413 with its code and the cap of /pricing/, before anything is reserved.
    const heavy = await fail(upload('muy-pesado.mp4'));
    expect(heavy.status).toBe(413);
    expect(heavy.data).toEqual({ error: 'El archivo pesa más de 2 GB, el máximo que podemos recibir.', code: 'file_too_large', limit_bytes: 2 * 1024 ** 3 });
    expect((await fail(upload('largo-20min.mp4', { project_type: 'single_edit' }))).fieldErrors.project_type?.[0]).toMatch(/hasta 10 min/);
    expect((await fail(apiCall(projects.createProject, { body: { video_url: 'https://youtu.be/abc', project_type: 'single_edit' } }))).fieldErrors.project_type?.[0]).toMatch(/subí el archivo/);
    expect((await fail(apiCall(projects.createProjectFromPrompt, { body: { video_url: 'https://youtu.be/abc', prompt: ' ' } }))).fieldErrors.prompt).toBeTruthy();

    const failing = await upload('sin-voz-5min.mp4');
    advance(SIM.queueMs + SIM.stageMs * 5);
    const project = await apiCall(projects.getProject, { params: { id: failing.id } });
    expect(project.status).toBe('failed');
    expect(project.pipeline_error_code).toBe('AINoSpeechDetectedError');
    expect(project.cost?.state).toBe('refunded');
    // The reservation that came back and its refund say so: the screen shows −N and +N, never a lone +N.
    const tokens = (await apiCall(projects.getProjectActivity, { params: { id: failing.id } })).filter((item) => item.amount !== null);
    expect(tokens.map((item) => [item.type, item.reservation_state]).sort()).toEqual([
      ['tokens_refunded', 'refunded'],
      ['tokens_reserved', 'refunded'],
    ]);
    expect(tokens.reduce((sum, item) => sum + (item.amount ?? 0), 0)).toBe(0);
    expect(project.stage_timings && Object.values(project.stage_timings).filter((timing) => timing.duration_s === null && timing.finished_at)).toHaveLength(1);

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
    const accepted = await apiCall(workspaces.acceptInvitation, { body: { token: 'con-cuenta' } });
    expect(accepted.tokens).toBeNull();
    expect((await apiCall(users.getMe)).workspaces.map((workspace) => workspace.name)).toContain('Estudio Norte');
    // Used once: the link is dead for everybody, with the backend's 410 and code.
    const used = await fail(apiCall(workspaces.acceptInvitation, { body: { token: 'con-cuenta' } }));
    expect([used.status, used.code]).toEqual([410, 'invitation_invalid']);
    expect((await fail(apiCall(workspaces.getInvitation, { body: { token: 'con-cuenta' } }))).status).toBe(404);
  });

  it('una invitación es de su email: sin sesión pide entrar, y otra cuenta no la acepta', async () => {
    signIn('estudio-norte', null);
    expect((await fail(apiCall(workspaces.acceptInvitation, { body: { token: 'con-cuenta' } }))).status).toBe(401);
    signIn('estudio-norte', 'lucia');
    const other = await fail(apiCall(workspaces.acceptInvitation, { body: { token: 'con-cuenta' } }));
    expect([other.status, other.code]).toEqual([403, 'invitation_email_mismatch']);
    expect((await apiCall(workspaces.getInvitation, { body: { token: 'con-cuenta' } })).status).toBe('pending');
  });

  it('invitar no dice de quién es la cuenta, y reenviar cambia el link', async () => {
    signIn('estudio-norte', 'lucia');
    // sofia@ejemplo.com has an account: the pending row still comes without `user`.
    const { invited } = await apiCall(workspaces.inviteMembers, { params: { id: WORKSPACE_IDS.estudio }, body: { emails: ['nadie@ejemplo.com'], role: 'editor' } });
    expect(invited[0]?.user).toBeNull();
    const db = loadDb();
    const before = db.invitations.find((item) => item.member === invited[0]?.id)?.token ?? '';
    await apiCall(workspaces.resendInvitation, { params: { id: WORKSPACE_IDS.estudio, memberId: invited[0]?.id ?? '' } });
    expect((await fail(apiCall(workspaces.getInvitation, { body: { token: before } }))).status).toBe(404);
    const after = loadDb().invitations.find((item) => item.member === invited[0]?.id)?.token ?? '';
    expect(after).not.toBe(before);
    expect((await apiCall(workspaces.getInvitation, { body: { token: after } })).email).toBe('nadie@ejemplo.com');
  });

  it('probe-link reconoce solo los hosts exactos del backend', async () => {
    const ok = await apiCall(projects.probeLink, { body: { video_url: 'https://www.youtube.com/watch?v=abc' } });
    expect(ok.provider).toBe('YouTube');
    for (const video_url of ['https://youtube.com.evil.com/watch?v=abc', 'https://consent.youtube.com/m?continue=x', 'https://evil.vimeo.com.example/1']) {
      const error = await fail(apiCall(projects.probeLink, { body: { video_url } }));
      expect(error.status, video_url).toBe(400);
      expect(error.code, video_url).toBe('unsupported');
    }
  });

  it('probe-link: más de 3 h es 400 `duration_app` con cualquier plan; dentro de las 3 h y más que el plan, 403 `duration`', async () => {
    const long = { body: { video_url: 'https://www.youtube.com/watch?v=largo' } }; // 5 h
    // Over the app's 3 h no plan takes it: on Pro (3 h) and on Gratis (10 min) alike.
    const app = await fail(apiCall(projects.probeLink, long));
    expect([app.status, app.code, app.data.limit]).toEqual([400, 'duration_app', 10800]);
    expect(app.isPlanLimit).toBe(false);
    signIn('plan-gratis');
    const onGratis = await fail(apiCall(projects.probeLink, long));
    expect([onGratis.status, onGratis.code, onGratis.data.limit]).toEqual([400, 'duration_app', 10800]);
    expect(onGratis.isPlanLimit).toBe(false);
    // Within the 3 h and over Gratis's 10 min: a bigger plan takes it.
    const shorter = await fail(apiCall(projects.probeLink, { body: { video_url: 'https://www.youtube.com/watch?v=clase-45min' } }));
    expect([shorter.status, shorter.code, shorter.data.limit]).toEqual([403, 'duration', 600]);
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

  it('workspaces: como el backend, el rol es el del workspace de la ruta y el dueño solo cambia al transferir', async () => {
    const id = WORKSPACE_IDS.estudio;
    // A new workspace: the user is its owner and the wallet starts empty; the name is trimmed.
    const created = await apiCall(workspaces.createWorkspace, { body: { name: '  Canal nuevo  ' } });
    expect(created).toMatchObject({ name: 'Canal nuevo', owner: USER_IDS.lucia, role: 'owner' });
    expect((await apiCall(users.getMe)).workspaces.find((item) => item.id === created.id)?.available_balance).toBe(0);
    expect((await fail(apiCall(workspaces.createWorkspace, { body: { name: '   ' } }))).fieldErrors.name?.[0]).toBe('Escribí un nombre para el espacio.');
    expect((await apiCall(workspaces.listWorkspaces)).at(-1)?.id).toBe(created.id);

    // The owner's row: never demoted or removed through members.
    const { members } = await apiCall(workspaces.listMembers, { params: { id } });
    const owner = members.find((member) => member.role === 'owner');
    const other = members.find((member) => member.status === 'active' && member.role !== 'owner' && member.user);
    if (!owner || !other?.user) throw new Error('faltan miembros');
    expect((await fail(apiCall(workspaces.updateMember, { params: { id, memberId: owner.id }, body: { role: 'viewer' } }))).fieldErrors.role?.[0]).toBe('No se puede cambiar el rol del dueño.');
    expect((await fail(apiCall(workspaces.removeMember, { params: { id, memberId: owner.id } }))).message).toBe('No se puede quitar al dueño del workspace.');
    expect((await fail(apiCall(workspaces.removeMember, { params: { id, memberId: created.id } }))).status).toBe(404);

    // Delete: the exact name, and an empty workspace goes.
    expect((await fail(apiCall(workspaces.deleteWorkspace, { params: { id: created.id }, body: { confirm_name: 'canal nuevo' } }))).fieldErrors.confirm_name?.[0]).toBe('El nombre no coincide.');
    await apiCall(workspaces.deleteWorkspace, { params: { id: created.id }, body: { confirm_name: 'Canal nuevo' } });
    expect((await fail(apiCall(workspaces.getWorkspace, { params: { id: created.id } }))).status).toBe(404);

    // Transfer: not to oneself; the previous owner stays as Admin and stops being able to transfer or delete.
    expect((await fail(apiCall(workspaces.transferWorkspace, { params: { id }, body: { new_owner_id: USER_IDS.lucia } }))).status).toBe(400);
    const transferred = await apiCall(workspaces.transferWorkspace, { params: { id }, body: { new_owner_id: other.user.id } });
    expect(transferred).toMatchObject({ owner: other.user.id, role: 'admin' });
    const denied = await fail(apiCall(workspaces.transferWorkspace, { params: { id }, body: { new_owner_id: USER_IDS.lucia } }));
    expect(denied.status).toBe(403);
    // The backend's sentence, with no `code`.
    expect(denied.code).toBeUndefined();
    expect(denied.message).toBe('Solo el dueño del espacio puede hacer esto.');
  });

  it('borrar un workspace: nunca con publicaciones saliendo, con un pago en curso, con saldo ni si es el único', async () => {
    const id = WORKSPACE_IDS.estudio;
    const name = (await apiCall(workspaces.getWorkspace, { params: { id } })).name;
    const remove = () => fail(apiCall(workspaces.deleteWorkspace, { params: { id }, body: { confirm_name: name } }));
    const edit = (change: (db: ReturnType<typeof loadDb>) => void) => {
      const db = loadDb();
      change(db);
      saveDb(db);
    };
    // Nothing processing, one post going out.
    edit((db) => {
      db.projects = db.projects.filter((project) => project.workspace !== id || !['uploaded', 'ingesting', 'rendering'].includes(project.status));
      const post = db.posts.find((item) => item.workspace === id);
      if (!post) throw new Error('falta una publicación');
      post.status = 'PROCESSING';
      for (const wallet of db.wallets) if (wallet.workspace === id) Object.assign(wallet, { available_balance: 120.5, reserved_balance: 0 });
    });
    expect((await remove()).code).toBe('posts_in_flight');
    // Once none is going out (the simulation would send a due SCHEDULED one, so they end as drafts), a payment
    // waiting for its provider stops it: its row is what the confirmation settles.
    const purchaseId = '7d1f3a52-6c1e-4b8a-9f0d-2e5c8b4a1f00';
    edit((db) => {
      for (const post of db.posts) if (post.workspace === id && ['SCHEDULED', 'QUEUED', 'PROCESSING'].includes(post.status)) post.status = 'DRAFT';
      db.purchases.push({
        id: purchaseId,
        workspace: id,
        status: 'pending',
        tokens: 1000,
        plan_id: null,
        amount_usd: 10,
        checkout_url: `http://localhost:3000/dev/checkout?compra=${purchaseId}`,
        failure_reason: null,
        created_at: new Date().toISOString(),
        balance_after: null,
        _settleAt: null,
        _reject: false,
      });
    });
    const paying = await remove();
    expect([paying.status, paying.code, paying.message]).toEqual([409, 'payment_pending', 'Hay un pago en curso: terminalo, o esperá a que venza (24 h después de abrirlo).']);
    // Once it is settled (here, rejected), the tokens stop it.
    edit((db) => {
      for (const purchase of db.purchases) if (purchase.id === purchaseId) Object.assign(purchase, { status: 'rejected', checkout_url: null });
    });
    const balance = await remove();
    expect(balance.code).toBe('balance_left');
    expect(balance.message).toBe('Quedan 120.5 tokens en este espacio y no se pueden recuperar después de borrarlo. Usalos o escribinos antes de borrarlo.');
    // No other workspace to work in: that comes first, whatever else holds (the tokens are still there).
    edit((db) => {
      db.workspaces = db.workspaces.filter((workspace) => workspace.id === id);
      db.members = db.members.filter((member) => member.workspace === id);
    });
    expect((await remove()).code).toBe('last_workspace');
    expect(loadDb().workspaces.map((workspace) => workspace.id)).toEqual([id]);
  });

  it('crear workspaces: hasta 10 propios', async () => {
    const owned = loadDb().workspaces.filter((workspace) => workspace.owner === USER_IDS.lucia).length;
    for (let number = owned; number < 10; number += 1) await apiCall(workspaces.createWorkspace, { body: { name: `Espacio ${number}` } });
    const refused = await fail(apiCall(workspaces.createWorkspace, { body: { name: 'Uno más' } }));
    expect(refused.status).toBe(409);
    expect(refused.code).toBe('workspace_limit');
  });

  it('borrar un workspace: nunca con proyectos en proceso', async () => {
    signIn('todos-los-estados');
    const me = await apiCall(users.getMe);
    const mine = me.workspaces.find((item) => item.id === me.active_workspace_id);
    if (!mine || mine.role !== 'owner') throw new Error('falta el workspace propio');
    const busy = await fail(apiCall(workspaces.deleteWorkspace, { params: { id: mine.id }, body: { confirm_name: mine.name } }));
    expect(busy.status).toBe(409);
    expect(busy.code).toBe('project_processing');
    await expect(apiCall(workspaces.getWorkspace, { params: { id: mine.id } })).resolves.toBeTruthy();
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
    // One that ended: a project that is processing or rendering cannot be deleted (409).
    const own = mine.find((project) => project.uploaded_by === USER_IDS.carla && project.status === 'completed');
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

  it('el checkout de prueba con billing en el backend: simulate liquida en el momento y una vez', async () => {
    const purchase = await apiCall(billing.createTokenPurchase, { body: { custom_tokens: 2500, return_url: 'http://localhost:3000/uso/comprar/resultado' } });
    expect(purchase.plan_id).toBeNull();
    const approved = await apiCall(billing.simulatePurchase, { params: { id: purchase.id }, body: { approved: true } });
    expect(approved).toMatchObject({ status: 'approved', checkout_url: null, balance_after: 4860 + 2500 });
    // Settled already: answered as it is, nothing credited twice.
    expect((await apiCall(billing.simulatePurchase, { params: { id: purchase.id }, body: { approved: false } })).status).toBe('approved');
    expect((await apiCall(billing.getWallet)).available_balance).toBe(4860 + 2500);
    expect((await fail(apiCall(billing.createTokenPurchase, { body: { custom_tokens: 1_000_001, return_url: 'http://localhost:3000/uso/comprar/resultado' } }))).fieldErrors.custom_tokens).toEqual(['El máximo es 1.000.000 tokens por compra.']);
  });

  it('planes: bajar sin período pagado se aplica al momento; la misma subida con pago reusa su checkout', async () => {
    const plans = await apiCall(billing.listPlans);
    const plan = (name: string) => plans.find((item) => item.name === name)?.id ?? '';
    const db = loadDb();
    const workspace = db.workspaces.find((item) => item.id === WORKSPACE_IDS.estudio);
    if (!workspace) throw new Error('falta el workspace');
    workspace.renews_at = null;
    saveDb(db);
    const down = await apiCall(billing.changePlan, { body: { plan_id: plan('Creador') } });
    expect(down.applied).toBe('now');
    expect(down.subscription.plan.name).toBe('Creador');

    signIn('plan-con-checkout');
    const first = await apiCall(billing.changePlan, { body: { plan_id: plan('Estudio'), return_url: 'http://localhost:3000/uso/planes' } });
    const again = await apiCall(billing.changePlan, { body: { plan_id: plan('Estudio'), return_url: 'http://localhost:3000/uso/planes' } });
    expect(again.checkout_url).toBe(first.checkout_url);
    expect(loadDb().purchases.filter((item) => item.plan_id && item.status === 'pending')).toHaveLength(1);
    // Keeping the current plan cancels the upgrade that waits for its payment.
    const kept = await apiCall(billing.changePlan, { body: { plan_id: plan('Pro') } });
    expect(kept.applied).toBe('now');
    expect(loadDb().purchases.find((item) => item.plan_id)?.status).toBe('rejected');
    expect((await fail(apiCall(billing.changePlan, { body: { plan_id: plan('Pro') } }))).code).toBe('nothing_to_cancel');
  });

  it('planes: «Seguir con» cancela solo lo que la pantalla mostró y nunca cambia el plan', async () => {
    signIn('plan-con-checkout');
    const plans = await apiCall(billing.listPlans);
    const plan = (name: string) => plans.find((item) => item.name === name)?.id ?? '';
    const started = await apiCall(billing.changePlan, { body: { plan_id: plan('Estudio'), return_url: 'http://localhost:3000/uso/planes' } });
    const payment = started.subscription.pending_payment;
    if (!payment) throw new Error('falta el pago');
    const before = JSON.stringify(loadDb().purchases);
    // A stale screen that still shows another plan, or a payment that is no longer the one waiting: 409, nothing changes.
    const stale = await fail(apiCall(billing.cancelPending, { body: { current_plan_id: plan('Gratis'), payment_id: payment.id } }));
    expect([stale.status, stale.code, stale.message]).toEqual([409, 'plan_changed', 'Tu plan cambió mientras tanto: ahora estás en el plan Pro. No cancelamos nada.']);
    const unseen = await fail(apiCall(billing.cancelPending, { body: { current_plan_id: plan('Pro'), payment_id: '99999999-9999-4999-8999-999999999999' } }));
    expect([unseen.status, unseen.code]).toEqual([409, 'pending_changed']);
    expect(JSON.stringify(loadDb().purchases)).toBe(before);
    expect((await fail(apiCall(billing.cancelPending, { body: { current_plan_id: plan('Pro') } }))).status).toBe(400);

    const kept = await apiCall(billing.cancelPending, { body: { current_plan_id: plan('Pro'), payment_id: payment.id } });
    expect([kept.subscription.plan.name, kept.subscription.pending_payment]).toEqual(['Pro', null]);
    expect(loadDb().purchases.find((item) => item.id === payment.id)).toMatchObject({ status: 'rejected', failure_reason: 'Elegiste seguir con tu plan antes de pagar este.' });
    const again = await fail(apiCall(billing.cancelPending, { body: { current_plan_id: plan('Pro'), payment_id: payment.id } }));
    expect([again.status, again.code]).toEqual([409, 'nothing_to_cancel']);
    expect(loadDb().purchases.filter((item) => item.plan_id)).toHaveLength(1);
  });

  it('planes: todo plan pago es una compra, y mientras una bajada espera nadie entra pasado sus lugares', async () => {
    const db = loadDb();
    const pro = db.plans.find((item) => item.name === 'Pro');
    if (!pro) throw new Error('faltan datos');
    // Same price, listed later: still a purchase that applies at once (no checkout scenario here).
    db.plans.push({ ...pro, id: '44444444-4444-4444-8444-444444444444', name: 'Pro Plus', sort_order: 9, is_recommended: false });
    // A second free plan with 5 seats: only a free plan waits for the end of the period.
    db.plans.push({ ...pro, id: '55555555-5555-4555-8555-555555555555', name: 'Libre', price_usd_monthly: 0, sort_order: 0, is_recommended: false, max_members: 5 });
    saveDb(db);
    const lateral = await apiCall(billing.changePlan, { body: { plan_id: '44444444-4444-4444-8444-444444444444' } });
    expect(lateral.applied).toBe('now');
    expect(lateral.subscription.plan.name).toBe('Pro Plus');
    const free = await apiCall(billing.changePlan, { body: { plan_id: '55555555-5555-4555-8555-555555555555' } });
    expect(free.applied).toBe('period_end');
    // Miembros shows the seats inviting checks, and why.
    const members = await apiCall(workspaces.listMembers, { params: { id: WORKSPACE_IDS.estudio } });
    expect([members.max_members, members.seats_used]).toEqual([5, 4]);
    expect(members.limited_by).toEqual({ reason: 'pending_plan', plan_name: 'Libre', effective_at: free.subscription.pending_plan?.effective_at });
    // Libre has 5 seats and 4 are taken: one more fits, the next does not.
    await apiCall(workspaces.inviteMembers, { params: { id: WORKSPACE_IDS.estudio }, body: { emails: ['quinta@estudionorte.com'], role: 'viewer' } });
    const refused = await fail(apiCall(workspaces.inviteMembers, { params: { id: WORKSPACE_IDS.estudio }, body: { emails: ['sexta@estudionorte.com'], role: 'viewer' } }));
    expect(refused.code).toBe('seats_exceeded');
    expect(refused.message).toMatch(/^Tu plan pasa a 'Libre' el .* y ese plan permite 5 miembros/);
  });

  it('planes: mientras un pago de plan espera su checkout, nadie entra pasado los lugares de ese plan', async () => {
    signIn('plan-con-checkout');
    const plans = await apiCall(billing.listPlans);
    const creator = plans.find((item) => item.name === 'Creador');
    if (!creator) throw new Error('falta Creador');
    // Pro with 4 seats in use; Creador has 5.
    expect((await apiCall(workspaces.listMembers, { params: { id: WORKSPACE_IDS.estudio } })).limited_by).toBeNull();
    expect((await apiCall(billing.changePlan, { body: { plan_id: creator.id, return_url: 'http://localhost:3000/uso/planes' } })).checkout_url).toBeTruthy();
    // Miembros shows the seats inviting checks, and why.
    const members = await apiCall(workspaces.listMembers, { params: { id: WORKSPACE_IDS.estudio } });
    expect([members.max_members, members.seats_used]).toEqual([5, 4]);
    expect(members.limited_by).toEqual({ reason: 'pending_payment', plan_name: 'Creador', effective_at: null });
    await apiCall(workspaces.inviteMembers, { params: { id: WORKSPACE_IDS.estudio }, body: { emails: ['quinta@estudionorte.com'], role: 'viewer' } });
    const refused = await fail(apiCall(workspaces.inviteMembers, { params: { id: WORKSPACE_IDS.estudio }, body: { emails: ['sexta@estudionorte.com'], role: 'viewer' } }));
    expect(refused.code).toBe('seats_exceeded');
    expect(refused.message).toBe("Estás pasando al plan 'Creador', que permite 5 miembros. Hay un pago en curso: terminalo, o elegí «Seguir con Pro» en Planes para cancelarlo.");
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

  it('volver al email actual (o null) cancela el cambio pendiente', async () => {
    expect((await apiCall(users.updateMe, { body: { email: 'lucia@gmial.com', current_password: 'secreta' } })).pending_email).toBe('lucia@gmial.com');
    expect((await apiCall(users.updateMe, { body: { email: 'Lucia@EstudioNorte.com' } })).pending_email).toBeNull();
    await apiCall(users.updateMe, { body: { email: 'lucia@gmial.com', current_password: 'secreta' } });
    expect((await apiCall(users.updateMe, { body: { email: null } })).pending_email).toBeNull();
  });

  it('cambiar la contraseña devuelve un par nuevo que sirve para seguir con sesión', async () => {
    expect((await fail(apiCall(users.changePassword, { body: { current_password: 'incorrecta', new_password: 'unaclavelarga' } }))).fieldErrors.current_password).toEqual(['La contraseña actual no coincide.']);
    const tokens = await apiCall(users.changePassword, { body: { current_password: 'laDeSiempre', new_password: 'unaclavelarga' } });
    expect(auth.TokenPair.safeParse(tokens).success).toBe(true);
    setTokens(tokens);
    expect((await apiCall(users.getMe)).email).toBe('lucia@estudionorte.com');
  });

  it('miembros: una invitación pendiente ocupa un lugar y el plan corta al llegar al límite', async () => {
    signIn('miembros-queda-un-lugar');
    const id = WORKSPACE_IDS.estudio;
    const before = await apiCall(workspaces.listMembers, { params: { id } });
    expect(before.seats_used).toBe(before.max_members - 1);
    const invited = await apiCall(workspaces.inviteMembers, { params: { id }, body: { emails: ['juan@estudionorte.com'], role: 'editor' } });
    expect(invited.invited[0]?.status).toBe('pending');
    expect((await apiCall(workspaces.listMembers, { params: { id } })).seats_used).toBe(10);
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
    // Later copies are numbered.
    const second = await apiCall(brandKits.duplicateBrandKit, { params: { id: other.id } });
    expect(second.name).toBe(`Copia 2 de ${other.name}`);
    await apiCall(brandKits.deleteBrandKit, { params: { id: second.id } });
    // As the backend: every file of the copy is its own (another name), and the rest is the same.
    const withFiles = kits.find((kit) => kit.watermark_logo && kit.primary_font);
    if (!withFiles) throw new Error('falta un kit con archivos');
    const twin = await apiCall(brandKits.duplicateBrandKit, { params: { id: withFiles.id } });
    for (const field of ['primary_font', 'secondary_font', 'watermark_logo', 'intro_video', 'outro_video'] as const) {
      if (withFiles[field] === null) expect(twin[field]).toBeNull();
      else {
        expect(twin[field]).not.toBe(withFiles[field]);
        // The folder of the copy, the same file name: no storage suffix.
        expect(twin[field]?.split('/').slice(0, -1)).toEqual([...(withFiles[field]?.split('/').slice(0, -1) ?? []), twin.id]);
        expect(twin[field]?.split('/').pop()).toBe(withFiles[field]?.split('/').pop());
      }
    }
    expect({ ...twin, id: '', name: '', user: null, created_at: '', is_default: false, primary_font: null, secondary_font: null, watermark_logo: null, intro_video: null, outro_video: null }).toEqual({
      ...withFiles, id: '', name: '', user: null, created_at: '', is_default: false, primary_font: null, secondary_font: null, watermark_logo: null, intro_video: null, outro_video: null,
    });
    // Unmarking the default leaves the workspace without one, and creating a kit does not make it default.
    await apiCall(brandKits.updateBrandKit, { params: { id: other.id }, body: { is_default: false } });
    expect((await apiCall(brandKits.listBrandKits)).filter((kit) => kit.is_default)).toEqual([]);
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

describe('mocks · renders pagos (views_clips.py)', () => {
  beforeEach(() => signIn('estudio-norte'));

  const projectByTitle = (title: string) => {
    const project = loadDb().projects.find((item) => item.title.startsWith(title));
    if (!project) throw new Error(`No hay proyecto «${title}»`);
    return project;
  };
  const edit = (title: string, change: (project: ReturnType<typeof projectByTitle>, db: ReturnType<typeof loadDb>) => void) => {
    const db = loadDb();
    const project = db.projects.find((item) => item.title.startsWith(title));
    if (!project) throw new Error(`No hay proyecto «${title}»`);
    change(project, db);
    saveDb(db);
  };
  const transaction = (id: string) => loadDb().transactions.find((item) => item.id === id);
  const seconds = (clip: { start_time: number; end_time: number }) => clip.end_time - clip.start_time;

  it('reintentar un clip fallido cobra su render: reserva, renderiza y liquida', async () => {
    const project = projectByTitle('Charla en la UTN');
    const failed = project.clips.find((clip) => clip.status === 'failed');
    if (!failed) throw new Error('falta el clip fallido');
    const before = await apiCall(billing.getWallet);

    const retried = await apiCall(clips.retryClip, { params: { id: failed.id } });
    // As the backend: the clip is `draft`, queued, until a worker takes it.
    expect([retried.status, retried.render_queued]).toEqual(['draft', true]);
    expect(retried.final_cost).toBeGreaterThan(0);
    expect(transaction(retried.transaction_id)).toMatchObject({ status: 'RESERVED', amount: retried.final_cost, category: 'render', description: `Reintento de clip: ${failed.title}` });
    const reserved = await apiCall(billing.getWallet);
    expect(reserved.available_balance).toBeCloseTo(before.available_balance - retried.final_cost, 2);
    expect(reserved.reserved_balance).toBeCloseTo(before.reserved_balance + retried.final_cost, 2);
    // The project renders until the clip ends, as in the backend.
    expect((await apiCall(projects.getProject, { params: { id: project.id } })).status).toBe('rendering');
    advance(SIM.claimMs);
    const claimed = (await apiCall(projects.getProject, { params: { id: project.id } })).clips.find((clip) => clip.id === failed.id);
    expect([claimed?.status, claimed?.render_queued]).toEqual(['rendering', undefined]);

    advance(SIM.clipMs * 2);
    const after = await apiCall(projects.getProject, { params: { id: project.id } });
    expect(after.status).toBe('completed');
    expect(after.clips.find((clip) => clip.id === failed.id)?.status).toBe('completed');
    expect(transaction(retried.transaction_id)).toMatchObject({ status: 'COMPLETED', amount: retried.final_cost });
    const settled = await apiCall(billing.getWallet);
    expect(settled.available_balance).toBeCloseTo(before.available_balance - retried.final_cost, 2);
    expect(settled.reserved_balance).toBeCloseTo(before.reserved_balance, 2);
  });

  it('un solo render pago por proyecto a la vez: el segundo recibe el 409 del backend y no se cobra', async () => {
    const project = projectByTitle('Charla en la UTN');
    const failed = project.clips.find((clip) => clip.status === 'failed');
    const ready = project.clips.find((clip) => clip.status === 'completed');
    if (!failed || !ready) throw new Error('faltan clips');
    await apiCall(clips.retryClip, { params: { id: failed.id } });
    const wallet = await apiCall(billing.getWallet);

    // A double click on the retry: the clip is no longer failed.
    const again = await fail(apiCall(clips.retryClip, { params: { id: failed.id } }));
    expect([again.status, again.code]).toEqual([409, 'not_failed']);
    // The same clip from the editor, while it waits for its render.
    const same = await fail(apiCall(clips.renderClip, { params: { id: failed.id } }));
    expect([same.status, same.code, same.message]).toEqual([409, 'clip_rendering', 'El clip ya se está renderizando.']);
    // Another clip of the same project.
    const other = await fail(apiCall(clips.renderClip, { params: { id: ready.id } }));
    expect([other.status, other.code]).toEqual([409, 'project_rendering']);
    expect(other.message).toMatch(/El proyecto se está renderizando/);
    // And the whole project.
    const all = await fail(apiCall(projects.rerenderProject, { params: { id: project.id }, body: {} }));
    expect([all.status, all.code]).toEqual([409, 'clip_rendering']);
    // The same once a worker took the clip.
    advance(SIM.claimMs);
    const claimed = await fail(apiCall(clips.renderClip, { params: { id: failed.id } }));
    expect([claimed.status, claimed.code]).toEqual([409, 'clip_rendering']);
    // Nothing else was reserved.
    expect((await apiCall(billing.getWallet)).available_balance).toBe(wallet.available_balance);
    expect((await apiCall(billing.getWallet)).reserved_balance).toBe(wallet.reserved_balance);
  });

  it('un reintento que vuelve a fallar devuelve todo y el clip queda en error', async () => {
    const project = projectByTitle('Charla en la UTN');
    const failed = project.clips.find((clip) => clip.status === 'failed');
    if (!failed) throw new Error('falta el clip fallido');
    const before = await apiCall(billing.getWallet);
    const retried = await apiCall(clips.retryClip, { params: { id: failed.id } });
    edit('Charla en la UTN', (item) => {
      for (const clip of item.clips) if (clip.id === failed.id) clip._renderFails = true;
    });
    advance(SIM.clipMs * 2);
    const after = await apiCall(projects.getProject, { params: { id: project.id } });
    expect(after.status).toBe('partial');
    expect(after.clips.find((clip) => clip.id === failed.id)?.status).toBe('failed');
    expect(transaction(retried.transaction_id)).toMatchObject({ status: 'FAILED', kind: 'released' });
    expect((await apiCall(billing.getWallet)).available_balance).toBeCloseTo(before.available_balance, 2);
    // Actividad does not say the render finished: nothing was generated, and it says what came back.
    const feed = await apiCall(projects.getProjectActivity, { params: { id: project.id } });
    const finished = feed.filter((item) => item.type === 'render_finished')[0];
    expect(finished?.description).toMatch(/^No se pudo renderizar el clip: sigue con error\. Te devolvimos [\d.,]+ tokens?\.$/);
  });

  it('un render de clip que falla dice en Actividad que quedó el clip anterior, y el cobro de cada render dice de cuál es', async () => {
    edit('Vlog Mendoza', (item, db) => {
      db.posts = db.posts.filter((post) => post.clip.project_id !== item.id);
    });
    const project = projectByTitle('Vlog Mendoza');
    const [first, second] = project.clips.filter((clip) => clip.status === 'completed' || clip.status === 'published');
    if (!first || !second) throw new Error('faltan clips entregados');
    const activity = async () => await apiCall(projects.getProjectActivity, { params: { id: project.id } });

    // One clip rendered again and delivered: its charge is named after it.
    await apiCall(clips.renderClip, { params: { id: second.id } });
    advance(SIM.clipMs * 2);
    expect((await activity()).filter((item) => item.type === 'tokens_charged')[0]?.description).toBe(`Cobro por renderizar «${second.title}»`);

    // Another one whose new render fails: it keeps the file it had and everything comes back.
    const paid = await apiCall(clips.renderClip, { params: { id: first.id } });
    edit('Vlog Mendoza', (item) => {
      for (const clip of item.clips) if (clip.id === first.id) clip._renderFails = true;
    });
    advance(SIM.clipMs * 2);
    expect(transaction(paid.transaction_id)).toMatchObject({ status: 'FAILED' });
    const after = await apiCall(projects.getProject, { params: { id: project.id } });
    expect(after.clips.find((clip) => clip.id === first.id)?.status).toBe(first.status);
    const finished = (await activity()).filter((item) => item.type === 'render_finished')[0];
    expect(finished?.description).toMatch(/^No se pudo volver a renderizar: quedó el clip anterior\. Te devolvimos [\d.,]+ tokens?\.$/);
  });

  it('se cobra por lo entregado: de un render de todos los clips, lo que falló vuelve y el clip conserva lo que tenía', async () => {
    // Its scheduled posts would refuse the render (posts_pending): they are cancelled first.
    edit('Vlog Mendoza', (item, db) => {
      db.posts = db.posts.filter((post) => post.clip.project_id !== item.id);
    });
    const project = projectByTitle('Vlog Mendoza');
    const delivered = project.clips.filter((clip) => clip.status === 'completed' || clip.status === 'published');
    expect(delivered.length).toBeGreaterThan(1);
    const loser = delivered[0] as (typeof delivered)[number];
    const before = await apiCall(billing.getWallet);

    const paid = await apiCall(projects.rerenderProject, { params: { id: project.id }, body: { editing_style: 'hormozi' } });
    edit('Vlog Mendoza', (item) => {
      for (const clip of item.clips) if (clip.id === loser.id) clip._renderFails = true;
    });
    advance(SIM.clipMs * (delivered.length + 1));

    const total = delivered.reduce((sum, clip) => sum + seconds(clip), 0);
    const charged = Math.round(paid.final_cost * ((total - seconds(loser)) / total) * 100) / 100;
    expect(transaction(paid.transaction_id)).toMatchObject({ status: 'COMPLETED', amount: charged });
    expect((await apiCall(billing.getWallet)).available_balance).toBeCloseTo(before.available_balance - charged, 2);
    const after = await apiCall(projects.getProject, { params: { id: project.id } });
    // The clip whose new render failed keeps the file it had: the project has nothing in error.
    expect(after.status).toBe('completed');
    const kept = after.clips.find((clip) => clip.id === loser.id);
    expect([kept?.status, kept?.s3_object_key]).toEqual([loser.status, loser.s3_object_key]);
    expect(after.editing_style).toBe('hormozi');
    // Actividad says what the bell says: not every clip was generated again, and what came back.
    expect(finishedText(await activityOf(project.id))).toMatch(/^No pudimos volver a generar todos los clips: los que ya tenías quedaron como estaban\. Te devolvimos [\d.,]+ tokens?\.$/);
  });

  const activityOf = async (id: string) => await apiCall(projects.getProjectActivity, { params: { id } });
  const finishedText = (feed: Awaited<ReturnType<typeof activityOf>>) => feed.filter((item) => item.type === 'render_finished')[0]?.description;
  const failEveryRender = (title: string) =>
    edit(title, (item) => {
      for (const clip of item.clips) clip._renderFails = true;
    });

  it('renderizar todo de nuevo en un proyecto parcial, si falla entero, dice que quedaron los clips que ya tenías', async () => {
    const project = projectByTitle('Charla en la UTN');
    const delivered = project.clips.filter((clip) => clip.status === 'completed' || clip.status === 'published');
    expect(delivered.length).toBeGreaterThan(1);
    expect(project.clips.some((clip) => clip.status === 'failed')).toBe(true);
    edit('Charla en la UTN', (item, db) => {
      db.posts = db.posts.filter((post) => post.clip.project_id !== item.id);
    });
    const paid = await apiCall(projects.rerenderProject, { params: { id: project.id }, body: { editing_style: 'hormozi' } });
    failEveryRender('Charla en la UTN');
    advance(SIM.clipMs * (project.clips.length + 2));
    expect(transaction(paid.transaction_id)).toMatchObject({ status: 'FAILED' });
    const after = await apiCall(projects.getProject, { params: { id: project.id } });
    expect(after.status).toBe('partial');
    expect(finishedText(await activityOf(project.id))).toMatch(/^No se pudo volver a renderizar: quedaron los clips que ya tenías\. Te devolvimos [\d.,]+ tokens?\.$/);
  });

  it('un video completo que vuelve a fallar dice que el render del video falló, y uno entregado conserva el anterior', async () => {
    signIn('todos-los-estados', 'martin');
    const broken = projectByTitle('Video completo que no se pudo generar');
    const first = await apiCall(projects.rerenderProject, { params: { id: broken.id }, body: {} });
    failEveryRender('Video completo que no se pudo generar');
    advance(SIM.clipMs * 3);
    expect(transaction(first.transaction_id)).toMatchObject({ status: 'FAILED' });
    expect(finishedText(await activityOf(broken.id))).toMatch(/^El render del video falló\. Te devolvimos [\d.,]+ tokens?\.$/);

    const done = projectByTitle('Video sin voz, editado sin subtítulos');
    const second = await apiCall(projects.rerenderProject, { params: { id: done.id }, body: {} });
    failEveryRender('Video sin voz, editado sin subtítulos');
    advance(SIM.clipMs * 3);
    expect(transaction(second.transaction_id)).toMatchObject({ status: 'FAILED' });
    expect(finishedText(await activityOf(done.id))).toMatch(/^No se pudo volver a renderizar: quedó el video anterior\. Te devolvimos [\d.,]+ tokens?\.$/);
  });

  it('una aprobación cuyos clips fallan todos no dice que terminó el render; una que entrega dice cuántos clips', async () => {
    const review = projectByTitle('Podcast Ep. 42');
    expect(review.status).toBe('awaiting_approval');
    const segments = review.clips.slice(0, 2).map((clip) => ({ start: clip.start_time, end: clip.end_time, clip_id: clip.id }));
    const approved = await apiCall(projects.approvePaperEdit, { params: { id: review.id }, body: { approved_segments: segments } });
    failEveryRender('Podcast Ep. 42');
    advance(SIM.clipMs * 4);
    const after = await apiCall(projects.getProject, { params: { id: review.id } });
    expect(after.clips.map((clip) => clip.status)).toEqual(['failed', 'failed']);
    expect(transaction(approved.transaction_id)).toMatchObject({ status: 'FAILED' });
    expect(finishedText(await activityOf(review.id))).toMatch(/^No se pudo renderizar: no se generó ningún clip\. Te devolvimos [\d.,]+ tokens?\.$/);

    // The retry of one of them is delivered: the project has one ready and one in error.
    const failed = after.clips[0];
    if (!failed) throw new Error('falta el clip');
    await apiCall(clips.retryClip, { params: { id: failed.id } });
    advance(SIM.clipMs * 2);
    expect(finishedText(await activityOf(review.id))).toBe('Terminó el render: 1 clip listo y 1 con error');
  });

  it('sin saldo el reintento responde 402 con lo que hacía falta y no cambia nada', async () => {
    const project = projectByTitle('Charla en la UTN');
    const failed = project.clips.find((clip) => clip.status === 'failed');
    if (!failed) throw new Error('falta el clip fallido');
    edit('Charla en la UTN', (_item, db) => {
      for (const wallet of db.wallets) wallet.available_balance = 0;
    });
    const error = await fail(apiCall(clips.retryClip, { params: { id: failed.id } }));
    expect(error.status).toBe(402);
    expect(error.message).toBe('Saldo insuficiente para reservar los tokens.');
    expect(error.requiredTokens).toBeGreaterThan(0);
    const after = await apiCall(projects.getProject, { params: { id: project.id } });
    expect([after.status, after.clips.find((clip) => clip.id === failed.id)?.status]).toEqual(['partial', 'failed']);
  });

  it('un clip con una publicación programada no se renderiza de nuevo (409 posts_pending)', async () => {
    const post = loadDb().posts.find((item) => item.status === 'SCHEDULED');
    if (!post) throw new Error('falta una publicación programada');
    const error = await fail(apiCall(clips.renderClip, { params: { id: post.video_clip } }));
    expect([error.status, error.code]).toEqual([409, 'posts_pending']);
  });

  it('re-despachar un proyecto que falló reserva de nuevo lo que se había devuelto', async () => {
    signIn('todos-los-estados', 'martin');
    const project = projectByTitle('Falló: CapacityTimeoutError');
    const creation = transaction(String(project.metadata.reservation_id));
    expect(creation?.status).toBe('FAILED');
    const before = await apiCall(billing.getWallet);

    const resumed = await apiCall(projects.redispatchProject, { params: { id: project.id } });
    // As the backend: the row is still `failed` with its code; the task reopens it when it runs.
    expect([resumed.status, resumed.pipeline_error_code]).toEqual(['failed', 'CapacityTimeoutError']);
    expect(resumed.cost_details?.tokens_reserved).toBe(creation?.amount);
    expect(transaction(resumed.cost_details?.transaction_id ?? '')).toMatchObject({ status: 'RESERVED', amount: creation?.amount, description: `Procesamiento: ${project.title}` });
    expect((await apiCall(billing.getWallet)).available_balance).toBeCloseTo(before.available_balance - (creation?.amount ?? 0), 2);
    // A second one before the task ran queues it again and reserves nothing more: its reservation is open.
    const again = await apiCall(projects.redispatchProject, { params: { id: project.id } });
    expect(again.cost_details).toBeUndefined();
    expect((await apiCall(billing.getWallet)).available_balance).toBeCloseTo(before.available_balance - (creation?.amount ?? 0), 2);
    // The task runs: the project is reopened and its error is gone.
    advance(SIM.queueMs);
    const reopened = await apiCall(projects.getProject, { params: { id: project.id } });
    expect([reopened.status, reopened.pipeline_error_code]).toEqual(['ingesting', '']);
    const late = await fail(apiCall(projects.redispatchProject, { params: { id: project.id } }));
    expect([late.status, late.code]).toEqual([409, 'not_failed']);
    // Its clips are chosen: the processing reserved again is charged, and the charge says it is the re-dispatch's.
    advance(SIM.stageMs * 6);
    expect(transaction(resumed.cost_details?.transaction_id ?? '')).toMatchObject({ status: 'COMPLETED' });
    const charges = (await apiCall(projects.getProjectActivity, { params: { id: project.id } })).filter((item) => item.type === 'tokens_charged');
    expect(charges.map((item) => item.description)).toEqual(['Cobro por volver a procesar']);
  });

  it('re-despachar: sin saldo 402; moderación, ingesta sin encolar y un proyecto que no falló, 409 con su código', async () => {
    signIn('todos-los-estados', 'martin');
    const code = async (title: string) => {
      const error = await fail(apiCall(projects.redispatchProject, { params: { id: projectByTitle(title).id } }));
      return [error.status, error.code];
    };
    expect(await code('Falló: UnsafeContentError')).toEqual([409, 'moderation_blocked']);
    expect(await code('Falló: IngestionNotQueued')).toEqual([409, 'not_queued']);
    expect(await code('Vlog Mendoza')).toEqual([409, 'not_failed']);
    edit('Falló: CapacityTimeoutError', (_item, db) => {
      for (const wallet of db.wallets) wallet.available_balance = 0;
    });
    const broke = await fail(apiCall(projects.redispatchProject, { params: { id: projectByTitle('Falló: CapacityTimeoutError').id } }));
    expect(broke.status).toBe(402);
    expect(broke.requiredTokens).toBeGreaterThan(0);
    expect(projectByTitle('Falló: CapacityTimeoutError').status).toBe('failed');
  });

  it('re-despachar es del Dueño y de los Admin', async () => {
    signIn('todos-los-estados', 'carla');
    const error = await fail(apiCall(projects.redispatchProject, { params: { id: projectByTitle('Falló: CapacityTimeoutError').id } }));
    expect(error.status).toBe(403);
  });

  it('borrar un proyecto con un render abierto responde 409 con el mensaje del servidor; cuando termina, se borra', async () => {
    const project = projectByTitle('Charla en la UTN');
    const failed = project.clips.find((clip) => clip.status === 'failed');
    if (!failed) throw new Error('falta el clip fallido');
    await apiCall(clips.retryClip, { params: { id: failed.id } });
    const busy = await fail(apiCall(projects.deleteProject, { params: { id: project.id } }));
    expect([busy.status, busy.code, busy.message]).toEqual([409, 'project_processing', 'El proyecto se está procesando: esperá a que termine para borrarlo.']);
    expect(loadDb().projects.some((item) => item.id === project.id)).toBe(true);

    // Ended, but its reservation is not settled yet.
    edit('Charla en la UTN', (item) => {
      item.status = 'partial';
    });
    const open = await fail(apiCall(projects.deleteProject, { params: { id: project.id } }));
    expect([open.status, open.code]).toEqual([409, 'reservation_open']);
    expect(open.message).toMatch(/tokens reservados que todavía no se liquidaron/);

    edit('Charla en la UTN', (item) => {
      item.status = 'rendering';
    });
    advance(SIM.clipMs * 2);
    await expect(apiCall(projects.deleteProject, { params: { id: project.id } })).resolves.toBeUndefined();
  });

  it('un proyecto en revisión se puede borrar: su procesamiento ya se cobró, no queda ninguna reserva', async () => {
    // A seeded one…
    const seeded = projectByTitle('Podcast Ep. 42');
    expect(seeded.status).toBe('awaiting_approval');
    expect(transaction(String(seeded.metadata.reservation_id))?.status).toBe('COMPLETED');
    await expect(apiCall(projects.deleteProject, { params: { id: seeded.id } })).resolves.toBeUndefined();
    // …and one that went through the pipeline: charged when its clips were selected, before the review.
    const reservedBefore = (await apiCall(billing.getWallet)).reserved_balance;
    const created = await apiCall(projects.createProject, { body: { source_file: new File(['x'], 'clase-8min.mp4', { type: 'video/mp4' }) } });
    expect(transaction(created.cost_details.transaction_id)?.status).toBe('RESERVED');
    const busy = await fail(apiCall(projects.deleteProject, { params: { id: created.id } }));
    expect([busy.status, busy.code]).toEqual([409, 'project_processing']);
    advance(SIM.queueMs + SIM.stageMs * 5);
    const inReview = await apiCall(projects.getProject, { params: { id: created.id } });
    expect(inReview.status).toBe('awaiting_approval');
    expect(transaction(created.cost_details.transaction_id)?.status).toBe('COMPLETED');
    expect((await apiCall(billing.getWallet)).reserved_balance).toBeCloseTo(reservedBefore, 2);
    await expect(apiCall(projects.deleteProject, { params: { id: created.id } })).resolves.toBeUndefined();
    expect(loadDb().projects.some((item) => item.id === created.id)).toBe(false);
  });

  it('un clip no se borra mientras su proyecto renderiza', async () => {
    const project = projectByTitle('Charla en la UTN');
    const failed = project.clips.find((clip) => clip.status === 'failed');
    const ready = project.clips.find((clip) => clip.status === 'completed');
    if (!failed || !ready) throw new Error('faltan clips');
    await apiCall(clips.retryClip, { params: { id: failed.id } });
    const error = await fail(apiCall(clips.deleteClip, { params: { id: ready.id } }));
    expect([error.status, error.code]).toEqual([409, 'project_rendering']);
  });
});
