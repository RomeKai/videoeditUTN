import { z } from 'zod';
import { SLOW_REQUEST_TIMEOUT_MS } from '../config';
import { defineEndpoint } from '../endpoint';
import { ColorValue, Decimal, HexColor, Id, IsoDateTime, NoContent, PageQuery, Platform, RenderResolution, UserRef, paginated } from './common';

/* ── Enums of VideoProject (back/apps/videos/models.py) ─────────────────── */

export const ProjectType = z.enum(['repurpose', 'single_edit']);
export type ProjectType = z.infer<typeof ProjectType>;

export const EditingStyle = z.enum(['dynamic', 'minimalist', 'vlog', 'hormozi', 'custom']);
export type EditingStyle = z.infer<typeof EditingStyle>;

export const ProjectStatus = z.enum([
  'uploaded',
  'ingesting',
  'awaiting_approval',
  'rendering',
  'completed',
  'partial',
  'failed',
]);
export type ProjectStatus = z.infer<typeof ProjectStatus>;

/** Last stage the pipeline finished. */
export const PipelineStage = z.enum([
  'uploaded',
  'audio_extracted',
  'transcribed',
  'moderated',
  'clips_selected',
  'render_dispatched',
  'completed',
  'partial',
  'failed',
]);
export type PipelineStage = z.infer<typeof PipelineStage>;

/** services/ai/pipeline_state.py · StageStatus */
export const StageStatus = z.enum(['pending', 'running', 'completed', 'failed', 'pending_capacity']);
export type StageStatus = z.infer<typeof StageStatus>;

/** `{ "<stage>": "<status>" }` for the stages that do work. */
export const PipelineStageStatus = z.record(z.string(), StageStatus);
export type PipelineStageStatus = z.infer<typeof PipelineStageStatus>;

export const IntelligenceLevel = z.enum(['fast', 'smart']);
export type IntelligenceLevel = z.infer<typeof IntelligenceLevel>;

export const AspectRatio = z.enum(['9:16', '1:1', '16:9']);
export type AspectRatio = z.infer<typeof AspectRatio>;

export const Layout = z.enum(['fill', 'fit', 'blurred', 'split', 'pip', 'versus', 'active', 'reaction', 'cinematic', 'podcast', 'trending']);
export type Layout = z.infer<typeof Layout>;

export const Position = z.enum(['left', 'center', 'right']);
export type Position = z.infer<typeof Position>;

/** Corner of the camera inset of the PiP layout. */
export const PipCorner = z.enum(['bottom-right', 'bottom-left', 'top-right', 'top-left']);
export type PipCorner = z.infer<typeof PipCorner>;

export const SubtitleSize = z.enum(['small', 'medium', 'large']);
export type SubtitleSize = z.infer<typeof SubtitleSize>;

export const SubtitlePosition = z.enum(['top', 'center', 'bottom']);
export type SubtitlePosition = z.infer<typeof SubtitlePosition>;

/** `pipeline_error_code` values the UI translates (map §4). Any other string falls back to the generic message. */
export const KNOWN_PIPELINE_ERROR_CODES = [
  'UnsafeContentError',
  'ModerationBlockedError',
  'CapacityTimeoutError',
  'AINoSpeechDetectedError',
  'AIEmptySelectionError',
  'SoftTimeLimitExceeded',
  'ModerationUnavailableError',
  'AIAuthenticationError',
  /** A re-dispatched auto-render whose remaining clips cannot be paid (tasks/ingestion.py). The clips stay; it can be re-dispatched with tokens. */
  'AutoRenderNotPaid',
] as const;
export type KnownPipelineErrorCode = (typeof KNOWN_PIPELINE_ERROR_CODES)[number];

/** InvalidUploadError.code (services/media_validation.py). */
export const UPLOAD_ERROR_CODES = [
  'not_a_video',
  'unreadable',
  'no_duration',
  'no_video_stream',
  'too_long',
  'too_large',
  'too_small',
] as const;
export type UploadErrorCode = (typeof UPLOAD_ERROR_CODES)[number];

/* ── Clips ──────────────────────────────────────────────────────────────── */

/** VideoClip.Status */
export const ClipStatus = z.enum(['draft', 'rendering', 'completed', 'published', 'failed']);
export type ClipStatus = z.infer<typeof ClipStatus>;

/** ScheduledPost.Status */
export const PostStatus = z.enum(['DRAFT', 'SCHEDULED', 'QUEUED', 'PROCESSING', 'PUBLISHED', 'FAILED']);
export type PostStatus = z.infer<typeof PostStatus>;

export const ClipLayer = z.object({
  id: Id,
  clip: Id,
  layer_type: z.enum(['text', 'image', 'video', 'audio']),
  start_time: z.number(),
  end_time: z.number().nullable(),
  content: z.string(),
  asset_file: z.string().nullable(),
  config: z.record(z.string(), z.unknown()),
  is_ai_generated: z.boolean(),
  prompt_used: z.string(),
});

/** Where a clip is published or scheduled: the marks on the clip card (D3). */
export const ClipPostRef = z.object({
  id: Id,
  platform: Platform,
  status: PostStatus,
  publish_at: IsoDateTime,
});
export type ClipPostRef = z.infer<typeof ClipPostRef>;

/** VideoClipSerializer (fields = '__all__' + layers) plus the proposed extras. */
export const Clip = z.object({
  id: Id,
  project: Id,
  title: z.string(),
  /** Storage key, not a URL: use `download_url` or the download endpoint (gap 5). */
  s3_object_key: z.string().nullable(),
  /** Seconds in the source video. */
  start_time: z.number(),
  end_time: z.number(),
  /** 0–100 */
  virality_score: z.number().int(),
  ai_reasoning: z.string(),
  status: ClipStatus,
  created_at: IsoDateTime,
  layers: z.array(ClipLayer),
  /** Proposed (gap 5): temporary URL to play the rendered clip. */
  preview_url: z.string().url().nullable().optional(),
  /** Proposed: poster image. */
  thumbnail_url: z.string().url().nullable().optional(),
  /** Proposed (gap 11): why the render failed (class name), for "Reintentar no gasta tokens". */
  error_code: z.string().nullable().optional(),
  /** Proposed (gap 8): networks where it is published or scheduled. */
  posts: z.array(ClipPostRef).optional(),
});
export type Clip = z.infer<typeof Clip>;

/* ── Project ────────────────────────────────────────────────────────────── */

/** Words (or segments when there are no words) with times in seconds. */
export const TranscriptItem = z.object({ start: z.number(), end: z.number(), text: z.string() }).passthrough();
export type TranscriptItem = z.infer<typeof TranscriptItem>;

/** Result of Prompt-to-Edit (documents/ia/04-prompt-to-edit.md). */
export const PteResult = z.object({
  status: z.enum(['applied', 'empty', 'failed']),
  applied: z.record(z.string(), z.unknown()).default({}),
  ignored: z.array(z.object({ field: z.string(), reason: z.string() })).default([]),
  error_code: z.string().optional(),
});
export type PteResult = z.infer<typeof PteResult>;

/** Free-form JSON; these are the keys the UI reads. Unknown keys are kept. */
export const ProjectMetadata = z
  .object({
    max_clips: z.number().int().optional(),
    selection_criteria: z.string().optional(),
    render_resolution: RenderResolution.optional(),
    use_seo: z.boolean().optional(),
    /** What the upload validation measured (CORE-05). */
    source_media: z.object({ duration_s: z.number(), width: z.number(), height: z.number(), mime: z.string() }).optional(),
    priced_duration_s: z.number().optional(),
    /** Probed duration for links. */
    duration: z.number().optional(),
    reservation_id: z.string().optional(),
    render_reservation_id: z.string().optional(),
    final_duration: z.number().optional(),
    full_text: z.string().optional(),
    pte_prompt: z.string().optional(),
    pte_explicit: z.array(z.string()).optional(),
    pte: PteResult.optional(),
    /** The video has no speech: it is edited without subtitles (`_persist_silent_transcript`). */
    no_speech: z.boolean().optional(),
    /**
     * "Renderizar sin revisar" could not be paid when the clips were chosen: the
     * project waits in the review, where the user approves (and pays) what they want.
     */
    auto_render_skipped: z.enum(['insufficient_funds', 'plan_limit']).optional(),
    /** Reservation of the automatic render (same price as approving the clips). */
    auto_render_reservation_id: z.string().optional(),
    /** Seconds that reservation paid for. */
    auto_render_seconds: z.number().optional(),
    /** Clips that reservation paid for: only they are charged. */
    auto_render_clip_ids: z.array(z.string()).optional(),
  })
  .passthrough();
export type ProjectMetadata = z.infer<typeof ProjectMetadata>;

export const ApprovedSegment = z.object({ start: z.number().min(0), end: z.number().positive(), text: z.string().optional() }).passthrough();

/** Proposed (gap 4): the project's charge and where it stands. */
export const ProjectCost = z.object({
  amount: Decimal,
  state: z.enum(['reserved', 'charged', 'refunded']),
  transaction_id: Id.nullable(),
});
export type ProjectCost = z.infer<typeof ProjectCost>;

/** Proposed (gap 15): times per stage for the process timeline. */
export const StageTiming = z.object({
  started_at: IsoDateTime.nullable(),
  finished_at: IsoDateTime.nullable(),
  duration_s: z.number().nullable(),
});
export type StageTiming = z.infer<typeof StageTiming>;

/** VideoProjectSerializer: every model field, `clips`, `brand_kit_name`, `proxy_url`, plus the proposed extras (all optional). */
export const Project = z.object({
  id: Id,
  workspace: Id,
  uploaded_by: Id.nullable(),
  title: z.string(),
  original_r2_key: z.string().nullable(),
  proxy_r2_key: z.string().nullable(),
  final_export_r2_key: z.string().nullable(),
  project_type: ProjectType,
  editing_style: EditingStyle,
  intelligence_level: IntelligenceLevel,
  auto_render_bypass: z.boolean(),
  brand_kit: Id.nullable(),
  /** Absent when the project has no brand kit (DRF skips a dotted source that is null). */
  brand_kit_name: z.string().optional(),
  aspect_ratio: AspectRatio,
  render_layout: Layout,
  gameplay_position: Position,
  camera_selection_position: Position,
  manual_camera_x: z.number(),
  manual_camera_y: z.number(),
  manual_camera_zoom: z.number(),
  /** PiP layout only. */
  pip_corner: PipCorner,
  /** Trending layout only: one end of the border gradient. */
  trending_color: ColorValue,
  /** Trending layout only: text above the video; empty means none. */
  trending_text: z.string(),
  speaker_tracking: z.boolean(),
  add_subtitles: z.boolean(),
  subtitle_color: ColorValue,
  subtitle_with_emojis: z.boolean(),
  subtitle_words_per_segment: z.number().int(),
  subtitle_size: SubtitleSize,
  subtitle_position: SubtitlePosition,
  use_facetracking: z.boolean(),
  remove_silences: z.boolean(),
  /** URL of the uploaded file, or null for link projects. */
  source_file: z.string().nullable(),
  video_url: z.string().nullable(),
  metadata: ProjectMetadata,
  ai_rationale_log: z.record(z.string(), z.unknown()),
  transcript_data: z.array(TranscriptItem).nullable(),
  approved_segments: z.array(ApprovedSegment).nullable(),
  status: ProjectStatus,
  pipeline_stage: PipelineStage,
  pipeline_stage_status: PipelineStageStatus,
  pipeline_attempts: z.number().int().nonnegative(),
  /** Class name of the exception, '' when there is none. */
  pipeline_error_code: z.string(),
  pipeline_error_at: IsoDateTime.nullable(),
  created_at: IsoDateTime,
  clips: z.array(Clip),
  /** Presigned URL of the proxy video (1 h), null until ingestion makes it. */
  proxy_url: z.string().nullable(),
  /* Proposed extras */
  uploaded_by_detail: UserRef.nullable().optional(),
  thumbnail_url: z.string().url().nullable().optional(),
  /** Source duration in seconds, whatever the origin (gap: today it is spread over metadata). */
  duration_seconds: z.number().nullable().optional(),
  cost: ProjectCost.nullable().optional(),
  stage_timings: z.record(z.string(), StageTiming).optional(),
  /** Seconds until "Listo para revisar" (gap 18). */
  eta_seconds: z.number().int().nonnegative().nullable().optional(),
});
export type Project = z.infer<typeof Project>;

/** Filter of the projects list (map B2). */
export const ProjectStatusGroup = z.enum(['all', 'in_progress', 'to_review', 'ready', 'failed']);
export type ProjectStatusGroup = z.infer<typeof ProjectStatusGroup>;

export const ProjectsQuery = PageQuery.extend({
  status_group: ProjectStatusGroup.optional(),
  /** Matches the title. */
  search: z.string().optional(),
  ordering: z.enum(['-created_at', 'created_at', 'title']).optional(),
});
export type ProjectsQuery = z.infer<typeof ProjectsQuery>;

const ProjectsPage = paginated(Project).extend({
  /** Proposed: how many projects fall in each filter, for the chips. */
  status_counts: z.record(ProjectStatusGroup, z.number().int().nonnegative()).optional(),
});

/**
 * Today the backend answers a bare array (no pagination class). The contract is
 * the paginated shape; the bare array is accepted and wrapped so the list works
 * against the current backend too.
 */
export const ProjectsResponse = z.union([ProjectsPage, z.array(Project)]).transform((value) =>
  Array.isArray(value)
    ? // `paginated: false`: the backend ignored page, filter, search and order, so the screen applies them itself.
      { count: value.length, next: null, previous: null, results: value, status_counts: undefined, paginated: false as const }
    : { ...value, paginated: true as const },
);
export type ProjectsResponse = z.infer<typeof ProjectsResponse>;

const FileField = z.custom<Blob>((value) => typeof Blob !== 'undefined' && value instanceof Blob, 'Archivo inválido');

/** Fields of the creation body (multipart). Field names as VideoProjectSerializer reads them. */
const CreateProjectFields = z.object({
  title: z.string().min(1).max(255).optional(),
  /** .mp4, .mov or .avi, up to 500 MB. Exactly one of source_file / video_url. */
  source_file: FileField.optional(),
  video_url: z.string().url().max(500).optional(),
  project_type: ProjectType.optional(),
  editing_style: EditingStyle.optional(),
  intelligence_level: IntelligenceLevel.optional(),
  auto_render_bypass: z.boolean().optional(),
  brand_kit: Id.optional(),
  aspect_ratio: AspectRatio.optional(),
  render_layout: Layout.optional(),
  gameplay_position: Position.optional(),
  camera_selection_position: Position.optional(),
  manual_camera_x: z.number().min(0).max(1).optional(),
  manual_camera_y: z.number().min(0).max(1).optional(),
  manual_camera_zoom: z.number().min(1).max(4).optional(),
  pip_corner: PipCorner.optional(),
  trending_color: HexColor.optional(),
  trending_text: z.string().max(60).optional(),
  speaker_tracking: z.boolean().optional(),
  /** "Not sent" is its own case in the serializer (`_NotSentBooleanField`): true on create, unchanged on update. */
  add_subtitles: z.boolean().optional(),
  subtitle_color: HexColor.optional(),
  subtitle_words_per_segment: z.number().int().min(1).optional(),
  subtitle_size: SubtitleSize.optional(),
  subtitle_position: SubtitlePosition.optional(),
  use_facetracking: z.boolean().optional(),
  remove_silences: z.boolean().optional(),
  /** 1–20, stored in metadata.max_clips. */
  max_clips: z.number().int().min(1).max(20).optional(),
  selection_criteria: z.string().optional(),
  /** Paid add-on (+5 tokens), plan permitting. */
  use_seo: z.boolean().optional(),
  /** Priced at creation; 720p when absent. Links only accept 720p. */
  render_resolution: RenderResolution.optional(),
});

const oneSource = {
  check: (value: { source_file?: unknown; video_url?: unknown }) => Boolean(value.source_file) !== Boolean(value.video_url),
  issue: { message: "Mandá 'source_file' o 'video_url', uno solo.", path: ['source_file'] },
};

/**
 * Body of `POST /projects/`. `add_subtitles` always travels (true unless the
 * user turns subtitles off), so the request says what the form shows whatever
 * the serializer's default is.
 */
export const CreateProjectRequest = CreateProjectFields.extend({ add_subtitles: z.boolean().default(true) }).refine(oneSource.check, oneSource.issue);
/** What a screen passes to `useCreateProject` (`add_subtitles` may be left out: it defaults to true). */
export type CreateProjectRequest = z.input<typeof CreateProjectRequest>;

/**
 * Body of `POST /projects/from-prompt/`. Here `add_subtitles` stays out unless
 * the user chose it: a field that is present counts as explicit and wins over
 * the text, so sending it would stop "sin subtítulos" in the prompt from working.
 */
export const CreateFromPromptRequest = CreateProjectFields.extend({ prompt: z.string().min(1, 'Contanos qué querés') }).refine(oneSource.check, oneSource.issue);
export type CreateFromPromptRequest = z.input<typeof CreateFromPromptRequest>;

export const CostDetails = z.object({ tokens_reserved: Decimal, transaction_id: Id });

export const CreateProjectResponse = Project.extend({
  cost_details: CostDetails,
  message: z.string(),
});
export type CreateProjectResponse = z.infer<typeof CreateProjectResponse>;

export const CreateFromPromptResponse = CreateProjectResponse.extend({
  prompt_config: z.object({ status: z.literal('pending'), detail: z.string() }),
});

export const UpdateProjectRequest = z.object({ title: z.string().min(1).max(255) }).partial();

export const PaperEditData = z.object({
  project_id: Id,
  title: z.string(),
  status: ProjectStatus,
  /** Presigned proxy URL (2 h). */
  proxy_url: z.string().nullable(),
  transcript: z.array(TranscriptItem).nullable(),
  ai_suggestions: z.array(Clip),
  metadata: ProjectMetadata,
});
export type PaperEditData = z.infer<typeof PaperEditData>;

export const ApprovePaperEditRequest = z.object({
  /** At least one; `0 <= start < end`. Extra keys (title, clip_id) are kept by the backend. */
  approved_segments: z
    .array(
      z
        .object({
          start: z.number().min(0),
          end: z.number().positive(),
          text: z.string().optional(),
          title: z.string().optional(),
          clip_id: Id.optional(),
        })
        .refine((segment) => segment.end > segment.start, { message: 'El fin tiene que ser mayor que el inicio.' }),
    )
    .min(1),
});
export type ApprovePaperEditRequest = z.infer<typeof ApprovePaperEditRequest>;

export const ApprovePaperEditResponse = z.object({
  message: z.string(),
  project_id: Id,
  final_cost: Decimal,
  transaction_id: Id,
});

/** Who did what, and the token movements of the project (D1 › Actividad). */
export const ActivityItem = z.object({
  id: Id,
  type: z.enum([
    'project_created',
    'prompt_applied',
    'review_ready',
    'review_approved',
    'render_finished',
    'clip_edited',
    'clip_retried',
    'post_scheduled',
    'post_published',
    'post_failed',
    'project_renamed',
    'project_failed',
    'tokens_reserved',
    'tokens_charged',
    'tokens_refunded',
  ]),
  /** Null: the system did it. */
  actor: UserRef.nullable(),
  created_at: IsoDateTime,
  /** Ready-to-show sentence in Spanish. */
  description: z.string(),
  /** Token movement, signed (negative = spent or reserved). */
  amount: Decimal.nullable(),
  platform: Platform.nullable().optional(),
});
export type ActivityItem = z.infer<typeof ActivityItem>;

/** New style for "Renderizar todo de nuevo" / "Volver a renderizar con otro estilo". */
export const RerenderRequest = z
  .object({
    editing_style: EditingStyle,
    render_layout: Layout,
    pip_corner: PipCorner,
    trending_color: HexColor,
    trending_text: z.string().max(60),
    aspect_ratio: AspectRatio,
    brand_kit: Id.nullable(),
    add_subtitles: z.boolean(),
    subtitle_color: HexColor,
    subtitle_size: SubtitleSize,
    subtitle_position: SubtitlePosition,
    subtitle_words_per_segment: z.number().int().min(1),
    use_facetracking: z.boolean(),
    remove_silences: z.boolean(),
  })
  .partial();
export type RerenderRequest = z.infer<typeof RerenderRequest>;

export const PaidActionResponse = z.object({
  message: z.string(),
  final_cost: Decimal,
  transaction_id: Id,
});

const paymentErrors = [
  { status: 402, when: 'Saldo insuficiente: `{ error, required }`. La UI abre el diálogo D2.' },
  { status: 403, when: 'El plan no lo permite (duración, resolución o SEO). La UI abre el diálogo D3.' },
];

export const listProjects = defineEndpoint({
  id: 'projects.list',
  method: 'GET',
  path: '/api/v1/projects/',
  summary: 'Proyectos del workspace, del más nuevo al más viejo.',
  query: ProjectsQuery,
  response: ProjectsResponse,
  errors: [],
  backend: {
    status: 'partial',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.list + VideoProjectSerializer',
    gap: 14,
    note: 'Hoy devuelve un array sin paginar y sin filtros. Desde el PR #125 lee `X-Workspace-Id` y trae solo los proyectos del workspace activo (404 si el usuario no es miembro activo); B2 igual filtra por `workspace` en el navegador, como defensa. Propuesto: paginación de 25, `status_group`, `search`, `status_counts` y los campos extra del proyecto.',
  },
  screens: ['B2', 'J1', 'B1'],
});

export const createProject = defineEndpoint({
  id: 'projects.create',
  method: 'POST',
  path: '/api/v1/projects/',
  // After the upload the server still validates the video (ffprobe), moves it to storage, prices it and reserves the tokens.
  timeoutMs: SLOW_REQUEST_TIMEOUT_MS,
  summary: 'Crea un proyecto desde un archivo (multipart) o un link, reserva los tokens y arranca la ingesta.',
  request: CreateProjectRequest,
  bodyType: 'multipart',
  response: CreateProjectResponse,
  successStatus: 201,
  errors: [
    {
      status: 400,
      when: 'Validación. En `source_file` llega el mensaje de InvalidUploadError; la UI lo traduce por código (propuesto: `{message, code}`) o por el texto. `project_type` para video completo con link o de más de 10 min. Link con resolución distinta de 720p.',
    },
    ...paymentErrors,
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.create + VideoProjectSerializer.create/validate',
    note: 'El código de InvalidUploadError no llega en el cuerpo (DRF solo manda el mensaje); el 403 trae un texto en inglés sin código. Desde el PR #125 el proyecto se crea en el workspace de `X-Workspace-Id` y se aplica el rol (el Lector recibe 403 `{ detail }` en español).',
  },
  screens: ['C1'],
});

export const createProjectFromPrompt = defineEndpoint({
  id: 'projects.createFromPrompt',
  method: 'POST',
  path: '/api/v1/projects/from-prompt/',
  // After the upload the server still validates the video (ffprobe), moves it to storage, prices it and reserves the tokens.
  timeoutMs: SLOW_REQUEST_TIMEOUT_MS,
  summary: 'Igual que crear, más `prompt`: la IA arma la configuración al iniciar la ingesta (queda en `metadata.pte`).',
  request: CreateFromPromptRequest,
  bodyType: 'multipart',
  response: CreateFromPromptResponse,
  successStatus: 201,
  errors: [
    { status: 400, when: '`prompt` vacío ("Contanos qué querés") o más largo que el máximo; más las validaciones de crear.' },
    ...paymentErrors,
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.from_prompt',
    note: '`add_subtitles` es opcional y «no enviado» es un caso propio (`_NotSentBooleanField`, PR #121): al crear queda en true y al editar no cambia. Así el texto puede apagar los subtítulos; si el campo viaja, cuenta como elegido a mano y le gana al texto.',
  },
  screens: ['C1'],
});

export const getProject = defineEndpoint({
  id: 'projects.detail',
  method: 'GET',
  path: '/api/v1/projects/:id/',
  summary: 'Detalle del proyecto con el estado del pipeline, la metadata, la transcripción y sus clips.',
  response: Project,
  errors: [{ status: 404, when: 'No existe o es de otro workspace. La UI muestra K1.' }],
  backend: {
    status: 'partial',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.retrieve + VideoProjectSerializer',
    gap: [4, 15, 18],
    note: 'Faltan `cost` (vínculo con la transacción y estado devuelto), `stage_timings`, `eta_seconds`, `duration_seconds`, `uploaded_by_detail` y `thumbnail_url`.',
  },
  screens: ['D1', 'D1e', 'D1f', 'D3', 'D4', 'D5'],
});

export const updateProject = defineEndpoint({
  id: 'projects.update',
  method: 'PATCH',
  path: '/api/v1/projects/:id/',
  summary: 'Renombra el proyecto.',
  request: UpdateProjectRequest,
  response: Project,
  errors: [
    { status: 400, when: 'Título vacío.' },
    { status: 403, when: 'El rol no puede editar (Lector).' },
    {
      status: 409,
      when: 'El proyecto se está renderizando y el pedido cambia algo más que `title` (ProjectRenderingError). El cuerpo trae solo `{ detail }`; el código `project_rendering` no llega. Renombrar siempre se puede.',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.partial_update (ModelViewSet) + VideoProjectSerializer.update',
    note: 'El PATCH acepta más campos del serializer; el frontend solo manda `title`, así que no debería ver el 409.',
  },
  screens: ['B2', 'D1'],
});

export const deleteProject = defineEndpoint({
  id: 'projects.delete',
  method: 'DELETE',
  path: '/api/v1/projects/:id/',
  summary: 'Borra el proyecto con su video, sus clips y sus publicaciones programadas. No devuelve lo cobrado.',
  response: NoContent,
  successStatus: 204,
  errors: [{ status: 403, when: 'El rol no puede borrar (Lector; Editor solo los propios).' }],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.destroy (ModelViewSet)',
    note: 'No aplica la regla de roles (hueco 3).',
  },
  screens: ['B2', 'D1', 'D4 (diálogo)'],
});

export const getPaperEditData = defineEndpoint({
  id: 'projects.paperEditData',
  method: 'GET',
  path: '/api/v1/projects/:id/paper-edit-data/',
  summary: 'Datos de la revisión: video proxy, transcripción, clips sugeridos y metadata.',
  response: PaperEditData,
  errors: [{ status: 404, when: 'No existe o es de otro workspace.' }],
  backend: { status: 'exists', source: 'back/apps/videos/views.py · VideoProjectViewSet.paper_edit_data' },
  screens: ['D2'],
});

export const approvePaperEdit = defineEndpoint({
  id: 'projects.approvePaperEdit',
  method: 'POST',
  path: '/api/v1/projects/:id/approve-paper-edit/',
  summary: 'Aprueba los segmentos elegidos, reserva el costo del render y lo despacha.',
  request: ApprovePaperEditRequest,
  response: ApprovePaperEditResponse,
  errors: [
    {
      status: 400,
      when: 'Tres casos, todos `{ error }` (se distinguen con `approveErrorKind()` de `src/lib/error-messages.ts`): el proyecto ya no está en revisión ("El proyecto no está en espera de aprobación. Estado actual: rendering"): la UI avisa y lleva a Clips; el plan no permite la duración o la resolución ("Error en el motor de precios: …"): la UI abre D3; segmentos inválidos. De estos últimos, uno más largo que el límite del render (PR #124: "…cada segmento puede durar hasta 1800 segundos. Dividí los más largos en varios.") se muestra con la frase del servidor; el resto, con un mensaje genérico.',
    },
    { status: 402, when: 'Saldo insuficiente: `{ error, required }`. La UI abre D2.' },
    { status: 409, when: 'Solo en la carrera de dos aprobaciones simultáneas: `{ error }`. La UI lleva a Clips.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.approve_paper_edit',
    note: 'Propuesto: 409 con `code: "not_awaiting_approval"` para un proyecto que ya no está en revisión y 403 con `code` para el límite del plan, en lugar de los 400 de hoy.',
  },
  screens: ['D2'],
});

export const redispatchProject = defineEndpoint({
  id: 'projects.redispatch',
  method: 'POST',
  path: '/api/v1/projects/:id/redispatch/',
  summary: 'Vuelve a despachar el pipeline de un proyecto detenido, desde la última etapa terminada. No cobra.',
  response: Project,
  successStatus: 202,
  errors: [
    { status: 403, when: 'Solo Dueño y Admin.' },
    { status: 409, when: 'El proyecto ya está en curso o terminó.' },
  ],
  backend: {
    status: 'proposed',
    note: 'Hoy es una acción del admin de Django (mapa §7: herramientas internas fuera del MVP). Se define para no tener que tocar el contrato cuando se exponga.',
  },
  screens: ['D1'],
});

export const rerenderProject = defineEndpoint({
  id: 'projects.rerender',
  method: 'POST',
  path: '/api/v1/projects/:id/rerender/',
  summary: 'Renderiza todos los clips de nuevo con otro estilo (layout, subtítulos, brand kit). Cobra el render.',
  request: RerenderRequest,
  response: PaidActionResponse,
  successStatus: 202,
  errors: [
    { status: 402, when: 'Saldo insuficiente: `{ error, required }`. La UI abre D2.' },
    { status: 409, when: 'El proyecto está procesando o renderizando.' },
  ],
  backend: { status: 'proposed', gap: 11 },
  screens: ['D1', 'D4', 'D5'],
});

export const getProjectActivity = defineEndpoint({
  id: 'projects.activity',
  method: 'GET',
  path: '/api/v1/projects/:id/activity/',
  summary: 'Quién hizo qué en el proyecto y sus movimientos de tokens, del más nuevo al más viejo.',
  response: z.array(ActivityItem),
  errors: [],
  backend: { status: 'proposed', gap: 4 },
  screens: ['D1f'],
});

export const downloadAllClips = defineEndpoint({
  id: 'projects.downloadAll',
  method: 'POST',
  path: '/api/v1/projects/:id/clips/download/',
  // The zip is built while the request waits.
  timeoutMs: SLOW_REQUEST_TIMEOUT_MS,
  summary: 'Arma un .zip con los clips listos (todos, o los de `clip_ids`) y devuelve la URL temporal.',
  request: z.object({ clip_ids: z.array(Id).optional() }),
  response: z.object({ url: z.string().url(), expires_at: IsoDateTime }),
  errors: [{ status: 409, when: 'Ningún clip está listo todavía.' }],
  backend: { status: 'proposed', gap: 17 },
  screens: ['D3'],
});

export const LinkProbe = z.object({
  /** Normalized URL. */
  video_url: z.string().url(),
  /** "YouTube", "Vimeo"… */
  provider: z.string(),
  title: z.string(),
  duration_seconds: z.number().nonnegative(),
  thumbnail_url: z.string().url().nullable(),
});
export type LinkProbe = z.infer<typeof LinkProbe>;

export const probeLink = defineEndpoint({
  id: 'projects.probeLink',
  method: 'POST',
  path: '/api/v1/projects/probe-link/',
  // The backend asks the video site: slower than the API itself.
  timeoutMs: 60_000,
  summary: 'Reconoce un link antes de crear el proyecto: de dónde es, su título y su duración.',
  request: z.object({ video_url: z.string().url().max(500) }),
  response: LinkProbe,
  errors: [
    { status: 400, when: 'El link no es de un sitio soportado (`code: "unsupported"`) o no se puede bajar: privado, borrado o con restricción (`code: "unavailable"`).' },
    { status: 403, when: 'La duración supera el plan (`code: "duration"`, `limit` en segundos). La UI lo muestra junto al campo, con link a Planes.' },
  ],
  backend: {
    status: 'proposed',
    note: 'Sin número de hueco: lo pide C1 (artboard «Pegar link») y cierra el aviso del mapa de que los links no pasan por la validación de duración del plan.',
  },
  screens: ['C1'],
});

export const projectsEndpoints = [
  probeLink,
  listProjects,
  createProject,
  createProjectFromPrompt,
  getProject,
  updateProject,
  deleteProject,
  getPaperEditData,
  approvePaperEdit,
  redispatchProject,
  rerenderProject,
  getProjectActivity,
  downloadAllClips,
] as const;
