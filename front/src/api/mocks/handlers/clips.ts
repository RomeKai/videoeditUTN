import * as contract from '../../contracts/clips';
import { newId, type MockClip, type MockDb, type MockProject } from '../db';
import { badRequest, conflict, dec, MockHttpError, notFound, route } from '../http';
import { adoptClip } from '../hybrid';
import { renderReservationOpen, startPaidRender } from '../render';
import { serializeClip } from '../serialize';
import { addActivity } from '../sim';
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
    // A delivered clip deleted in the middle of a render would leave its charge short.
    if (['rendering', 'uploaded', 'ingesting'].includes(project.status) || clip.status === 'rendering' || renderReservationOpen(db, project)) {
      throw conflict('El proyecto se está renderizando: esperá a que termine para borrar el clip.', 'project_rendering');
    }
    if (db.posts.some((post) => post.video_clip === clip.id && post.status === 'PROCESSING')) {
      throw conflict('Una publicación de este clip se está subiendo: esperá a que termine para borrarlo.', 'post_processing');
    }
    project.clips = project.clips.filter((item) => item.id !== clip.id);
    db.posts = db.posts.filter((post) => post.video_clip !== clip.id);
    return { data: undefined };
  }),

  route(contract.renderClip, ({ db, workspace, params, user, now, require }) => {
    require('clip.edit');
    const { project, clip } = find(db, params.id, workspace.id);
    const transaction = startPaidRender(db, project, [clip], { userId: user.id, description: `Render de clip: ${clip.title}`, now });
    addActivity(db, project, 'tokens_reserved', `Reserva por renderizar «${clip.title}»`, now, { actorId: user.id, amount: -transaction.amount });
    return { data: { message: 'Render encolado.', final_cost: dec(transaction.amount), transaction_id: transaction.id }, status: 202 };
  }),

  // Paid like `render`: the share of a clip that failed went back when its render was settled.
  route(contract.retryClip, ({ db, workspace, params, user, now, require }) => {
    require('clip.edit');
    const { project, clip } = find(db, params.id, workspace.id);
    if (clip.status !== 'failed') throw conflict('El clip no está en error.', 'not_failed');
    const transaction = startPaidRender(db, project, [clip], { userId: user.id, description: `Reintento de clip: ${clip.title}`, now });
    addActivity(db, project, 'clip_retried', `Reintentó el clip «${clip.title}»`, now, { actorId: user.id });
    addActivity(db, project, 'tokens_reserved', `Reserva por reintentar «${clip.title}»`, now, { actorId: user.id, amount: -transaction.amount });
    return { data: { ...serializeClip(db, clip), final_cost: dec(transaction.amount), transaction_id: transaction.id }, status: 202 };
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
