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
  /** The creation could not queue the ingestion (broker down): the project fails and its reservation goes back, together (views.py · _fail_unqueued_project). Never resumed. */
  'IngestionNotQueued',
  /** A link project whose link is not one video of YouTube, Vimeo, Twitch or Google Drive when it is fetched (tasks/shared.py · download_from_youtube). Not retried. */
  'UnsupportedLinkError',
  /** The linked video is longer than the plan allows (another plan would take it). Not retried. */
  'LinkTooLongError',
  /** The linked video is longer than the app accepts on any plan (AI_UPLOAD_MAX_DURATION_SECONDS, 3 h). Not retried, and redispatch answers 409 `duration_app`. */
  'LinkTooLongForAppError',
  /** The linked video's file is larger than LINK_DOWNLOAD_MAX_BYTES. Not retried. */
  'LinkTooLargeError',
  /** The ingestion made no progress (its message was lost or its worker died) and recover_stuck_renders failed it and returned its reservation (tasks/render_recovery.py). It can be re-dispatched: it reserves again and runs. */
  'IngestionStuck',
] as const;
export type KnownPipelineErrorCode = (typeof KNOWN_PIPELINE_ERROR_CODES)[number];

/** `code` of the 413 of a file over MAX_UPLOAD_BYTES (back/apps/core/upload_limits.py). */
export const FILE_TOO_LARGE_CODE = 'file_too_large';
/** Body of that 413. A proxy in front of the backend may answer a 413 of its own, without it. */
export const UploadTooLargeBody = z.object({
  error: z.string(),
  code: z.literal(FILE_TOO_LARGE_CODE),
  /** MAX_UPLOAD_BYTES, the `upload_max_size_bytes` of GET /pricing/. */
  limit_bytes: z.number().int().positive(),
});
export type UploadTooLargeBody = z.infer<typeof UploadTooLargeBody>;

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

/**
 * A publication of the clip (VideoClipSerializer.posts · ClipPostSerializer): where and when it goes or
 * went out, and how it is. The marks and the counts of the clip cards (D3) read it.
 */
export const ClipPostRef = z.object({
  id: Id,
  platform: Platform,
  status: PostStatus,
  publish_at: IsoDateTime,
});
export type ClipPostRef = z.infer<typeof ClipPostRef>;

/** VideoClipSerializer (fields = '__all__' + layers, preview_url, thumbnail_url, render_queued and posts) plus the proposed extras. */
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
  /**
   * Temporary URL that plays the rendered file (VideoClipSerializer.preview_url): a presigned GET that
   * lasts what a download link does (CLIP_DOWNLOAD_URL_TTL_SECONDS, 1 h), or the absolute /media/ URL
   * with local files (USE_R2=False). It is signed again on every response. Null unless the clip is
   * `completed` or `published` and has a file. Optional for backends from before it existed.
   */
  preview_url: z.string().url().nullable().optional(),
  /**
   * Temporary URL of the thumbnail (a JPEG frame of the render, 360 px on the long side), with the same
   * expiry as `preview_url`. Null when the clip has none: it was never rendered, it was rendered before
   * thumbnails existed, or the thumbnail could not be made (it is best effort and never fails a render).
   */
  thumbnail_url: z.string().url().nullable().optional(),
  /**
   * True only while the clip, in `draft`, waits for a worker in an open paid render (a render, a
   * retry or a rerender); false otherwise, also once it is `rendering`. Read-only. The backend adds
   * it in PR #147 (VideoClipSerializer); older backends do not send it, so it is optional and the UI
   * falls back to "a draft of a project that is rendering".
   */
  render_queued: z.boolean().optional(),
  /** Proposed (gap 11): why the render failed (class name). The retry is paid whatever the reason (clips.retry). */
  error_code: z.string().nullable().optional(),
  /**
   * Every publication of the clip, whatever its status (drafts and failures too), by `publish_at`
   * (VideoClipSerializer.posts): D3 counts «Publicados» and «Programados» from it. Read once for the whole
   * response. Optional for backends from before it existed: without it the clip shows «Listo».
   */
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

/**
 * The project's charge and where it stands: its creation reservation (`metadata.reservation_id`). A partial
 * charge shows what was charged (the returned part is in the activity); a refund, what had been set aside.
 */
export const ProjectCost = z.object({
  amount: Decimal,
  state: z.enum(['reserved', 'charged', 'refunded']),
  transaction_id: Id.nullable(),
});
export type ProjectCost = z.infer<typeof ProjectCost>;

/**
 * Times of a stage. One that finished (its last successful run) always has `duration_s`; its `started_at`
 * and `finished_at` are null when it finished before the backend recorded when it ended. One that started
 * and did not finish (the one running, or the one where the project failed) has `started_at` and a null
 * `duration_s`; its `finished_at` is when a failed project stopped (`pipeline_error_at`), null otherwise.
 */
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
  /** Presigned URL of the proxy video (1 h), null until ingestion makes it. With local files (USE_R2=False), the absolute /media/ URL. */
  proxy_url: z.string().nullable(),
  /**
   * The thumbnail of the project's first clip that has one (the same temporary URL as the clip's
   * `thumbnail_url`), null when no clip has a thumbnail. Optional for backends from before it existed.
   */
  thumbnail_url: z.string().url().nullable().optional(),
  /** Who created the project (no email); null when that user is gone. Optional for older backends. */
  uploaded_by_detail: UserRef.nullable().optional(),
  /** Null when the project has no creation reservation. Optional for older backends. */
  cost: ProjectCost.nullable().optional(),
  /** Keyed by pipeline stage (`transcribed`, `moderated`…): only the stages that finished. */
  stage_timings: z.record(z.string(), StageTiming).optional(),
  /**
   * While it is processing: seconds until "Listo para revisar" (the estimate of Nuevo proyecto for its duration,
   * minus what its finished stages took). Null otherwise and for a whole-video edit. Optional for older backends.
   */
  eta_seconds: z.number().int().nonnegative().nullable().optional(),
  /** Proposed, not sent yet: source duration in seconds, whatever the origin (today it is spread over metadata). */
  duration_seconds: z.number().nullable().optional(),
});
export type Project = z.infer<typeof Project>;

/**
 * A row of the projects list (VideoProjectListSerializer): the project without the transcript (`transcript_data`
 * and `metadata.full_text`) and without `ai_rationale_log`. Only the project's own screens read them, from
 * `GET /projects/:id/`; with a long video they are most of the weight of a project.
 */
export const ProjectListItem = Project.omit({ transcript_data: true, ai_rationale_log: true });
export type ProjectListItem = z.infer<typeof ProjectListItem>;

/** Filter of the projects list (map B2). */
export const ProjectStatusGroup = z.enum(['all', 'in_progress', 'to_review', 'ready', 'failed']);
export type ProjectStatusGroup = z.infer<typeof ProjectStatusGroup>;

export const ProjectsQuery = PageQuery.extend({
  status_group: ProjectStatusGroup.optional(),
  /** Part of the title, whatever the case. Plain text: `%` and `_` match themselves. */
  search: z.string().optional(),
  /** Newest first (`-created_at`) when absent. `title` ignores the case. */
  ordering: z.enum(['-created_at', 'created_at', 'title']).optional(),
});
export type ProjectsQuery = z.infer<typeof ProjectsQuery>;

/** How many projects fall in each filter, for the chips: under the same search, whatever the filter and the page asked for. */
export const ProjectStatusCounts = z.object({
  all: z.number().int().nonnegative(),
  in_progress: z.number().int().nonnegative(),
  to_review: z.number().int().nonnegative(),
  ready: z.number().int().nonnegative(),
  failed: z.number().int().nonnegative(),
});
export type ProjectStatusCounts = z.infer<typeof ProjectStatusCounts>;

/** A page of projects (ProjectPagination): 25 unless `page_size` says otherwise, 100 at most. */
export const ProjectsResponse = paginated(ProjectListItem).extend({ status_counts: ProjectStatusCounts });
export type ProjectsResponse = z.infer<typeof ProjectsResponse>;

const FileField = z.custom<Blob>((value) => typeof Blob !== 'undefined' && value instanceof Blob, 'Archivo inválido');

/** Fields of the creation body (multipart). Field names as VideoProjectSerializer reads them. */
const CreateProjectFields = z.object({
  title: z.string().min(1).max(255).optional(),
  /** .mp4, .mov or .avi, up to MAX_UPLOAD_BYTES (`upload_max_size_bytes` of GET /pricing/, 2 GB). Exactly one of source_file / video_url. */
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

/** Body of `POST projects/{id}/redispatch/`: the project, with `cost_details` only when it reserved again (ProjectRedispatchView). */
export const RedispatchResponse = Project.extend({ cost_details: CostDetails.optional() });
export type RedispatchResponse = z.infer<typeof RedispatchResponse>;

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
  /**
   * In `tokens_reserved`, `tokens_charged` and `tokens_refunded`: how the reservation those items are about
   * ended. The reservation, its charge and its refund are items of ONE movement of the balance: with this the
   * screen shows them as a group whose signed amounts add up to what the balance moved.
   */
  reservation_state: z.enum(['reserved', 'charged', 'partially_charged', 'refunded']).nullable().optional(),
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

/** A file over MAX_UPLOAD_BYTES: refused before anything is created or reserved. */
const uploadTooLargeError = {
  status: 413,
  when: 'El archivo pesa más que `MAX_UPLOAD_BYTES` (2 GB, el `upload_max_size_bytes` de `GET /pricing/`): `{ error, code: "file_too_large", limit_bytes }`. El backend lo responde antes de leer el cuerpo (por el Content-Length) o al validar el archivo, antes de ffprobe, del storage y de la reserva: no se crea ni se reserva nada. Un proxy adelante puede responder su propio 413, sin ese cuerpo. La web revisa el peso antes de subir; si igual llega un 413, lo muestra junto al archivo.',
};

/** Creation only: the reservation was made but the broker did not take the ingestion. */
const notQueuedError = {
  status: 503,
  when: 'No se pudo encolar la ingesta (broker caído): `{ error, code: "IngestionNotQueued", project_id, refunded }`. Con `refunded: true` el proyecto queda `failed` con ese `pipeline_error_code` y la reserva ya volvió; con `false` no se pudo escribir y el proyecto sigue en `uploaded` con la reserva. La UI muestra el genérico de 5xx.',
};

export const listProjects = defineEndpoint({
  id: 'projects.list',
  method: 'GET',
  path: '/api/v1/projects/',
  summary: 'Proyectos del workspace, del más nuevo al más viejo.',
  query: ProjectsQuery,
  response: ProjectsResponse,
  errors: [
    { status: 400, when: 'Un `status_group` o un `ordering` que no existe, o una búsqueda con un carácter nulo: `{ campo: [mensaje] }`.' },
    { status: 404, when: 'Una página que no existe: `{ detail: "Página inválida." }`. B2 retrocede hasta una que exista.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.list + ProjectPagination + ProjectsQuerySerializer + VideoProjectListSerializer',
    note: 'Solo los proyectos del workspace activo (`X-Workspace-Id`; 404 si el usuario no es miembro activo). Páginas de 25 (`page_size` hasta 100). `status_group` agrupa los estados como los filtros de B2: `in_progress` (uploaded, ingesting, rendering), `to_review` (awaiting_approval), `ready` (completed, partial) y `failed`. `search` busca en el título sin distinguir mayúsculas. `status_counts` cuenta cada filtro con la misma búsqueda, sin importar el filtro ni la página pedidos. Cada fila es el proyecto sin `transcript_data`, `ai_rationale_log` ni `metadata.full_text`, `metadata.render_outcomes` y `metadata.clip_render_scope` (`ProjectListItem`): esos van solo en `GET /projects/:id/`. Las consultas a la base no crecen con la página. De los campos extra del proyecto solo falta `duration_seconds`: la pantalla lee la duración de `metadata`.',
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
      when: 'Validación. En `source_file` llega el mensaje de InvalidUploadError; la UI lo traduce por código (propuesto: `{message, code}`) o por el texto. `project_type` para video completo con link o de más de 10 min. Link con resolución distinta de 720p. En `video_url`, un link que no es de un host admitido, que apunta a una IP o una dirección interna, o que no nombra un solo video (una lista, un canal): las mismas reglas que `probe-link`. Se guarda la URL del video (`watch?v=<id>` en YouTube).',
    },
    ...paymentErrors,
    uploadTooLargeError,
    notQueuedError,
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
    uploadTooLargeError,
    notQueuedError,
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
    note: 'Trae `cost` (la reserva de creación: reservado, cobrado o devuelto), `stage_timings` (las etapas terminadas, con `started_at` y `finished_at` en `null` en las que terminaron antes de que se guardara; y la etapa que arrancó y no terminó, la que está en curso o donde falló el proyecto, con `started_at` de `metadata.stage_started_at`, `duration_s` en `null` y `finished_at` solo si el proyecto falló: `pipeline_error_at`) y `uploaded_by_detail` (sin el email). `eta_seconds` mientras se procesa (el estimado de Nuevo proyecto menos lo que tardaron las etapas terminadas). Falta `duration_seconds` (la duración sale de `metadata.source_media`). Cada clip trae `preview_url` y `thumbnail_url`, y el proyecto `thumbnail_url` (la miniatura de su primer clip que tenga una): son URLs temporales (1 h, se firman de nuevo en cada respuesta) y vienen en `null` cuando no hay render o no hay miniatura.',
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
  errors: [
    { status: 403, when: 'El rol no puede borrar (Lector; Editor solo los propios).' },
    {
      status: 409,
      when: '`{ error, code }`: el proyecto puede estar procesando o renderizando (`project_processing`) o terminó con tokens reservados sin liquidar (`reservation_open`). El diálogo muestra el mensaje del servidor. Uno trabado del todo (ningún worker y nada en `RENDER_STUCK_AFTER_SECONDS`, sin nada entregado) se borra y devuelve lo reservado.',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · VideoProjectViewSet.destroy + views_clips.project_delete_decision',
    note: 'Nada liquida una reserva cuyo proyecto ya no existe, por eso el 409. No devuelve nada de lo cobrado.',
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
  summary: 'Vuelve a despachar el pipeline de un proyecto que falló, desde la última etapa terminada. Si lo que falta se había devuelto, lo reserva de nuevo por el mismo monto.',
  response: RedispatchResponse,
  successStatus: 202,
  errors: [
    { status: 402, when: 'Saldo insuficiente para volver a reservar lo devuelto: `{ error, required }`. La UI abre D2.' },
    { status: 403, when: 'Solo Dueño y Admin.' },
    {
      status: 409,
      when: '`{ error, code }`: no está en error (`not_failed`), la moderación frenó su contenido (`moderation_blocked`, solo `UnsafeContentError`), su ingesta nunca se encoló al crearlo (`not_queued`: se crea de nuevo), sus clips ya se liquidaron y se reintentan de a uno desde Clips (`render_settled`), su video completo ya se renderizó (`use_rerender`), no tiene reserva de creación (`no_reservation`) o su link dura más de las 3 h que procesa la app (`duration_app`: falló con `LinkTooLongForAppError`; se crea otro con un video más corto). Uno que falló con `LinkTooLongError` sí se re-despacha: el plan pudo cambiar.',
    },
    { status: 503, when: 'No se pudo encolar: lo reservado vuelve (el proyecto queda con `pipeline_error_code: "RedispatchNotQueued"`) y se puede probar de nuevo.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_clips.py · ProjectRedispatchView',
    note: 'No cobra lo que sigue pagado: un proyecto con los clips elegidos sigue gratis (su render se paga solo). Lo que se devolvió al fallar (una etapa antes de elegir los clips, o el render de un video completo) se reserva otra vez y el pipeline lo liquida como al crear. Responde `cost_details` cuando reservó. El proyecto sigue `failed` hasta que la tarea lo reabre. Uno que falló con `IngestionStuck` (su ingesta no avanzaba y se devolvió la reserva) también reserva de nuevo y corre. Solo el mensaje de este pedido lo reabre: una copia vieja que llega tarde no corre nada.',
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
    { status: 400, when: 'Un campo del estilo inválido (por campo, como el PATCH del proyecto), o un clip más largo de lo que un render termina.' },
    { status: 402, when: 'Saldo insuficiente: `{ error, required }`. La UI abre D2.' },
    { status: 403, when: 'El plan no permite la resolución o la suma de segundos (`code: "resolution"` / `"duration"`). La UI abre D3.' },
    {
      status: 409,
      when: '`{ error, code }`: el proyecto está procesando o renderizando (`project_processing`, `project_rendering`, `clip_rendering`), espera la revisión (`awaiting_approval`), tiene un render sin liquidar (`render_unsettled`), un clip con publicaciones pendientes (`posts_pending`) o no tiene clips (`no_clips`).',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_clips.py · ProjectRerenderView',
    note: 'Un video completo es su único clip; en un proyecto de clips, los que se entregaron o fallaron y los `draft` que un render pagó y nunca entregó (nunca un sugerido de la revisión). Reserva `render_cost` de la suma de sus segundos y guarda el estilo en la misma transacción. `render_resolution` no cambia (se cobró al crear). Se cobra lo entregado (CLIP_RENDER).',
  },
  screens: ['D1', 'D4', 'D5'],
});

export const getProjectActivity = defineEndpoint({
  id: 'projects.activity',
  method: 'GET',
  path: '/api/v1/projects/:id/activity/',
  summary: 'Quién hizo qué en el proyecto y sus movimientos de tokens, del más nuevo al más viejo.',
  response: z.array(ActivityItem),
  errors: [{ status: 404, when: 'No existe o es de otro workspace.' }],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_project_data.py · ProjectActivityView + services/project_activity.py · build_activity',
    note: 'No hay un registro de eventos: se arma con lo guardado. Creación (`created_at`, `uploaded_by`), prompt (`metadata.pte` y la hora de su AIUsageRecord), revisión (AIUsageRecord de la selección; dice los clips que se sugirieron, `ai_rationale_log.suggestions_count`, no los que quedaron después de aprobar), aprobación (su reserva y quién la hizo), reservas, cobros y devoluciones (todas las Transaction del proyecto: las que tienen `project` y, sin proyecto, las que nombra su metadata en las claves `*reservation_id` y en `replaced_reservation_ids`, siempre de la billetera de su workspace y nunca las de otro proyecto; con `settled_at` y `reserved_amount` desde la migración 0012 de payments; la reserva, su cobro y su devolución traen `reservation_state`, cómo terminó esa reserva: son ítems de un mismo movimiento del saldo, no cobros ni ingresos aparte; con todas las reservas, los montos con signo suman lo que el proyecto movió el saldo; el cobro se llama como su reserva, «Cobro por reintentar «Clip»», «Cobro por renderizar «Clip»», «Cobro por renderizar todo de nuevo», y «Cómo se calculó» muestra esa `description`), fin del render (un ítem por cada render que cerró, cuando se liquidó la reserva que lo pagó, con el id de esa reserva: el render del proyecto dice cuántos clips dejó, el de un solo clip lo nombra, «Terminó el render de «Clip»»; lo que dice sale de cómo terminó ese render, `metadata.render_outcomes`, así que el «Terminó el render» de la aprobación sigue ahí cuando un render posterior falla; si su reserva terminó devuelta entera, `description` no dice «Terminó el render» sino que no se pudo renderizar, lo que se devolvió y, cuando el backend restauró lo que había, «quedó el clip anterior»), falla (`pipeline_error_at`) y publicaciones (ScheduledPost, sin quién las programó: `actor` null). No aparecen porque no se guardan: `project_renamed`, `clip_edited` y `clip_retried`, ni una reserva de antes de la migración 0015 de payments que no quedó vinculada (el render automático ya liquidado de un proyecto que después se aprobó). Lo ven todos los roles. Máximo 200 ítems: entran primero los movimientos de tokens, cada reserva entera, y el resto se completa con los ítems más nuevos; los otros ítems guardan 20 lugares para los más nuevos (una falla, un post).',
  },
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
  errors: [
    { status: 400, when: '`clip_ids` no es una lista de ids, o trae más de 50.' },
    { status: 404, when: 'No existe o es de otro workspace.' },
    { status: 409, when: 'Ningún clip está listo todavía.' },
    {
      status: 422,
      when: 'Supera el tope del .zip: `code: "zip_too_large"` (`limit` en bytes, 1 GB por defecto) o `code: "zip_too_many_clips"` (`limit` en clips, 50). La UI muestra el mensaje del servidor: elegir menos o bajarlos de a uno.',
    },
    { status: 429, when: 'Ya se está armando otro .zip en el mismo workspace (un lock de Redis con dueño, entre todos los workers).' },
    { status: 503, when: 'Error del almacenamiento (`code: "storage_unavailable"`) o el armado, subida incluida, pasó el tiempo máximo (`code: "zip_timeout"`, 140 s por defecto).' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_project_data.py · ProjectClipsDownloadView + services/clip_download.py · build_project_zip',
    note: 'Se arma mientras espera el pedido: lee los tamaños antes de bajar nada, copia cada clip del almacenamiento al .zip por partes (sin comprimir: son MP4), lo guarda en `exports/zips/` y devuelve la URL firmada por 1 h, con `Content-Disposition: attachment`. Borra siempre la carpeta temporal, y cada pedido borra los .zip vencidos. Entran los clips `completed` y `published` con archivo; los ids de `clip_ids` que no son de clips listos del proyecto (de otro proyecto o workspace) se ignoran. Lo puede pedir cualquier rol (`clip.download`).',
  },
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
    {
      status: 400,
      when: 'El link no es una URL web (`code: "invalid"`), no es de un sitio soportado, es una IP o una dirección interna, o es una lista, un canal o un vivo (`code: "unsupported"`), o no se puede bajar: privado, borrado o con restricción (`code: "unavailable"`), o dura más que el máximo de la app, 3 h, con cualquier plan: ningún plan lo aceptaría (`code: "duration_app"`, `limit` en segundos, 10800). Decide la duración del video, no qué tope es menor: con Gratis, un video de 4 h también es `duration_app`. La UI lo dice junto al campo, sin ofrecer planes. Sin `video_url`: `{ video_url: [...] }`.',
    },
    { status: 403, when: 'La duración supera el plan pero no las 3 h de la app: otro plan la permite (`code: "duration"`, `limit` en segundos). La UI lo muestra junto al campo, con link a Planes. También el Lector (`{ detail }`): revisar un link es parte de crear.' },
    { status: 404, when: 'El workspace de `X-Workspace-Id` no es del usuario.' },
    { status: 429, when: 'Más de 20 links por minuto del mismo usuario.' },
    { status: 502, when: 'El sitio del video respondió con un error o no se encontró su dirección (`code: "provider_error"`).' },
    { status: 503, when: 'La herramienta que lee los links no está disponible (`code: "unavailable_tool"`).' },
    { status: 504, when: 'El sitio tardó más de 25 s (`code: "timeout"`).' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_project_data.py · ProbeLinkView + services/link_probe.py · probe_link',
    note: 'Hosts admitidos, exactos: youtube.com (y www, m, music), youtu.be, vimeo.com (y www, player), twitch.tv (y www, m, clips, player) y drive.google.com (`LINK_HOSTS` de services/link_validation.py, los mismos que crear un proyecto y la descarga del worker). Antes de consultar nada rechaza otros esquemas, IPs, otros subdominios, usuario en la URL, puertos raros, hosts que resuelven a direcciones privadas y links que no nombran un solo video. yt-dlp lee solo los datos con los extractores de esos sitios (services/ytdlp_safe.py), sin pedidos ni redirecciones a direcciones no públicas, en un proceso aparte con entorno mínimo que se corta entero a los 25 s. `video_url` vuelve como la URL del video (`watch?v=<id>` en YouTube) y es la que hay que mandar al crear. Crear no exige el probe: la duración del plan vale igual en la descarga, y un video más largo hace fallar el proyecto con `LinkTooLongError` y la devolución de la reserva. Si además pasa las 3 h que procesa la app con cualquier plan (`AI_UPLOAD_MAX_DURATION_SECONDS`), el código es `LinkTooLongForAppError`: no es culpa del plan. El probe decide con la misma regla (`link_too_long_error` de tasks/shared.py), por la duración del video: más de 3 h es 400 `duration_app` con cualquier plan; dentro de las 3 h y más que el plan, 403 `duration`. Un link leído se guarda 10 min en la caché.',
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
