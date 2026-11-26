import * as contract from '../../contracts/clips';
import { newId, type MockClip, type MockDb, type MockProject } from '../db';
import { badRequest, conflict, dec, MockHttpError, notFound, route } from '../http';
import { adoptClip } from '../hybrid';
import { priceOf } from '../seed';
import { planOf, serializeClip } from '../serialize';
import { addActivity, InsufficientFunds, reserveFunds, SIM, settleReservation } from '../sim';
import { MOCK_DOWNLOAD_URL } from './projects';

function find(db: MockDb, clipId: string | undefined, workspaceId: string): { project: MockProject; clip: MockClip } {
  for (const project of db.projects) {
    if (project.workspace !== workspaceId) continue;
    const clip = project.clips.find((item) => item.id === clipId);
    if (clip) return { project, clip };
  }
  // Hybrid: a clip of the real backend gets a stand-in the first time a mocked endpoint is asked about it.
  const adopted = adoptClip(db, workspaceId, clipId);
  if (adopted) return adopted;
  throw notFound();
}

const RESOLUTION_RANK = { '720p': 1, '1080p': 2, '4K': 3 } as const;

const projectRendering = () =>
  new MockHttpError(409, { detail: 'El proyecto se está renderizando: esperá a que termine para cambiarlo.', code: 'project_rendering' });

const PREVIEW_TTL_MS = 3600_000;
const PREVIEW_MAX_ACTIVE = 3;

function previewPayload(preview: MockDb['previews'][number]) {
  const { _startedAt, ...payload } = preview;
  void _startedAt;
  return payload;
}

export const clipsHandlers = [
  route(contract.getClip, ({ db, workspace, params }) => ({ data: serializeClip(db, find(db, params.id, workspace.id).clip) })),

  route(contract.updateClip, ({ db, workspace, params, body, user, now, require }) => {
    require('clip.edit');
    const { project, clip } = find(db, params.id, workspace.id);
    const start = body.start_time ?? clip.start_time;
    const end = body.end_time ?? clip.end_time;
    // Same rule as the project's PATCH (ProjectRenderingError): what the render reads does not change under it.
    if (project.status === 'rendering' && (start !== clip.start_time || end !== clip.end_time)) throw projectRendering();
    if (end <= start) throw badRequest({ end_time: ['El fin tiene que ser mayor que el inicio.'] });
    if (project.duration_seconds && end > project.duration_seconds + 0.5) throw badRequest({ end_time: ['El recorte se pasa del final del video.'] });
    if (body.title !== undefined) clip.title = body.title;
    clip.start_time = start;
    clip.end_time = end;
    for (const post of db.posts) {
      if (post.video_clip !== clip.id) continue;
      post.clip.title = clip.title;
      post.clip.duration_seconds = Math.round((end - start) * 100) / 100;
    }
    addActivity(db, project, 'clip_edited', `Editó el clip «${clip.title}»`, now, { actorId: user.id });
    return { data: serializeClip(db, clip) };
  }),

  route(contract.deleteClip, ({ db, workspace, params, require }) => {
    require('clip.edit');
    const { project, clip } = find(db, params.id, workspace.id);
    project.clips = project.clips.filter((item) => item.id !== clip.id);
    db.posts = db.posts.filter((post) => post.video_clip !== clip.id);
    return { data: undefined };
  }),

  route(contract.renderClip, ({ db, workspace, params, user, now, require }) => {
    require('clip.edit');
    const { project, clip } = find(db, params.id, workspace.id);
    if (clip.status === 'rendering') throw conflict('El clip ya se está renderizando.');
    if (project.status === 'rendering') throw projectRendering();
    const plan = planOf(db, workspace.id);
    const resolution = project.metadata.render_resolution ?? '720p';
    if (plan && RESOLUTION_RANK[resolution] > RESOLUTION_RANK[plan.max_resolution]) {
      // PricingEngine refuses a resolution above the plan's (the project was created on a higher plan).
      throw new MockHttpError(403, { error: `Resolution ${resolution} not allowed in your plan (max ${plan.max_resolution}).`, code: 'resolution' });
    }
    const cost = priceOf(Math.ceil(clip.end_time - clip.start_time), project.metadata.render_resolution ?? '720p', false, plan?.base_discount_rate ?? 0);
    let transaction;
    try {
      transaction = reserveFunds(db, { workspace: workspace.id, amount: cost, userId: user.id, description: `Render de clip: ${clip.title}`, project: { id: project.id, title: project.title }, category: 'render' }, now);
    } catch (error) {
      if (error instanceof InsufficientFunds) throw new MockHttpError(402, { error: 'Saldo insuficiente para reservar los tokens.', required: error.required });
      throw error;
    }
    clip.status = 'rendering';
    clip._renderUntil = now + SIM.clipMs * 2;
    // The single-clip render settles when it is queued: the mock has no per-clip reservation tracking.
    settleReservation(db, transaction.id, now);
    addActivity(db, project, 'tokens_charged', `Cobro por renderizar «${clip.title}»`, now, { actorId: user.id, amount: -cost });
    return { data: { message: 'Render encolado.', final_cost: dec(cost), transaction_id: transaction.id }, status: 202 };
  }),

  route(contract.retryClip, ({ db, workspace, params, user, now, require }) => {
    require('clip.edit');
    const { project, clip } = find(db, params.id, workspace.id);
    if (clip.status !== 'failed') throw conflict('El clip no está en error.');
    clip.status = 'rendering';
    clip.error_code = null;
    clip._renderUntil = now + SIM.clipMs * 2;
    addActivity(db, project, 'clip_retried', `Reintentó el clip «${clip.title}»`, now, { actorId: user.id });
    return { data: serializeClip(db, clip), status: 202 };
  }),

  route(contract.getClipDownload, ({ db, workspace, params, now }) => {
    const { clip } = find(db, params.id, workspace.id);
    if (clip.status !== 'completed' && clip.status !== 'published') throw conflict('El clip todavía no está renderizado.');
    const filename = `${clip.title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').toLowerCase() || 'clip'}.mp4`;
    return { data: { url: MOCK_DOWNLOAD_URL, filename, expires_at: new Date(now + 3600_000).toISOString() } };
  }),

  route(contract.requestClipPreview, ({ db, workspace, params, now }) => {
    const { project, clip } = find(db, params.clipId, workspace.id);
    if (!project.source_file) throw conflict('El video fuente todavía no está disponible.');
    if (project.project_type === 'single_edit') throw conflict('El video completo se renderiza entero: no tiene vista previa.');
    const current = db.previews.find((item) => item.clip_id === clip.id && (item.status === 'pending' || item.status === 'rendering'));
    if (current) return { data: previewPayload(current), status: 200 };
    const clipIds = new Set(db.projects.filter((item) => item.workspace === workspace.id).flatMap((item) => item.clips.map((entry) => entry.id)));
    const active = db.previews.filter((item) => clipIds.has(item.clip_id) && (item.status === 'pending' || item.status === 'rendering')).length;
    if (active >= PREVIEW_MAX_ACTIVE) {
      throw new MockHttpError(429, { error: 'Ya hay vistas previas en proceso en este espacio. Probá en unos segundos.' });
    }
    const preview = {
      preview_id: newId(),
      clip_id: clip.id,
      status: 'pending' as const,
      url: null,
      expires_at: new Date(now + PREVIEW_TTL_MS).toISOString(),
      error_code: null,
      cost_tokens: 0 as const,
      simplified: { height: 360, face_tracking: false, silence_removal: false },
      _startedAt: now,
    };
    db.previews = [preview, ...db.previews.filter((item) => item.clip_id !== clip.id)];
    return { data: previewPayload(preview), status: 202 };
  }),

  route(contract.getClipPreview, ({ db, workspace, params }) => {
    const { clip } = find(db, params.clipId, workspace.id);
    const preview = db.previews.find((item) => item.clip_id === clip.id);
    if (!preview) throw new MockHttpError(404, { error: 'No hay vista previa vigente.' });
    return { data: previewPayload(preview) };
  }),
];
