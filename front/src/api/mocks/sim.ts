import type { Role } from '../contracts/common';
import type { PipelineStage } from '../contracts/projects';
import { KIND, mid, newId, type MockClip, type MockDb, type MockProject, type MockTransaction, type MockWallet, type PaidRender } from './db';
import { buildClips, PLAN_IDS, priceOf, stageStatusFor } from './seed';

/** Pace of the simulated pipeline: slow enough to see each step with the 5 s polling, fast enough to click through. */
export const SIM = {
  queueMs: 3_000,
  stageMs: 6_000,
  clipMs: 4_000,
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
    transaction_type: 'SPEND',
    status: 'RESERVED',
    description: input.description,
    created_at: iso(now),
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
  return transaction.amount;
}

export function addActivity(
  db: MockDb,
  project: MockProject,
  type: MockDb['activity'][number]['type'],
  description: string,
  now: number,
  options: { actorId?: string | null; amount?: number | null } = {},
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
  });
}

/** In-app notification for every active member of the workspace. */
export function notify(
  db: MockDb,
  workspace: string,
  input: { type: MockDb['notifications'][number]['type']; title: string; body: string; project_id?: string | null; post_id?: string | null },
  now: number,
): void {
  const members = db.members.filter((member) => member.workspace === workspace && member.status === 'active' && member.user);
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
  let refunded = 0;
  for (const id of project._reservations) refunded += releaseReservation(db, id, now);
  if (project.cost) project.cost = { ...project.cost, state: 'refunded' };
  delete project._sim;
  addActivity(db, project, 'project_failed', 'El proceso falló', now);
  if (refunded > 0) addActivity(db, project, 'tokens_refunded', 'Devolución de la reserva', now, { amount: refunded });
  notify(db, project.workspace, { type: 'project_failed', title: `${project.title} no se pudo procesar`, body: refunded > 0 ? `Te devolvimos ${refunded} tokens.` : 'No se cobró nada.', project_id: project.id }, now);
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

  if (sim.failAt) {
    const failStep = RUNNING_STAGE.indexOf(sim.failAt.stage);
    if (step > failStep) {
      project.pipeline_stage = FINISHED_STAGE[failStep] ?? 'uploaded';
      failProject(db, project, sim.failAt.code, now);
      return;
    }
  }
  if (step < RUNNING_STAGE.length) {
    const finished = FINISHED_STAGE[step] ?? 'uploaded';
    project.pipeline_stage = finished;
    project.pipeline_stage_status = stageStatusFor(finished, 'running');
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
  const pending = project.clips.filter((clip) => clip.status === 'rendering');
  const alreadyDone = project.clips.length - pending.length;
  const finishedNow = Math.min(pending.length, Math.floor((now - sim.startedAt) / SIM.clipMs));
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
    let refunded = 0;
    for (const id of project._reservations) refunded += releaseReservation(db, id, now);
    if (project.cost) project.cost = { ...project.cost, state: 'refunded' };
    if (refunded > 0) addActivity(db, project, 'tokens_refunded', 'Devolución de la reserva', now, { amount: refunded });
    notify(db, project.workspace, { type: 'project_failed', title: `${project.title} no se pudo generar`, body: `Te devolvimos ${refunded} tokens.`, project_id: project.id }, now);
    return;
  }
  let charged = 0;
  let returned = 0;
  const delivered = project.clips.length > 0 ? (project.clips.length - failed) / project.clips.length : 1;
  for (const id of project._reservations) {
    const transaction = db.transactions.find((item) => item.id === id);
    if (transaction?.status !== 'RESERVED') continue;
    // The render of the clips that failed is not charged; the processing of the video is.
    if (failed > 0 && transaction.category === 'render') returned += settleReservationPartially(db, id, transaction.amount * delivered, now);
    else settleReservation(db, id, now);
    charged += transaction.amount;
  }
  if (returned > 0) addActivity(db, project, 'tokens_refunded', 'Devolución por los clips que no se generaron', now, { amount: round2(returned) });
  if (project.cost) project.cost = { ...project.cost, state: 'charged' };
  addActivity(db, project, 'render_finished', 'Terminó el render', now);
  if (charged > 0) addActivity(db, project, 'tokens_charged', 'Cobro del procesamiento', now, { amount: -charged });
  const ready = project.clips.length - failed;
  notify(db, project.workspace, { type: 'render_finished', title: `${project.title}: terminó el render`, body: single ? 'Tu video está listo.' : `${ready} clips listos.`, project_id: project.id }, now);
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
  const transaction = db.transactions.find((item) => item.id === paid.reservation);
  const amount = transaction?.status === 'RESERVED' ? transaction.amount : 0;
  const charge = reserved <= 0 || delivered >= reserved ? amount : round2(amount * (delivered / reserved));
  const returned = settleReservationPartially(db, paid.reservation, charge, now);
  addActivity(db, project, 'render_finished', 'Terminó el render', now);
  if (amount - returned > 0) addActivity(db, project, 'tokens_charged', 'Cobro del render', now, { amount: -round2(amount - returned) });
  if (returned > 0) addActivity(db, project, 'tokens_refunded', 'Devolución por lo que no se generó', now, { amount: round2(returned) });
  notify(
    db,
    project.workspace,
    {
      type: 'render_finished',
      title: `${project.title}: terminó el render`,
      body: delivered >= reserved ? 'Tus clips están listos.' : returned > 0 ? `Parte del render falló. Te devolvimos ${round2(returned)} tokens.` : 'Parte del render falló.',
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
    // The beat: a scheduled post whose time arrived goes to the queue.
    if (post.status === 'SCHEDULED' && new Date(post.publish_at).getTime() <= now && !post._sim) {
      post.status = 'QUEUED';
      post._sim = { startedAt: now };
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
      // A regeneration replaces the text; the first generation only fills what is empty.
      if (!post.caption || regen > 0) post.caption = post.generated_caption;
      if (post.hashtags.length === 0 || regen > 0) post.hashtags = post.generated_hashtags;
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

function tickPurchases(db: MockDb, now: number): void {
  for (const purchase of db.purchases) {
    if (purchase.status !== 'pending' || purchase._settleAt === null || now < purchase._settleAt) continue;
    purchase.checkout_url = null;
    purchase._settleAt = null;
    if (purchase._reject) {
      purchase.status = 'rejected';
      purchase.failure_reason = 'El banco rechazó el pago. Probá con otra tarjeta.';
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
      transaction_type: 'DEPOSIT',
      status: 'COMPLETED',
      description: `Compra de ${purchase.tokens} tokens`,
      created_at: iso(now),
      kind: 'purchase',
      created_by_detail: null,
      project: null,
      category: 'purchase',
    });
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
