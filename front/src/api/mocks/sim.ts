import { formatNumber, formatTokens, plural } from '@/lib/format';
import type { Role } from '../contracts/common';
import type { PipelineStage } from '../contracts/projects';
import { KIND, mid, newId, type MockActivity, type MockClip, type MockDb, type MockProject, type MockTransaction, type MockWallet, type PaidRender } from './db';
import { buildClips, PLAN_IDS, priceOf, stageStatusFor } from './seed';

/** Pace of the simulated pipeline: slow enough to see each step with the 5 s polling, fast enough to click through. */
export const SIM = {
  queueMs: 3_000,
  stageMs: 6_000,
  clipMs: 4_000,
  /** How long the clips of a paid render wait in `draft` before a worker takes them. */
  claimMs: 1_500,
  postQueuedMs: 4_000,
  postProcessingMs: 6_000,
  purchaseMs: 3_000,
  previewPendingMs: 2_000,
  previewRenderMs: 5_000,
} as const;

const RUNNING_STAGE: PipelineStage[] = ['audio_extracted', 'transcribed', 'moderated', 'clips_selected'];
const FINISHED_STAGE: PipelineStage[] = ['uploaded', 'audio_extracted', 'transcribed', 'moderated'];

const iso = (ms: number) => new Date(ms).toISOString();
const round2 = (value: number) => Math.round((value + Number.EPSILON) * 100) / 100;

export function walletOf(db: MockDb, workspaceId: string): MockWallet {
  let wallet = db.wallets.find((item) => item.workspace === workspaceId);
  if (!wallet) {
    // Same self-healing as the backend: a workspace always has a wallet.
    wallet = { id: newId(), workspace: workspaceId, available_balance: 0, reserved_balance: 0, updated_at: iso(Date.now()) };
    db.wallets.push(wallet);
  }
  return wallet;
}

export class InsufficientFunds extends Error {
  constructor(readonly required: number) {
    super('Saldo insuficiente');
  }
}

/** wallet_service.reserve_funds: moves the amount from available to reserved, or fails with what was required. */
export function reserveFunds(
  db: MockDb,
  input: { workspace: string; amount: number; userId: string | null; description: string; project: { id: string; title: string } | null; category: MockTransaction['category'] },
  now: number,
): MockTransaction {
  const wallet = walletOf(db, input.workspace);
  if (wallet.available_balance < input.amount) throw new InsufficientFunds(input.amount);
  wallet.available_balance = round2(wallet.available_balance - input.amount);
  wallet.reserved_balance = round2(wallet.reserved_balance + input.amount);
  wallet.updated_at = iso(now);
  const user = db.users.find((item) => item.id === input.userId);
  const transaction: MockTransaction = {
    id: newId(),
    workspace: input.workspace,
    wallet: wallet.id,
    created_by: input.userId,
    amount: input.amount,
    reserved_amount: input.amount,
    transaction_type: 'SPEND',
    status: 'RESERVED',
    description: input.description,
    created_at: iso(now),
    settled_at: null,
    kind: 'reserved',
    created_by_detail: user ? { id: user.id, name: user.name } : null,
    project: input.project,
    category: input.category,
  };
  db.transactions.unshift(transaction);
  return transaction;
}

/** The job was delivered: the reservation becomes a charge. */
export function settleReservation(db: MockDb, transactionId: string, now: number): void {
  const transaction = db.transactions.find((item) => item.id === transactionId);
  if (!transaction || transaction.status !== 'RESERVED') return;
  const wallet = walletOf(db, transaction.workspace);
  wallet.reserved_balance = round2(Math.max(0, wallet.reserved_balance - transaction.amount));
  wallet.updated_at = iso(now);
  transaction.status = 'COMPLETED';
  transaction.kind = 'charged';
  transaction.settled_at = iso(now);
}

/**
 * Part of the job was delivered (some clips failed): like the backend's commit_partial_reservation,
 * the SPEND row keeps only what is charged and becomes COMPLETED; the rest goes back to the
 * available balance without a row of its own. Returns what was returned.
 */
export function settleReservationPartially(db: MockDb, transactionId: string, amount: number, now: number): number {
  const transaction = db.transactions.find((item) => item.id === transactionId);
  if (!transaction || transaction.status !== 'RESERVED') return 0;
  const charged = round2(amount);
  if (charged >= transaction.amount) {
    settleReservation(db, transactionId, now);
    return 0;
  }
  if (charged <= 0) return releaseReservation(db, transactionId, now);
  const wallet = walletOf(db, transaction.workspace);
  const released = round2(transaction.amount - charged);
  wallet.reserved_balance = round2(Math.max(0, wallet.reserved_balance - transaction.amount));
  wallet.available_balance = round2(wallet.available_balance + released);
  wallet.updated_at = iso(now);
  transaction.amount = charged;
  transaction.status = 'COMPLETED';
  transaction.kind = 'charged';
  transaction.settled_at = iso(now);
  return released;
}

/** The job failed: the reservation goes back to available (SPEND + FAILED, shown as "Devuelto"). */
export function releaseReservation(db: MockDb, transactionId: string, now: number): number {
  const transaction = db.transactions.find((item) => item.id === transactionId);
  if (!transaction || transaction.status !== 'RESERVED') return 0;
  const wallet = walletOf(db, transaction.workspace);
  wallet.reserved_balance = round2(Math.max(0, wallet.reserved_balance - transaction.amount));
  wallet.available_balance = round2(wallet.available_balance + transaction.amount);
  wallet.updated_at = iso(now);
  transaction.status = 'FAILED';
  transaction.kind = 'released';
  transaction.settled_at = iso(now);
  return transaction.amount;
}

/**
 * How a reservation ended, as the backend says it in the three activity items of the transaction
 * (services/project_activity.py · `_reservation_state`).
 */
export function reservationStateOf(transaction: Pick<MockTransaction, 'status' | 'amount' | 'reserved_amount'>): NonNullable<MockActivity['reservation_state']> {
  if (transaction.status === 'COMPLETED') return transaction.reserved_amount !== null && transaction.reserved_amount > transaction.amount ? 'partially_charged' : 'charged';
  return transaction.status === 'FAILED' ? 'refunded' : 'reserved';
}

/** The activity of a project as the API serves it: each token item of a known reservation says how that reservation ended. */
export function serveActivity(db: MockDb, projectId: string): Array<Omit<MockActivity, 'project' | '_transaction'>> {
  return db.activity
    .filter((item) => item.project === projectId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .map(({ project: _project, _transaction, ...item }) => {
      void _project;
      const transaction = _transaction && item.type.startsWith('tokens_') ? db.transactions.find((entry) => entry.id === _transaction) : undefined;
      return transaction ? { ...item, reservation_state: reservationStateOf(transaction) } : item;
    });
}

/** Releases every open reservation of the project, with one refund item each. Returns the total. */
function refundReservations(db: MockDb, project: MockProject, now: number): number {
  let refunded = 0;
  for (const id of project._reservations) {
    const amount = releaseReservation(db, id, now);
    if (amount > 0) addActivity(db, project, 'tokens_refunded', 'Devolución de la reserva', now, { amount, transactionId: id });
    refunded += amount;
  }
  return refunded;
}

export function addActivity(
  db: MockDb,
  project: MockProject,
  type: MockDb['activity'][number]['type'],
  description: string,
  now: number,
  options: { actorId?: string | null; amount?: number | null; transactionId?: string } = {},
): void {
  const actor = options.actorId ? db.users.find((item) => item.id === options.actorId) : null;
  db.activity.unshift({
    id: newId(),
    project: project.id,
    type,
    actor: actor ? { id: actor.id, name: actor.name } : null,
    created_at: iso(now),
    description,
    amount: options.amount ?? null,
    ...(options.transactionId ? { _transaction: options.transactionId } : {}),
  });
}

/**
 * In-app notification for every active member of the workspace, like the backend
 * (apps/notifications/services.py). With `only`, just for that user, if they are still a member.
 */
export function notify(
  db: MockDb,
  workspace: string,
  input: { type: MockDb['notifications'][number]['type']; title: string; body: string; project_id?: string | null; post_id?: string | null },
  now: number,
  only?: string | null,
): void {
  const members = db.members.filter(
    (member) => member.workspace === workspace && member.status === 'active' && member.user && (only === undefined || member.user === only),
  );
  for (const member of members) {
    db.notifications.unshift({
      id: newId(),
      workspace,
      user: member.user as string,
      type: input.type,
      title: input.title,
      body: input.body,
      created_at: iso(now),
      read_at: null,
      project_id: input.project_id ?? null,
      post_id: input.post_id ?? null,
    });
  }
  // Keep the last 20 per user and workspace, like the panel.
  const seen = new Map<string, number>();
  db.notifications = db.notifications.filter((item) => {
    const key = `${item.user}:${item.workspace}`;
    const count = (seen.get(key) ?? 0) + 1;
    seen.set(key, count);
    return count <= 20;
  });
}

/**
 * The body of `render_finished`, like the backend: what is ready, or that part failed and what went back.
 * A render that ends complete and still returns tokens had a clip rendered again that failed: it kept its file.
 */
function renderFinishedBody(ready: number, partial: boolean, returned: number): string {
  const refund = returned > 0 ? ` Te devolvimos ${formatTokens(round2(returned))}.` : '';
  if (partial) return `Parte del render falló.${refund}`;
  if (returned > 0) return `No pudimos volver a generar todos los clips: los que ya tenías quedaron como estaban.${refund}`;
  return ready <= 1 ? 'Tu video está listo.' : `${ready} clips listos.`;
}

export function roleIn(db: MockDb, workspaceId: string, userId: string): Role | null {
  const workspace = db.workspaces.find((item) => item.id === workspaceId);
  if (!workspace) return null;
  if (workspace.owner === userId) return 'owner';
  const member = db.members.find((item) => item.workspace === workspaceId && item.user === userId && item.status === 'active');
  return member ? member.role : null;
}

function failProject(db: MockDb, project: MockProject, code: string, now: number): void {
  project.status = 'failed';
  project.pipeline_error_code = code;
  project.pipeline_error_at = iso(now);
  project.pipeline_stage_status = stageStatusFor(project.pipeline_stage, 'failed');
  project.eta_seconds = null;
  // The stage it stopped at keeps its start and gets when it stopped (stage_timings of a failed project).
  project.stage_timings = Object.fromEntries(
    Object.entries(project.stage_timings ?? {}).map(([name, timing]) => [name, timing.duration_s === null && timing.started_at ? { ...timing, finished_at: iso(now) } : timing]),
  );
  if (project.cost) project.cost = { ...project.cost, state: 'refunded' };
  delete project._sim;
  addActivity(db, project, 'project_failed', 'El proceso falló', now);
  const refunded = refundReservations(db, project, now);
  notify(db, project.workspace, { type: 'project_failed', title: `${project.title} no se pudo procesar`, body: refunded > 0 ? `Te devolvimos ${formatTokens(refunded)}.` : 'No se cobró nada.', project_id: project.id }, now);
}

export function startRender(project: MockProject, now: number, sim: { failOneClip?: boolean } = {}): void {
  project.status = 'rendering';
  project.pipeline_stage = 'render_dispatched';
  project.pipeline_stage_status = stageStatusFor('clips_selected', null);
  for (const clip of project.clips) if (clip.status !== 'completed' && clip.status !== 'published') clip.status = 'rendering';
  project._sim = { phase: 'render', startedAt: now, failOneClip: sim.failOneClip };
}

const RESOLUTION_RANK = { '720p': 1, '1080p': 2, '4K': 3 } as const;

/** `_reserve_auto_render`: returns why it could not be paid, or null when the reservation was made. */
export function reserveAutoRender(db: MockDb, project: MockProject, now: number): 'insufficient_funds' | 'plan_limit' | null {
  const seconds = project.clips.reduce((sum, clip) => sum + Math.max(0, clip.end_time - clip.start_time), 0);
  const workspace = db.workspaces.find((item) => item.id === project.workspace);
  const plan = db.plans.find((item) => item.id === (workspace?.subscription_plan ?? PLAN_IDS.free));
  const resolution = project.metadata.render_resolution ?? '720p';
  const billed = Math.ceil(Math.round(seconds * 1000) / 1000);
  if (plan && (billed > plan.max_video_duration_seconds || RESOLUTION_RANK[resolution] > RESOLUTION_RANK[plan.max_resolution])) return 'plan_limit';
  try {
    const transaction = reserveFunds(
      db,
      {
        workspace: project.workspace,
        amount: priceOf(billed, resolution, false, plan?.base_discount_rate ?? 0),
        userId: project.uploaded_by,
        description: `Render automático: ${project.title}`.slice(0, 255),
        project: { id: project.id, title: project.title },
        category: 'render',
      },
      now,
    );
    project.metadata = {
      ...project.metadata,
      auto_render_reservation_id: transaction.id,
      auto_render_seconds: Math.round(seconds * 1000) / 1000,
      auto_render_clip_ids: project.clips.map((clip) => clip.id),
    };
    project._reservations.push(transaction.id);
    return null;
  } catch (error) {
    if (error instanceof InsufficientFunds) return 'insufficient_funds';
    throw error;
  }
}

function tickIngest(db: MockDb, project: MockProject, now: number): void {
  const sim = project._sim;
  if (!sim) return;
  const elapsed = now - sim.startedAt;
  if (elapsed < SIM.queueMs) return;
  // A re-dispatched project stays `failed` with its code until the task reopens it.
  if (project.status === 'failed') {
    project.pipeline_error_code = '';
    project.pipeline_error_at = null;
    project.pipeline_stage = 'uploaded';
  }
  project.status = 'ingesting';
  project.pipeline_attempts = 1;
  let time = elapsed - SIM.queueMs;
  const wait = sim.waitMs ?? 0;
  // Capacity wait: after the first stage, the next one sits in pending_capacity.
  if (wait > 0 && time >= SIM.stageMs && time < SIM.stageMs + wait) {
    project.pipeline_stage = 'audio_extracted';
    project.pipeline_stage_status = stageStatusFor('audio_extracted', 'pending_capacity');
    return;
  }
  if (wait > 0 && time >= SIM.stageMs + wait) time -= wait;
  const step = Math.floor(time / SIM.stageMs);

  // Like the backend: the finished stages with their times, and the one at `running` with its start alone.
  const stageStart = (index: number) => sim.startedAt + SIM.queueMs + (index > 0 ? wait : 0) + index * SIM.stageMs;
  const timingsAt = (running: number) =>
    Object.fromEntries(
      RUNNING_STAGE.slice(0, running + 1).map((name, index) => [
        name,
        index < running
          ? { started_at: iso(stageStart(index)), finished_at: iso(stageStart(index) + SIM.stageMs), duration_s: SIM.stageMs / 1000 }
          : { started_at: iso(stageStart(index)), finished_at: null, duration_s: null },
      ]),
    );
  if (sim.failAt) {
    const failStep = RUNNING_STAGE.indexOf(sim.failAt.stage);
    if (step > failStep) {
      // The stage it fails at started, even when no tick saw it running.
      project.stage_timings = timingsAt(failStep);
      project.pipeline_stage = FINISHED_STAGE[failStep] ?? 'uploaded';
      failProject(db, project, sim.failAt.code, now);
      return;
    }
  }
  if (step < RUNNING_STAGE.length) {
    const finished = FINISHED_STAGE[step] ?? 'uploaded';
    project.pipeline_stage = finished;
    project.pipeline_stage_status = stageStatusFor(finished, 'running');
    project.stage_timings = timingsAt(step);
    if (step >= 2 && project._transcriptSeed === null) {
      project._transcriptSeed = Math.floor(sim.startedAt % 100_000);
      project.proxy_r2_key = `videos/proxy/${project.id}.mp4`;
    }
    const remaining = (RUNNING_STAGE.length - step) * SIM.stageMs - (time % SIM.stageMs);
    project.eta_seconds = Math.max(1, Math.round(remaining / 1000));
    return;
  }

  // Ingestion finished: the clips are selected.
  const duration = project.duration_seconds ?? 600;
  if (project._transcriptSeed === null) project._transcriptSeed = Math.floor(sim.startedAt % 100_000);
  project.proxy_r2_key = `videos/proxy/${project.id}.mp4`;
  project.pipeline_stage = 'clips_selected';
  project.pipeline_stage_status = stageStatusFor('clips_selected', null);
  project.eta_seconds = null;
  const stages = ['uploaded', 'audio_extracted', 'transcribed', 'moderated', 'clips_selected'];
  project.stage_timings = Object.fromEntries(
    stages.map((name, index) => [
      name,
      { started_at: iso(sim.startedAt + SIM.queueMs + index * SIM.stageMs), finished_at: iso(sim.startedAt + SIM.queueMs + (index + 1) * SIM.stageMs), duration_s: SIM.stageMs / 1000 },
    ]),
  );
  if (project.metadata.pte_prompt && !project.metadata.pte) {
    project.metadata.pte = {
      status: 'applied',
      applied: { max_clips: 5, selection_criteria: 'lo que pediste en el prompt' },
      ignored: project.metadata.render_resolution === '720p' ? [] : [{ field: 'render_resolution', reason: 'explicit' }],
    };
    addActivity(db, project, 'prompt_applied', 'La IA aplicó la configuración del prompt', now);
  }
  if (project.project_type === 'single_edit') {
    const clip: MockClip = {
      id: newId(),
      project: project.id,
      title: project.title,
      s3_object_key: null,
      start_time: 0,
      end_time: duration,
      virality_score: 0,
      ai_reasoning: '',
      status: 'rendering',
      created_at: iso(now),
      layers: [],
      preview_url: null,
      thumbnail_url: null,
      error_code: null,
      posts: [],
    };
    project.clips = [clip];
    startRender(project, now, { failOneClip: sim.failOneClip });
    return;
  }
  // `_commit_ingestion_reservation`: a clips project pays its processing here, before the review.
  // (A whole video is paid by the same reservation when its render ends.)
  const creation = db.transactions.find((item) => item.id === project.metadata.reservation_id);
  if (creation?.status === 'RESERVED') {
    settleReservation(db, creation.id, now);
    if (project.cost) project.cost = { ...project.cost, state: 'charged' };
    addActivity(db, project, 'tokens_charged', processingChargeLabel(db, creation.id), now, { amount: -creation.amount, transactionId: creation.id });
  }
  const count = Math.min(20, Math.max(1, project.metadata.max_clips ?? 8));
  const seedIndex = Math.floor(sim.startedAt % 9_000) + 500;
  project.clips = buildClips(project.id, seedIndex, duration, { count, statuses: ['draft'] }, now).map((clip) => ({ ...clip, id: newId() }));
  project.ai_rationale_log = { suggestions_count: project.clips.length };
  if (project.auto_render_bypass) {
    // "Renderizar sin revisar": the render of the chosen clips is reserved like an approval.
    const skipped = reserveAutoRender(db, project, now);
    if (!skipped) {
      project.approved_segments = project.clips.map((clip) => ({ start: clip.start_time, end: clip.end_time }));
      startRender(project, now, { failOneClip: sim.failOneClip });
      return;
    }
    // It cannot be paid: the project waits in the review with the reason.
    project.metadata = { ...project.metadata, auto_render_skipped: skipped };
  }
  project.status = 'awaiting_approval';
  delete project._sim;
  addActivity(db, project, 'review_ready', `Listo para revisar: ${project.clips.length} clips sugeridos`, now);
  notify(db, project.workspace, { type: 'project_ready', title: `${project.title} está listo para revisar`, body: `Encontramos ${project.clips.length} clips.`, project_id: project.id }, now);
}

function tickRender(db: MockDb, project: MockProject, now: number): void {
  const sim = project._sim;
  if (!sim) return;
  const paid = project._paidRender;
  // A paid render leaves its clips in `draft` until a worker claims them (`render_queued`).
  if (paid && now - sim.startedAt < SIM.claimMs) return;
  if (paid) {
    for (const clip of project.clips) {
      if (!paid.clipIds.includes(clip.id) || clip.status !== 'draft') continue;
      clip.status = 'rendering';
      delete clip.render_queued;
    }
  }
  const pending = project.clips.filter((clip) => clip.status === 'rendering');
  const alreadyDone = project.clips.length - pending.length;
  const finishedNow = Math.min(pending.length, Math.floor((now - sim.startedAt - (paid ? SIM.claimMs : 0)) / SIM.clipMs));
  pending.slice(0, finishedNow).forEach((clip, index) => {
    const last = alreadyDone + index === project.clips.length - 1;
    const fails = clip._renderFails === true;
    delete clip._renderFails;
    if (fails || (sim.failOneClip && last)) {
      clip.status = 'failed';
      clip.error_code = 'RenderError';
    } else {
      clip.status = 'completed';
      clip.s3_object_key = `clips/${project.id}/${clip.id}.mp4`;
      clip.error_code = null;
    }
  });
  if (finishedNow < pending.length) return;
  if (project._paidRender) {
    finishPaidRender(db, project, project._paidRender, now);
    return;
  }

  const failed = project.clips.filter((clip) => clip.status === 'failed').length;
  project.status = failed > 0 ? 'partial' : 'completed';
  project.pipeline_stage = failed > 0 ? 'partial' : 'completed';
  delete project._sim;
  const single = project.project_type === 'single_edit';
  if (single && failed > 0) {
    // The whole-video edit is charged on delivery and refunded when the render fails.
    const refunded = refundReservations(db, project, now);
    if (project.cost) project.cost = { ...project.cost, state: 'refunded' };
    notify(db, project.workspace, { type: 'project_failed', title: `${project.title} no se pudo generar`, body: refunded > 0 ? `Te devolvimos ${formatTokens(refunded)}.` : 'No se cobró nada.', project_id: project.id }, now);
    return;
  }
  let returned = 0;
  const delivered = project.clips.length > 0 ? (project.clips.length - failed) / project.clips.length : 1;
  // One charge (and one refund) per reservation, like the backend: each says which transaction it settles.
  const settled: Array<{ id: string; charged: number; returned: number; render: boolean; description: string }> = [];
  for (const id of project._reservations) {
    const transaction = db.transactions.find((item) => item.id === id);
    if (transaction?.status !== 'RESERVED') continue;
    const render = transaction.category === 'render';
    let back = 0;
    // The render of the clips that failed is not charged; the processing of the video is.
    if (failed > 0 && render) back = settleReservationPartially(db, id, transaction.amount * delivered, now);
    else settleReservation(db, id, now);
    returned += back;
    const after = db.transactions.find((item) => item.id === id);
    settled.push({ id, charged: after?.status === 'COMPLETED' ? after.amount : 0, returned: back, render, description: transaction.description });
  }
  for (const item of settled) {
    if (item.returned > 0) addActivity(db, project, 'tokens_refunded', 'Devolución por los clips que no se generaron', now, { amount: round2(item.returned), transactionId: item.id });
  }
  if (project.cost) project.cost = { ...project.cost, state: 'charged' };
  // Every clip failed and its render came back whole: it delivered nothing (project_activity.py · `_refunded_render_text`).
  const renders = settled.filter((item) => item.render);
  const nothing = renders.length > 0 && renders.every((item) => item.charged === 0 && item.returned > 0);
  const back = renders.reduce((sum, item) => sum + item.returned, 0);
  addActivity(db, project, 'render_finished', nothing ? `No se pudo renderizar: no se generó ningún clip. Te devolvimos ${formatTokens(round2(back))}.` : renderFinishedText(project), now);
  for (const item of settled) {
    if (item.charged > 0) addActivity(db, project, 'tokens_charged', item.render ? renderChargeLabel(item.description) : processingChargeLabel(db, item.id), now, { amount: -item.charged, transactionId: item.id });
  }
  const ready = project.clips.length - failed;
  notify(db, project.workspace, { type: 'render_finished', title: `${project.title}: terminó el render`, body: renderFinishedBody(ready, failed > 0, returned), project_id: project.id }, now);
}

const CLIP_RENDER_PREFIXES = [
  ['Render de clip: ', 'renderizar'],
  ['Reintento de clip: ', 'reintentar'],
] as const;

/** The title a reservation of one clip was written with (project_activity.py · `_clip_title`), or ''. */
function clipRenderTitle(description: string): string {
  for (const [prefix] of CLIP_RENDER_PREFIXES) {
    if (description.startsWith(prefix)) return description.slice(prefix.length).trim();
  }
  return '';
}

/**
 * What the charge of a render is called, like the backend (project_activity.py · `_purpose`): after its
 * reservation ("Reserva por reintentar «Clip»" is charged as "Cobro por reintentar «Clip»"), so two charges
 * of the same amount say which is which. The approval's is "the render".
 */
export function renderChargeLabel(description: string): string {
  for (const [prefix, what] of CLIP_RENDER_PREFIXES) {
    const title = description.startsWith(prefix) ? description.slice(prefix.length).trim() : '';
    if (title) return `Cobro por ${what} «${title}»`;
  }
  if (description.startsWith('Render con otro estilo: ')) return 'Cobro por renderizar todo de nuevo';
  if (description.startsWith('Render automático: ') || description.startsWith('Render sin revisar: ')) return 'Cobro por renderizar sin revisar';
  return 'Cobro del render';
}

/**
 * What the charge of the processing is called, like the backend (project_activity.py · `_REDISPATCH`):
 * the one a re-dispatch reserved again («Reserva por volver a procesar») is charged as such.
 */
function processingChargeLabel(db: MockDb, transactionId: string): string {
  const again = db.activity.some((item) => item._transaction === transactionId && item.type === 'tokens_reserved' && item.description === 'Reserva por volver a procesar');
  return again ? 'Cobro por volver a procesar' : 'Cobro del procesamiento';
}

/** What `render_finished` says of a render that delivered something, like the backend (project_activity.py · `build_activity`): the clips the project has. */
function renderFinishedText(project: MockProject): string {
  const ready = project.clips.filter((clip) => clip.status === 'completed' || clip.status === 'published').length;
  const failed = project.clips.filter((clip) => clip.status === 'failed').length;
  if (project.project_type === 'single_edit') return failed > 0 ? 'El render del video falló' : 'Terminó el render del video';
  const clips = plural(ready, 'clip listo', 'clips listos');
  return failed > 0 ? `Terminó el render: ${clips} y ${failed} con error` : `Terminó el render: ${clips}`;
}

/**
 * What `render_finished` says of a paid render that was returned whole, like the backend
 * (project_activity.py · `_refunded_render_text`): it delivered nothing. The clips it was for that are
 * delivered all the same are the ones the user had (given back as they were): all of them, "the clip(s)
 * before stayed"; only some (the others were failed before it and still are), "the ones you had stayed".
 */
function refundedRenderText(project: MockProject, paid: PaidRender, description: string, returned: number): string {
  const refund = `Te devolvimos ${formatTokens(round2(returned))}.`;
  const single = project.project_type === 'single_edit';
  const kept = paid.clipIds.filter((id) => ['completed', 'published'].includes(project.clips.find((clip) => clip.id === id)?.status ?? '')).length;
  if (kept > 0 && kept === paid.clipIds.length) {
    const what = single ? 'quedó el video anterior' : kept === 1 ? 'quedó el clip anterior' : 'quedaron los clips anteriores';
    return `No se pudo volver a renderizar: ${what}. ${refund}`;
  }
  if (kept > 0) return `No se pudo volver a renderizar: ${kept === 1 ? 'quedó el clip que ya tenías' : 'quedaron los clips que ya tenías'}. ${refund}`;
  // A retry is of a failed clip, and so is the render of one clip that ended failed: there was nothing to keep.
  const clipFailed = paid.scope === 'clip' && paid.clipIds.some((id) => project.clips.find((clip) => clip.id === id)?.status === 'failed');
  if (description.startsWith('Reintento de clip: ') || clipFailed) return `No se pudo renderizar el clip: sigue con error. ${refund}`;
  if (single) return `El render del video falló. ${refund}`;
  return `No se pudo renderizar: no se generó ningún clip. ${refund}`;
}

/**
 * Closes a paid render (`finalize_project_render` for CLIP_RENDER): its reservation is charged by
 * the seconds delivered (`_clip_render_charge`: amount × delivered / reserved) and the rest goes
 * back. A clip that was already delivered and whose new render failed gets back what it had.
 */
function finishPaidRender(db: MockDb, project: MockProject, paid: PaidRender, now: number): void {
  delete project._paidRender;
  delete project._sim;
  const clips = project.clips.filter((clip) => paid.clipIds.includes(clip.id));
  let reserved = 0;
  let delivered = 0;
  for (const clip of clips) {
    const seconds = Math.max(0, clip.end_time - clip.start_time);
    reserved += seconds;
    const before = paid.previous[clip.id];
    if (clip.status === 'completed') {
      delivered += seconds;
      // A published clip is still published: only its file changed.
      if (before?.status === 'published') clip.status = 'published';
    } else if (clip.status === 'failed' && before) {
      clip.status = before.status;
      clip.s3_object_key = before.key;
      clip.error_code = null;
    }
  }
  const failed = project.clips.filter((clip) => clip.status === 'failed').length;
  project.status = failed > 0 ? 'partial' : 'completed';
  project.pipeline_stage = failed > 0 ? 'partial' : 'completed';
  project.pipeline_stage_status = stageStatusFor('clips_selected', null);
  // The close ends the step only if this render started it (`_metadata_with_render_finished`).
  const running = project.stage_timings?.render_dispatched;
  if (running?.started_at && running.duration_s === null) {
    const started = new Date(running.started_at).getTime();
    project.stage_timings = { ...project.stage_timings, render_dispatched: { started_at: running.started_at, finished_at: iso(now), duration_s: Math.max(0, (now - started) / 1000) } };
  }
  const transaction = db.transactions.find((item) => item.id === paid.reservation);
  const amount = transaction?.status === 'RESERVED' ? transaction.amount : 0;
  const charge = reserved <= 0 || delivered >= reserved ? amount : round2(amount * (delivered / reserved));
  const returned = settleReservationPartially(db, paid.reservation, charge, now);
  const description = transaction?.description ?? '';
  // Returned whole: that render delivered nothing, whatever the project has from before.
  const nothing = amount > 0 && returned >= amount;
  // Charged in part with nothing left in error: what it could not generate got back the file it had (the bell's words).
  const restored = !nothing && returned > 0 && failed === 0 && project.project_type !== 'single_edit';
  // One `render_finished` per render, each with its own words: the render of one clip names it.
  const clipTitle = paid.scope === 'clip' ? clipRenderTitle(description) : '';
  const finished = nothing
    ? refundedRenderText(project, paid, description, returned)
    : clipTitle
      ? `Terminó el render de «${clipTitle}»`
      : restored
        ? `No pudimos volver a generar todos los clips: los que ya tenías quedaron como estaban. Te devolvimos ${formatTokens(round2(returned))}.`
        : renderFinishedText(project);
  addActivity(db, project, 'render_finished', finished, now);
  if (amount - returned > 0) addActivity(db, project, 'tokens_charged', renderChargeLabel(description), now, { amount: -round2(amount - returned), transactionId: paid.reservation });
  if (returned > 0) addActivity(db, project, 'tokens_refunded', 'Devolución por lo que no se generó', now, { amount: round2(returned), transactionId: paid.reservation });
  if (paid.scope === 'clip') {
    // The render of one clip is not the project's (notifications/services.py · `notify_clip_render_finished`):
    // delivered, it tells nobody; returned whole, the bell says what the activity says, word for word.
    if (nothing) notify(db, project.workspace, { type: 'render_finished', title: `${project.title}: no se pudo renderizar un clip`, body: finished, project_id: project.id }, now);
    return;
  }
  notify(
    db,
    project.workspace,
    {
      type: 'render_finished',
      title: `${project.title}: terminó el render`,
      body: renderFinishedBody(project.clips.length - failed, failed > 0, returned),
      project_id: project.id,
    },
    now,
  );
}

/** What the SEO generation "writes": the first text, and the ones "Regenerar" rotates through. */
const SEO_VARIANTS: Array<{ caption: (title: string) => string; hashtags: string[] }> = [
  { caption: (title) => `Lo mejor de «${title}», en menos de un minuto.`, hashtags: ['clips', 'onecreator', 'viral'] },
  { caption: (title) => `${title}: lo que nadie te cuenta, sin vueltas.`, hashtags: ['clips', 'tips', 'aprender', 'parati'] },
  { caption: (title) => `Guardá este video. «${title}» te va a servir más de una vez.`, hashtags: ['clips', 'guardalo', 'consejos'] },
];

function tickPosts(db: MockDb, now: number): void {
  for (const post of db.posts) {
    // The beat: a scheduled post whose time arrived goes to the queue. "Publish now" is one of them too
    // (the backend leaves it SCHEDULED for dispatch_scheduled_posts_batch): it keeps its clock and its fate.
    if (post.status === 'SCHEDULED' && new Date(post.publish_at).getTime() <= now) {
      post.status = 'QUEUED';
      post._sim = { ...post._sim, startedAt: post._sim?.startedAt ?? now };
    }
    if (!post._sim) continue;
    const elapsed = now - post._sim.startedAt;
    if (post.seo_status === 'pending' && elapsed > 2_000) {
      post.seo_status = 'ready';
      const regen = post._sim.regen ?? 0;
      let index = regen % SEO_VARIANTS.length;
      // A regeneration never brings back the text that is already there.
      if (regen > 0 && SEO_VARIANTS[index]?.caption(post.clip.title) === post.generated_caption) index = (index + 1) % SEO_VARIANTS.length || 1;
      const variant = SEO_VARIANTS[index] as (typeof SEO_VARIANTS)[number];
      post.generated_caption = variant.caption(post.clip.title);
      post.generated_hashtags = variant.hashtags;
      // A regeneration replaces the text (unless the user saved theirs after asking); the first generation only
      // fills what is empty.
      const replaces = regen > 0 && !post._sim.keepText;
      if (!post.caption || replaces) post.caption = post.generated_caption;
      if (post.hashtags.length === 0 || replaces) post.hashtags = post.generated_hashtags;
    }
    if (post.status !== 'QUEUED' && post.status !== 'PROCESSING') {
      if (post.seo_status !== 'pending') delete post._sim;
      continue;
    }
    if (elapsed < SIM.postQueuedMs) continue;
    if (elapsed < SIM.postQueuedMs + SIM.postProcessingMs) {
      post.status = 'PROCESSING';
      continue;
    }
    post.updated_at = iso(now);
    if (post._sim.fails) {
      post.status = 'FAILED';
      post.retry_count += 1;
      post.error_log = 'AyrshareError: upstream rejected the media';
      post.error_message = 'La red rechazó el video. Revisá que la cuenta siga conectada y volvé a intentarlo.';
      post.error_code = 'media_rejected';
      notify(db, post.workspace, { type: 'post_failed', title: 'No pudimos publicar un clip', body: post.clip.title, project_id: post.clip.project_id, post_id: post.id }, now);
    } else {
      post.status = 'PUBLISHED';
      post.post_url = `https://example.com/${post.platform.toLowerCase()}/post/${post.id.slice(0, 8)}`;
      post.error_log = null;
      post.error_message = null;
      post.error_code = null;
      const project = db.projects.find((item) => item.id === post.clip.project_id);
      const clip = project?.clips.find((item) => item.id === post.video_clip);
      if (clip) clip.status = 'published';
      if (project) addActivity(db, project, 'post_published', `Se publicó «${post.clip.title}»`, now);
    }
    delete post._sim;
  }
}

/** A payment was approved or rejected: for who started it, with the backend's texts. */
function notifyPayment(db: MockDb, purchase: MockDb['purchases'][number], now: number): void {
  if (!purchase._by) return;
  const approved = purchase.status === 'approved';
  const plan = purchase.plan_id ? db.plans.find((item) => item.id === purchase.plan_id)?.name : null;
  const tokens = formatNumber(purchase.tokens);
  const title = purchase.plan_id
    ? approved
      ? `Tu plan ahora es ${plan}`
      : `No pudimos cobrar el plan ${plan}`
    : approved
      ? `Se acreditaron ${tokens} tokens`
      : `No pudimos cobrar tu compra de ${tokens} tokens`;
  const body = !approved ? 'No se cobró nada. Podés intentarlo de nuevo.' : purchase.plan_id ? 'El pago se aprobó.' : 'Tu compra se aprobó.';
  notify(db, purchase.workspace, { type: approved ? 'payment_approved' : 'payment_rejected', title, body }, now, purchase._by);
}

function tickPurchases(db: MockDb, now: number): void {
  for (const purchase of db.purchases) {
    if (purchase.status !== 'pending' || purchase._settleAt === null || now < purchase._settleAt) continue;
    purchase.checkout_url = null;
    purchase._settleAt = null;
    if (purchase._reject) {
      purchase.status = 'rejected';
      purchase.failure_reason = 'El banco rechazó el pago. Probá con otra tarjeta.';
      notifyPayment(db, purchase, now);
      continue;
    }
    if (purchase.plan_id) {
      // The payment of a plan upgrade: the plan changes now and nothing touches the wallet.
      const workspace = db.workspaces.find((item) => item.id === purchase.workspace);
      if (workspace) {
        workspace.subscription_plan = purchase.plan_id;
        workspace.pending_plan = null;
        workspace.renews_at = iso(now + 30 * 24 * 3600 * 1000);
      }
      purchase.status = 'approved';
      notifyPayment(db, purchase, now);
      continue;
    }
    const wallet = walletOf(db, purchase.workspace);
    wallet.available_balance = round2(wallet.available_balance + purchase.tokens);
    wallet.updated_at = iso(now);
    purchase.status = 'approved';
    purchase.balance_after = wallet.available_balance;
    db.transactions.unshift({
      id: newId(),
      workspace: purchase.workspace,
      wallet: wallet.id,
      created_by: null,
      amount: purchase.tokens,
      reserved_amount: null,
      transaction_type: 'DEPOSIT',
      status: 'COMPLETED',
      description: `Compra de ${purchase.tokens} tokens`,
      created_at: iso(now),
      settled_at: null,
      kind: 'purchase',
      created_by_detail: null,
      project: null,
      category: 'purchase',
    });
    notifyPayment(db, purchase, now);
  }
}

function tickPreviews(db: MockDb, now: number): void {
  for (const preview of db.previews) {
    const elapsed = now - preview._startedAt;
    if (preview.status === 'pending' && elapsed >= SIM.previewPendingMs) preview.status = 'rendering';
    if (preview.status === 'rendering' && elapsed >= SIM.previewPendingMs + SIM.previewRenderMs) {
      preview.status = 'ready';
      // No real media in the mocks: a `mock:` URL, which <VideoPlayer> shows as its placeholder frame.
      preview.url = `mock://previews/${preview.preview_id}.mp4`;
    }
  }
  db.previews = db.previews.filter((preview) => new Date(preview.expires_at).getTime() > now);
}

/** Moves everything that is "in progress" forward to `now`. Runs before every mocked request. */
export function tick(db: MockDb, now: number = Date.now()): void {
  for (const project of db.projects) {
    if (project._sim?.phase === 'ingest') tickIngest(db, project, now);
    if (project._sim?.phase === 'render') tickRender(db, project, now);
  }
  tickPosts(db, now);
  tickPurchases(db, now);
  tickPreviews(db, now);
}

export { mid, KIND };
