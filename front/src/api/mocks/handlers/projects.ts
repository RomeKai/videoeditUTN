import * as contract from '../../contracts/projects';
import type { RenderResolution } from '../../contracts/common';
import { newId, type MockDb, type MockProject, type ProjectSim } from '../db';
import { badRequest, conflict, dec, MockHttpError, notFound, paginate, route } from '../http';
import { adoptProject } from '../hybrid';
import { priceOf, PRICING, stageStatusFor } from '../seed';
import { planOf, serializeClip, serializeProject } from '../serialize';
import { startPaidRender } from '../render';
import { addActivity, InsufficientFunds, reserveAutoRender, reserveFunds, serveActivity, startRender } from '../sim';
import { linkProvider } from '@/features/proyectos/project-view';
import { projectStatusGroup } from '@/lib/project-status';

const RANK: Record<RenderResolution, number> = { '720p': 1, '1080p': 2, '4K': 3 };
/** The stages a failed project can resume from, in order (services/ai/pipeline_state.py · STAGE_ORDER). */
const STAGE_ORDER: string[] = ['uploaded', 'audio_extracted', 'transcribed', 'moderated', 'clips_selected', 'render_dispatched'];
/** A tiny data URL stands in for the signed download links (see lib/download.ts). */
export const MOCK_DOWNLOAD_URL = 'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDE=';
export const MOCK_ZIP_URL = 'data:application/zip;base64,UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==';

const UPLOAD_MESSAGES = {
  not_a_video: 'El archivo no es un video válido. Formatos admitidos: MP4, MOV y AVI.',
  unreadable: 'No pudimos leer el video. Probá exportarlo de nuevo en MP4 (H.264).',
  no_video_stream: 'El archivo no tiene una pista de video.',
  no_duration: 'No pudimos determinar la duración del video.',
  too_long: 'El video supera la duración máxima que podemos procesar (3 h).',
  too_large: 'La resolución del video supera el máximo admitido (4096 px de lado).',
  too_small: 'La resolución del video es demasiado baja (mínimo 240 px de lado).',
} as const;

/**
 * Mock triggers, by file name (or link), to reach every error of map §4 without real media:
 *   no-video · ilegible · sin-pista · sin-duracion · muy-largo · muy-grande · muy-chico → 400 on the file
 * and by file name or title, for what happens later in the pipeline:
 *   moderacion · sin-voz · sin-clips · timeout · lento · falla → the project fails with that error code
 *   demanda → waits for capacity ("En espera") before going on
 *   parcial → one clip fails in the render ("Listo con errores")
 * The duration comes from "NNmin" in the name (e.g. "clase-45min.mp4"), else from the file size.
 */
const UPLOAD_TRIGGERS: Array<[RegExp, keyof typeof UPLOAD_MESSAGES]> = [
  [/no-video/i, 'not_a_video'],
  [/ilegible/i, 'unreadable'],
  [/sin-pista/i, 'no_video_stream'],
  [/sin-duracion/i, 'no_duration'],
  [/muy-largo/i, 'too_long'],
  [/muy-grande/i, 'too_large'],
  [/muy-chico/i, 'too_small'],
];

function simFor(text: string, now: number): ProjectSim {
  const sim: ProjectSim = { phase: 'ingest', startedAt: now };
  if (/moderaci[oó]n/i.test(text)) sim.failAt = { stage: 'moderated', code: 'UnsafeContentError' };
  else if (/sin-voz/i.test(text)) sim.failAt = { stage: 'transcribed', code: 'AINoSpeechDetectedError' };
  else if (/sin-clips/i.test(text)) sim.failAt = { stage: 'clips_selected', code: 'AIEmptySelectionError' };
  else if (/timeout/i.test(text)) sim.failAt = { stage: 'transcribed', code: 'CapacityTimeoutError' };
  else if (/lento/i.test(text)) sim.failAt = { stage: 'audio_extracted', code: 'SoftTimeLimitExceeded' };
  else if (/falla/i.test(text)) sim.failAt = { stage: 'moderated', code: 'ModerationUnavailableError' };
  if (/demanda/i.test(text)) sim.waitMs = 15_000;
  if (/parcial/i.test(text)) sim.failOneClip = true;
  return sim;
}

function durationFor(name: string, size: number): number {
  const minutes = /(\d+)\s*min/i.exec(name);
  if (minutes) return Number(minutes[1]) * 60;
  // Roughly 1 MB every 8 seconds, with a floor so tiny test files are still a few minutes long.
  return Math.max(180, Math.round((size / (1024 * 1024)) * 8));
}

const truthy = (value: FormDataEntryValue | null) => typeof value === 'string' && ['true', '1', 't', 'y', 'yes', 'on'].includes(value.trim().toLowerCase());

function own(db: MockDb, id: string | undefined, workspaceId: string): MockProject {
  // Hybrid: a project of the real backend gets a stand-in the first time a mocked endpoint is asked about it.
  const project = db.projects.find((item) => item.id === id && item.workspace === workspaceId) ?? adoptProject(db, workspaceId, id);
  if (!project) throw notFound();
  return project;
}

function payment(error: unknown): never {
  if (error instanceof InsufficientFunds) {
    // A Decimal put straight in a Response: DRF's encoder sends it as a number.
    throw new MockHttpError(402, { error: 'Saldo insuficiente para reservar los tokens.', required: error.required });
  }
  throw error;
}

function createProject(context: Parameters<Parameters<typeof route<typeof contract.createProject>>[1]>[0], prompt: string | null) {
  const { db, form, user, workspace, now, require } = context;
  require('project.create');
  if (!form) throw badRequest({ non_field_errors: ["Debes proporcionar un 'source_file' o una 'video_url'."] });
  const file = form.get('source_file');
  const upload = typeof file === 'object' && file !== null ? (file as File) : null;
  const url = typeof form.get('video_url') === 'string' ? String(form.get('video_url')).trim() : '';
  if (!upload && !url) throw badRequest({ non_field_errors: ["Debes proporcionar un 'source_file' o una 'video_url'."] });
  if (upload && url) throw badRequest({ non_field_errors: ['No puedes enviar archivo y URL al mismo tiempo.'] });

  const get = (key: string) => {
    const value = form.get(key);
    return typeof value === 'string' && value !== '' ? value : null;
  };
  const projectType = get('project_type') === 'single_edit' ? 'single_edit' : 'repurpose';
  const resolution = (get('render_resolution') ?? '720p') as RenderResolution;
  const useSeo = truthy(form.get('use_seo'));
  const plan = planOf(db, workspace.id);
  let duration = 0;

  if (upload) {
    const name = upload.name || 'video.mp4';
    if (!/\.(mp4|mov|avi)$/i.test(name)) throw badRequest({ source_file: ['Solo se permiten archivos de video (.mp4, .mov, .avi)'] });
    if (upload.size > PRICING.upload_max_size_bytes) throw badRequest({ source_file: ['El archivo es demasiado grande (Máx 500MB)'] });
    const trigger = UPLOAD_TRIGGERS.find(([pattern]) => pattern.test(name));
    // Same shape as the backend today: only the message of InvalidUploadError, no code.
    if (trigger) throw badRequest({ source_file: [UPLOAD_MESSAGES[trigger[1]]] });
    duration = durationFor(name, upload.size);
    if (projectType === 'single_edit' && duration > PRICING.single_edit_max_duration_seconds) {
      throw badRequest({ project_type: ['Para editar el video completo, el video puede durar hasta 10 min.'] });
    }
  } else {
    if (projectType === 'single_edit') {
      throw badRequest({ project_type: ['Para editar el video completo, subí el archivo. Los links sirven para sacar clips.'] });
    }
    if (resolution !== '720p') {
      throw badRequest({ error: 'Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo.' });
    }
    duration = /(\d+)\s*min/i.test(url) ? durationFor(url, 0) : 2700;
  }

  if (plan) {
    // Messages as PricingEngine raises them today (English, no code).
    if (upload && duration > plan.max_video_duration_seconds) {
      throw new MockHttpError(403, { error: `Video duration (${duration}s) exceeds your plan limit (${plan.max_video_duration_seconds}s).` });
    }
    if (RANK[resolution] > RANK[plan.max_resolution]) {
      throw new MockHttpError(403, { error: `Your plan '${plan.name}' does not support ${resolution} rendering.` });
    }
    if (useSeo && !plan.has_seo_optimization) {
      throw new MockHttpError(403, { error: 'SEO Optimization is not allowed in your current plan.' });
    }
  }
  const rate = plan?.base_discount_rate ?? 0;
  const cost = upload ? priceOf(duration, resolution, useSeo, rate) : PRICING.link_flat_cost + (useSeo ? priceOf(0, '720p', true, rate) : 0);

  // Like DRF: the serializer validates every field before the view reserves anything, so a 400 never leaves a reservation.
  const criteriaText = form.get('selection_criteria');
  if (typeof criteriaText === 'string' && criteriaText.length > PRICING.selection_criteria_max_chars) {
    // DRF's max_length message.
    throw badRequest({ selection_criteria: [`Asegúrese de que este campo no tenga más de ${PRICING.selection_criteria_max_chars} caracteres.`] });
  }

  const id = newId();
  const title = get('title') ?? 'Nuevo Proyecto';
  let transaction;
  try {
    transaction = reserveFunds(db, { workspace: workspace.id, amount: cost, userId: user.id, description: `Procesamiento: ${title}`, project: { id, title }, category: 'project' }, now);
  } catch (error) {
    payment(error);
  }

  const speakerTracking = truthy(form.get('speaker_tracking'));
  const layout = (get('render_layout') ?? get('layout') ?? (speakerTracking ? 'active' : 'fill')) as MockProject['render_layout'];
  const brandKitId = get('brand_kit');
  const brandKit = db.brandKits.find((kit) => kit.id === brandKitId && kit.workspace === workspace.id) ?? null;
  const number = (key: string, fallback: number) => (get(key) === null ? fallback : Number(get(key)));
  const bool = (key: string, fallback: boolean) => (form.get(key) === null ? fallback : truthy(form.get(key)));
  const maxClips = get('max_clips');
  const criteria = get('selection_criteria');

  const project: MockProject = {
    id,
    workspace: workspace.id,
    uploaded_by: user.id,
    title,
    original_r2_key: null,
    proxy_r2_key: null,
    final_export_r2_key: null,
    project_type: projectType,
    editing_style: (get('editing_style') ?? 'dynamic') as MockProject['editing_style'],
    intelligence_level: (get('intelligence_level') ?? 'fast') as MockProject['intelligence_level'],
    auto_render_bypass: bool('auto_render_bypass', false),
    brand_kit: brandKit?.id ?? null,
    ...(brandKit ? { brand_kit_name: brandKit.name } : {}),
    aspect_ratio: (get('aspect_ratio') ?? '9:16') as MockProject['aspect_ratio'],
    render_layout: layout,
    gameplay_position: (get('gameplay_position') ?? 'center') as MockProject['gameplay_position'],
    camera_selection_position: (get('camera_selection_position') ?? 'center') as MockProject['camera_selection_position'],
    manual_camera_x: number('manual_camera_x', 0.5),
    manual_camera_y: number('manual_camera_y', 0.2),
    manual_camera_zoom: number('manual_camera_zoom', 1),
    pip_corner: (get('pip_corner') ?? 'bottom-right') as MockProject['pip_corner'],
    trending_color: get('trending_color') ?? '#FF2D55',
    trending_text: get('trending_text') ?? '',
    speaker_tracking: speakerTracking,
    // Not sent means true on create (the serializer's `_NotSentBooleanField`): only a value that travels turns subtitles off.
    add_subtitles: bool('add_subtitles', true),
    subtitle_color: get('subtitle_color') ?? '#FFFF00',
    subtitle_with_emojis: false,
    subtitle_words_per_segment: number('subtitle_words_per_segment', 3),
    subtitle_size: (get('subtitle_size') ?? 'medium') as MockProject['subtitle_size'],
    subtitle_position: (get('subtitle_position') ?? 'bottom') as MockProject['subtitle_position'],
    use_facetracking: bool('use_facetracking', false),
    remove_silences: bool('remove_silences', false),
    source_file: upload ? `/media/videos/raw/${new Date(now).toISOString().slice(0, 7).replace('-', '/')}/${upload.name}` : null,
    video_url: upload ? null : url,
    metadata: {
      ...(maxClips ? { max_clips: Number(maxClips) } : {}),
      ...(criteria ? { selection_criteria: criteria } : {}),
      render_resolution: resolution,
      use_seo: useSeo,
      ...(upload ? { source_media: { duration_s: duration, width: 1920, height: 1080, mime: upload.type || 'video/mp4' }, priced_duration_s: duration } : { duration }),
      ...(prompt !== null ? { pte_prompt: prompt, pte_explicit: [...form.keys()].filter((key) => !['prompt', 'source_file', 'video_url', 'title'].includes(key)) } : {}),
      reservation_id: transaction.id,
    },
    ai_rationale_log: {},
    approved_segments: null,
    status: 'uploaded',
    pipeline_stage: 'uploaded',
    pipeline_stage_status: stageStatusFor('uploaded', null),
    pipeline_attempts: 0,
    pipeline_error_code: '',
    pipeline_error_at: null,
    created_at: new Date(now).toISOString(),
    clips: [],
    proxy_url: null,
    uploaded_by_detail: { id: user.id, name: user.name },
    thumbnail_url: null,
    duration_seconds: duration,
    cost: { amount: cost, state: 'reserved', transaction_id: transaction.id },
    stage_timings: {},
    eta_seconds: 30,
    _transcriptSeed: null,
    _reservations: [transaction.id],
    _sim: simFor(`${title} ${upload?.name ?? url}`, now),
  };
  db.projects.unshift(project);
  addActivity(db, project, 'project_created', 'Creó el proyecto', now, { actorId: user.id });
  addActivity(db, project, 'tokens_reserved', 'Reserva por el procesamiento', now, { actorId: user.id, amount: -cost, transactionId: transaction.id });
  return {
    ...serializeProject(db, project, { full: true }),
    cost_details: { tokens_reserved: cost, transaction_id: transaction.id },
    message: 'Video subido. Iniciando ingesta multimodal...',
  };
}

export const projectsHandlers = [
  route(contract.probeLink, ({ db, workspace, body }) => {
    const url = body.video_url;
    try {
      new URL(url);
    } catch {
      throw badRequest({ video_url: ['Ingresá una URL válida.'] });
    }
    // The backend's exact host list: youtube.com.evil.com is not YouTube.
    const provider = linkProvider(url);
    if (!provider) throw new MockHttpError(400, { error: 'No reconocemos este sitio. Pegá un link de YouTube, Vimeo, Twitch o Google Drive.', code: 'unsupported' });
    if (/privado|borrado/i.test(url)) throw new MockHttpError(400, { error: 'No pudimos acceder a este video. Puede ser privado o haberse borrado.', code: 'unavailable' });
    const duration = /(\d+)\s*min/i.test(url) ? durationFor(url, 0) : /largo/i.test(url) ? 5 * 3600 : 2700;
    const plan = planOf(db, workspace.id);
    // tasks/shared.py · link_too_long_error: the video's length decides. Over the app's limit no plan takes it,
    // whatever the plan (Gratis too): a 400 of its own. Within it, over the plan's: a 403 (another plan takes it).
    const appLimit = PRICING.upload_max_duration_seconds;
    if (duration > appLimit) {
      throw new MockHttpError(400, { error: 'El video dura más de 3 horas, el máximo que aceptamos. Probá con uno más corto.', code: 'duration_app', limit: appLimit });
    }
    if (plan && duration > plan.max_video_duration_seconds) {
      throw new MockHttpError(403, { error: `Video duration (${duration}s) exceeds your plan limit (${plan.max_video_duration_seconds}s).`, code: 'duration', limit: plan.max_video_duration_seconds });
    }
    return { data: { video_url: url, provider, title: `Video de ${provider}`, duration_seconds: duration, thumbnail_url: null } };
  }),

  route(contract.listProjects, ({ db, workspace, query }) => {
    // As ProjectsQuerySerializer: a value that is not one of the choices is a 400, not an empty list.
    const search = (query.get('search') ?? '').trim().toLowerCase();
    const group = query.get('status_group') ?? 'all';
    const ordering = query.get('ordering') ?? '-created_at';
    if (!contract.ProjectStatusGroup.safeParse(group).success) throw badRequest({ status_group: [`"${group}" is not a valid choice.`] });
    if (!['-created_at', 'created_at', 'title'].includes(ordering)) throw badRequest({ ordering: [`"${ordering}" is not a valid choice.`] });
    const mine = db.projects.filter((project) => project.workspace === workspace.id).filter((project) => !search || project.title.toLowerCase().includes(search));
    const counts = { all: mine.length, in_progress: 0, to_review: 0, ready: 0, failed: 0 };
    for (const project of mine) counts[projectStatusGroup(project)] += 1;
    const filtered = mine.filter((project) => group === 'all' || projectStatusGroup(project) === group);
    filtered.sort((a, b) =>
      ordering === 'title' ? a.title.toLowerCase().localeCompare(b.title.toLowerCase(), 'es') || b.created_at.localeCompare(a.created_at) : ordering === 'created_at' ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at),
    );
    const page = paginate(filtered, query, contract.listProjects.path);
    return { data: { ...page, results: page.results.map((project) => serializeProject(db, project, { full: false })), status_counts: counts } };
  }),

  route(contract.createProject, (context) => ({ data: createProject(context, null) })),

  route(contract.createProjectFromPrompt, (context) => {
    const raw = context.form?.get('prompt');
    const prompt = typeof raw === 'string' ? raw.trim() : '';
    if (!prompt) throw badRequest({ prompt: ['Este campo es obligatorio.'] });
    if (prompt.length > PRICING.prompt_max_chars) throw badRequest({ prompt: [`Máximo ${PRICING.prompt_max_chars} caracteres.`] });
    const data = createProject(context as never, prompt);
    return { data: { ...data, prompt_config: { status: 'pending', detail: 'Se aplica al iniciar la ingesta; el resultado queda en metadata.pte.' } } };
  }),

  route(contract.getProject, ({ db, workspace, params }) => ({ data: serializeProject(db, own(db, params.id, workspace.id), { full: true }) })),

  route(contract.updateProject, ({ db, workspace, params, body, user, now, require }) => {
    require('project.rename');
    const project = own(db, params.id, workspace.id);
    if (body.title !== undefined && body.title !== project.title) {
      project.title = body.title;
      for (const transaction of db.transactions) if (transaction.project?.id === project.id) transaction.project.title = body.title;
      for (const post of db.posts) if (post.clip.project_id === project.id) post.clip.project_title = body.title;
      addActivity(db, project, 'project_renamed', `Renombró el proyecto a «${body.title}»`, now, { actorId: user.id });
    }
    return { data: serializeProject(db, project, { full: true }) };
  }),

  route(contract.deleteProject, ({ db, workspace, params, require }) => {
    const project = own(db, params.id, workspace.id);
    require('project.delete', { ownerId: project.uploaded_by });
    // `project_delete_decision`: nothing settles a reservation once its project is gone.
    if (['rendering', 'uploaded', 'ingesting'].includes(project.status)) {
      throw conflict('El proyecto se está procesando: esperá a que termine para borrarlo.', 'project_processing');
    }
    if (project._reservations.some((id) => db.transactions.find((item) => item.id === id)?.status === 'RESERVED')) {
      throw conflict('El proyecto tiene tokens reservados que todavía no se liquidaron. Probá de nuevo en unos minutos.', 'reservation_open');
    }
    db.projects = db.projects.filter((item) => item.id !== project.id);
    // Scheduled publications of its clips go with it; nothing already charged comes back.
    db.posts = db.posts.filter((post) => post.clip.project_id !== project.id);
    db.activity = db.activity.filter((item) => item.project !== project.id);
    return { data: undefined };
  }),

  route(contract.getPaperEditData, ({ db, workspace, params }) => {
    const project = own(db, params.id, workspace.id);
    const data = serializeProject(db, project, { full: true });
    return {
      data: {
        project_id: project.id,
        title: project.title,
        status: project.status,
        proxy_url: null,
        transcript: data.transcript_data,
        ai_suggestions: project.clips.map((clip) => serializeClip(db, clip)),
        metadata: project.metadata,
      },
    };
  }),

  route(contract.approvePaperEdit, ({ db, workspace, params, body, user, now, require }) => {
    require('project.review');
    const project = own(db, params.id, workspace.id);
    // As the backend today: any status other than awaiting_approval is a 400 (409 is only the race of two approvals).
    if (project.status !== 'awaiting_approval') {
      throw badRequest({ error: `El proyecto no está en espera de aprobación. Estado actual: ${project.status}` });
    }
    // PR #124: a segment the render could not finish within its time limit is rejected (1800 s by default).
    if (body.approved_segments.some((segment) => segment.end - segment.start > 1800)) {
      throw badRequest({ error: 'Los segmentos aprobados son inválidos: cada segmento puede durar hasta 1800 segundos. Dividí los más largos en varios.' });
    }
    const total = body.approved_segments.reduce((sum, segment) => sum + (segment.end - segment.start), 0);
    const plan = planOf(db, workspace.id);
    const seconds = Math.ceil(Math.round(total * 1000) / 1000);
    // PricingEngine's plan check surfaces as a 400 of the view, not as a 403.
    if (plan && seconds > plan.max_video_duration_seconds) {
      throw badRequest({ error: `Error en el motor de precios: ['Video duration (${seconds}s) exceeds your plan limit (${plan.max_video_duration_seconds}s).']` });
    }
    const resolution = project.metadata.render_resolution ?? '720p';
    if (plan && RANK[resolution] > RANK[plan.max_resolution]) {
      throw badRequest({ error: `Error en el motor de precios: ["Your plan '${plan.name}' does not support ${resolution} rendering."]` });
    }
    const cost = priceOf(seconds, resolution, false, plan?.base_discount_rate ?? 0);
    let transaction;
    try {
      transaction = reserveFunds(db, { workspace: workspace.id, amount: cost, userId: user.id, description: `Render de los clips aprobados: ${project.title}`, project: { id: project.id, title: project.title }, category: 'render' }, now);
    } catch (error) {
      payment(error);
    }
    project.clips = body.approved_segments.map((segment, index) => {
      const existing = project.clips.find((clip) => clip.id === segment.clip_id) ?? project.clips.find((clip) => clip.start_time === segment.start && clip.end_time === segment.end);
      return {
        ...(existing ?? {
          id: newId(),
          project: project.id,
          s3_object_key: null,
          virality_score: 0,
          ai_reasoning: '',
          created_at: new Date(now).toISOString(),
          layers: [],
          preview_url: null,
          thumbnail_url: null,
          posts: [],
        }),
        title: segment.title ?? existing?.title ?? `Clip ${index + 1}`,
        start_time: segment.start,
        end_time: segment.end,
        status: 'rendering' as const,
        error_code: null,
      };
    });
    project.approved_segments = body.approved_segments.map((segment) => ({ start: segment.start, end: segment.end, ...(segment.text ? { text: segment.text } : {}) }));
    project.metadata = { ...project.metadata, final_duration: total, render_reservation_id: transaction.id };
    delete project.metadata.auto_render_skipped;
    project.status = 'rendering';
    project.pipeline_stage = 'render_dispatched';
    project._reservations.push(transaction.id);
    project._sim = { phase: 'render', startedAt: now, failOneClip: /parcial/i.test(project.title) };
    addActivity(db, project, 'review_approved', `Aprobó ${project.clips.length} clips para renderizar`, now, { actorId: user.id });
    addActivity(db, project, 'tokens_reserved', 'Reserva por el render', now, { actorId: user.id, amount: -cost, transactionId: transaction.id });
    return {
      data: { message: 'Edición aprobada. Iniciando renderizado de alta calidad...', project_id: project.id, final_cost: cost, transaction_id: transaction.id },
    };
  }),

  // ProjectRedispatchView: what a failed project paid went back when it failed, so resuming it reserves that again.
  route(contract.redispatchProject, ({ db, workspace, params, user, now, require }) => {
    require('members.manage');
    const project = own(db, params.id, workspace.id);
    if (project.status !== 'failed') throw conflict('El proyecto ya está en curso o terminó.', 'not_failed');
    if (project.pipeline_error_code === 'UnsafeContentError') {
      throw conflict('El contenido no pasó la moderación: no se puede volver a procesar. Si creés que es un error, escribinos.', 'moderation_blocked');
    }
    if (project.pipeline_error_code === 'IngestionNotQueued') {
      throw conflict('Este proyecto no llegó a procesarse y se devolvió lo reservado: creá uno nuevo.', 'not_queued');
    }
    const whole = project.project_type === 'single_edit';
    const beforeClips = STAGE_ORDER.indexOf(project.pipeline_stage) < STAGE_ORDER.indexOf('clips_selected');
    // The row is answered as it is, still `failed` with its code: the task reopens it when it runs (the next ticks).
    const answer = (reservation?: { id: string; amount: number }) => {
      const data = serializeProject(db, project, { full: true });
      return { data: reservation ? { ...data, cost_details: { tokens_reserved: dec(reservation.amount), transaction_id: reservation.id } } : data, status: 202 as const };
    };
    if (!whole && !beforeClips) {
      // The ingestion was paid and the render pays itself: the auto-render reserves again when it resumes.
      if (project.pipeline_error_code !== 'AutoRenderNotPaid') {
        throw conflict('El render de estos clips ya se liquidó: reintentá cada clip desde Clips.', 'render_settled');
      }
      const response = answer();
      // Without balance the pipeline fails it again with the same code: it stays as it was.
      if (reserveAutoRender(db, project, now) === null) {
        project.pipeline_error_code = '';
        project.pipeline_error_at = null;
        project.clips = project.clips.map((clip) => (clip.status === 'draft' ? { ...clip, status: 'rendering' as const } : clip));
        startRender(project, now);
      }
      return response;
    }
    if (whole && project.clips.some((clip) => ['completed', 'published', 'failed'].includes(clip.status))) {
      throw conflict('Este video ya se renderizó: para hacerlo de nuevo, usá «Volver a renderizar».', 'use_rerender');
    }
    const creation = db.transactions.find((item) => item.id === project.metadata.reservation_id);
    if (!creation) throw conflict('Este proyecto no se puede volver a procesar: creá uno nuevo.', 'no_reservation');
    let reservation;
    // Returned when it failed: the same amount is reserved again. Still reserved or already charged: it resumes free.
    if (creation.status === 'FAILED') {
      try {
        reservation = reserveFunds(db, { workspace: workspace.id, amount: creation.amount, userId: user.id, description: `Procesamiento: ${project.title}`, project: { id: project.id, title: project.title }, category: creation.category }, now);
      } catch (error) {
        payment(error);
      }
      project.metadata = { ...project.metadata, reservation_id: reservation.id };
      project._reservations.push(reservation.id);
      if (project.cost) project.cost = { amount: reservation.amount, state: 'reserved', transaction_id: reservation.id };
      addActivity(db, project, 'tokens_reserved', 'Reserva por volver a procesar', now, { actorId: user.id, amount: -reservation.amount, transactionId: reservation.id });
    }
    project._sim = { phase: 'ingest', startedAt: now };
    return answer(reservation);
  }),

  route(contract.rerenderProject, ({ db, workspace, params, body, user, now, require }) => {
    require('project.review');
    const project = own(db, params.id, workspace.id);
    // A whole video is its one clip; a clips project, the clips it delivered or failed (never a suggested draft; a draft a render paid for is one of them).
    const clips = project.project_type === 'single_edit' ? project.clips : project.clips.filter((clip) => clip.status !== 'draft' || clip.render_queued);
    if (clips.length === 0) throw conflict('El proyecto no tiene clips para renderizar.', 'no_clips');
    const transaction = startPaidRender(db, project, clips, { userId: user.id, description: `Render con otro estilo: ${project.title}`, now });
    const { brand_kit: brandKitId, ...style } = body;
    Object.assign(project, style);
    if (brandKitId !== undefined) {
      const kit = db.brandKits.find((item) => item.id === brandKitId) ?? null;
      project.brand_kit = kit?.id ?? null;
      if (kit) project.brand_kit_name = kit.name;
      else delete project.brand_kit_name;
    }
    addActivity(db, project, 'tokens_reserved', 'Reserva por renderizar todo de nuevo', now, { actorId: user.id, amount: -transaction.amount, transactionId: transaction.id });
    return { data: { message: 'Render encolado.', final_cost: dec(transaction.amount), transaction_id: transaction.id }, status: 202 };
  }),

  route(contract.getProjectActivity, ({ db, workspace, params }) => {
    const project = own(db, params.id, workspace.id);
    const items = serveActivity(db, project.id);
    return { data: items };
  }),

  route(contract.downloadAllClips, ({ db, workspace, params, body, now }) => {
    const project = own(db, params.id, workspace.id);
    const ready = project.clips.filter((clip) => (clip.status === 'completed' || clip.status === 'published') && (!body.clip_ids || body.clip_ids.includes(clip.id)));
    if (ready.length === 0) throw conflict('Ningún clip está listo todavía.');
    return { data: { url: MOCK_ZIP_URL, expires_at: new Date(now + 3600_000).toISOString() } };
  }),
];
