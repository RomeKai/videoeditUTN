import type { MockClip, MockDb, MockProject, MockTransaction } from './db';
import { conflict, MockHttpError } from './http';
import { priceOf } from './seed';
import { planOf } from './serialize';
import { InsufficientFunds, reserveFunds } from './sim';

/**
 * The paid renders of back/apps/videos/views_clips.py (`_start_paid_render`): a clip rendered again
 * (D4), a failed clip retried (D3) and every clip with another style (D5). They share the rules:
 * only a project that ended, one at a time, reserved before anything changes and charged by the
 * seconds delivered when the last clip ends (`finishPaidRender` in sim.ts).
 */

const RESOLUTION_RANK = { '720p': 1, '1080p': 2, '4K': 3 } as const;
/** `max_render_segment_seconds()`: what a render finishes within its time limit. */
const MAX_CLIP_SECONDS = 1800;
const ENDED = ['completed', 'partial', 'failed'];
const PENDING_POSTS = ['SCHEDULED', 'QUEUED', 'PROCESSING'];

export const INSUFFICIENT_FUNDS = 'Saldo insuficiente para reservar los tokens.';

/** A render reservation of the project is still RESERVED (`_render_reservation_open`). */
export function renderReservationOpen(db: MockDb, project: MockProject): boolean {
  return project._reservations.some((id) => {
    const transaction = db.transactions.find((item) => item.id === id);
    return transaction?.status === 'RESERVED' && (transaction.category === 'render' || project.project_type === 'single_edit');
  });
}

/** `_render_refusal` and `_posts_refusal`, in the backend's order: throws the 409 `{ error, code }`. */
function refuse(db: MockDb, project: MockProject, clips: MockClip[]): void {
  // `_clip_in_render`: rendering, or waiting in `draft` for a render that pays for it.
  if (clips.some((clip) => clip.status === 'rendering' || (clip.status === 'draft' && clip.render_queued))) throw conflict('El clip ya se está renderizando.', 'clip_rendering');
  if (project.status === 'awaiting_approval') throw conflict('Los clips de un proyecto en revisión se renderizan al aprobarla.', 'awaiting_approval');
  if (project.status === 'rendering') throw conflict('El proyecto se está renderizando: esperá a que termine para cambiarlo.', 'project_rendering');
  if (!ENDED.includes(project.status)) throw conflict('El proyecto se está procesando: esperá a que termine.', 'project_processing');
  if (renderReservationOpen(db, project)) {
    throw conflict('El render anterior de este proyecto todavía se está liquidando. Probá de nuevo en unos minutos.', 'render_unsettled');
  }
  const withFile = new Set(clips.filter((clip) => clip.s3_object_key || clip.status === 'completed' || clip.status === 'published').map((clip) => clip.id));
  if (db.posts.some((post) => withFile.has(post.video_clip) && PENDING_POSTS.includes(post.status))) {
    throw conflict('Este clip tiene publicaciones programadas o en curso: cancelalas o esperá a que se publiquen antes de renderizarlo de nuevo.', 'posts_pending');
  }
}

/**
 * Reserves the render of `clips` and starts it. Throws what the backend answers when it cannot:
 * 409 `{ error, code }`, 400 for a clip too long, 403 `{ error, code }` over the plan and
 * 402 `{ error, required }` without balance (nothing was written).
 */
export function startPaidRender(
  db: MockDb,
  project: MockProject,
  clips: MockClip[],
  input: { userId: string; description: string; now: number; wholeProject?: boolean },
): MockTransaction {
  refuse(db, project, clips);
  if (clips.some((clip) => clip.end_time - clip.start_time > MAX_CLIP_SECONDS)) {
    throw new MockHttpError(400, { error: `Cada clip puede durar hasta ${MAX_CLIP_SECONDS} segundos para renderizarse. Acortalo en el editor.` });
  }
  const seconds = Math.round(clips.reduce((sum, clip) => sum + Math.max(0, clip.end_time - clip.start_time), 0) * 1000) / 1000;
  const billed = Math.ceil(seconds);
  const plan = planOf(db, project.workspace);
  const resolution = project.metadata.render_resolution ?? '720p';
  if (plan && RESOLUTION_RANK[resolution] > RESOLUTION_RANK[plan.max_resolution]) {
    throw new MockHttpError(403, { error: `Tu plan ${plan.name} renderiza hasta ${plan.max_resolution} y este proyecto es ${resolution}.`, code: 'resolution' });
  }
  if (plan && billed > plan.max_video_duration_seconds) {
    throw new MockHttpError(403, {
      error: `Tu plan permite renderizar hasta ${plan.max_video_duration_seconds} segundos por vez. Acortá los clips o cambiá de plan.`,
      code: 'duration',
      limit: plan.max_video_duration_seconds,
    });
  }
  const cost = priceOf(billed, resolution, false, plan?.base_discount_rate ?? 0);
  let transaction: MockTransaction;
  try {
    transaction = reserveFunds(
      db,
      { workspace: project.workspace, amount: cost, userId: input.userId, description: input.description.slice(0, 255), project: { id: project.id, title: project.title }, category: 'render' },
      input.now,
    );
  } catch (error) {
    if (error instanceof InsufficientFunds) throw new MockHttpError(402, { error: INSUFFICIENT_FUNDS, required: error.required });
    throw error;
  }
  const previous: NonNullable<MockProject['_paidRender']>['previous'] = {};
  for (const clip of clips) {
    if ((clip.status === 'completed' || clip.status === 'published') && clip.s3_object_key) previous[clip.id] = { status: clip.status, key: clip.s3_object_key };
    // As the backend: back to `draft`, queued, until a worker takes it (sim.ts · tickRender).
    clip.status = 'draft';
    clip.render_queued = true;
    clip.error_code = null;
  }
  project.status = 'rendering';
  project.pipeline_stage = 'render_dispatched';
  project.pipeline_error_code = '';
  project.pipeline_error_at = null;
  project._reservations.push(transaction.id);
  const scope = input.wholeProject || project.project_type === 'single_edit' ? 'project' : 'clip';
  project._paidRender = { reservation: transaction.id, clipIds: clips.map((clip) => clip.id), scope, previous };
  project._sim = { phase: 'render', startedAt: input.now };
  // Only a render of the project starts «Renderizar» again (`_metadata_for_render_dispatch`); one clip's
  // leaves its times, unless the step has no closed render to keep.
  const closed = project.stage_timings?.render_dispatched?.duration_s != null;
  if (scope === 'project' || !closed) {
    project.stage_timings = { ...project.stage_timings, render_dispatched: { started_at: new Date(input.now).toISOString(), finished_at: null, duration_s: null } };
  }
  return transaction;
}
