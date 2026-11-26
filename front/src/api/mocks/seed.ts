import type { Plan } from '../contracts/billing';
import type { BrandKit } from '../contracts/brandkits';
import type { MemberRole, Platform, RenderResolution } from '../contracts/common';
import { KNOWN_PIPELINE_ERROR_CODES, type PipelineStage, type PostStatus, type ProjectStatus, type StageStatus, type TranscriptItem } from '../contracts/projects';
import {
  DB_VERSION,
  KIND,
  mid,
  PLATFORMS,
  type MockActivity,
  type MockClip,
  type MockDb,
  type MockMember,
  type MockNotification,
  type MockPost,
  type MockProject,
  type MockSocialAccount,
  type MockTransaction,
  type MockUser,
} from './db';

/* ── Reference data ─────────────────────────────────────────────────────── */

export const PLAN_IDS = { free: mid(KIND.plan, 1), creator: mid(KIND.plan, 2), pro: mid(KIND.plan, 3), studio: mid(KIND.plan, 4) } as const;
export type PlanKey = keyof typeof PLAN_IDS;

/** The four plans of artboards A6/G3. */
export function seedPlans(): Plan[] {
  const base = { has_thumbnail_engine: false, is_active: true };
  return [
    {
      ...base,
      id: PLAN_IDS.free,
      name: 'Gratis',
      max_video_duration_seconds: 600,
      max_resolution: '720p',
      has_watermark: true,
      allow_scheduling: false,
      allow_crossposting: false,
      has_seo_optimization: false,
      base_discount_rate: 0,
      max_members: 2,
      price_usd_monthly: 0,
      description: 'Para probar con videos cortos.',
      sort_order: 1,
      is_recommended: false,
    },
    {
      ...base,
      id: PLAN_IDS.creator,
      name: 'Creador',
      max_video_duration_seconds: 3600,
      max_resolution: '1080p',
      has_watermark: false,
      allow_scheduling: true,
      allow_crossposting: false,
      has_seo_optimization: true,
      base_discount_rate: 0.05,
      max_members: 5,
      price_usd_monthly: 19,
      description: 'Para publicar seguido en una red.',
      sort_order: 2,
      is_recommended: false,
    },
    {
      ...base,
      id: PLAN_IDS.pro,
      name: 'Pro',
      max_video_duration_seconds: 10800,
      max_resolution: '4K',
      has_watermark: false,
      allow_scheduling: true,
      allow_crossposting: true,
      has_seo_optimization: true,
      base_discount_rate: 0.1,
      max_members: 5,
      price_usd_monthly: 49,
      description: 'Para equipos que publican en todas las redes.',
      sort_order: 3,
      is_recommended: true,
    },
    {
      ...base,
      id: PLAN_IDS.studio,
      name: 'Estudio',
      max_video_duration_seconds: 10800,
      max_resolution: '4K',
      has_watermark: false,
      allow_scheduling: true,
      allow_crossposting: true,
      has_seo_optimization: true,
      base_discount_rate: 0.2,
      max_members: 15,
      price_usd_monthly: 129,
      description: 'Para estudios con videos largos y más gente.',
      sort_order: 4,
      is_recommended: false,
    },
  ];
}

/** Values of PricingEngine and of the upload limits (see contracts/billing.ts · Pricing). */
export const PRICING = {
  base_cost_per_second_720p: 0.5,
  resolution_multipliers: { '720p': 1, '1080p': 1.5, '4K': 3 },
  feature_costs: { seo_optimization: 5, ai_thumbnail: 15 },
  link_flat_cost: 10,
  min_cost: 0.01,
  token_price_usd: 0.01,
  single_edit_max_duration_seconds: 600,
  upload_max_duration_seconds: 10800,
  upload_max_size_bytes: 500 * 1024 * 1024,
  upload_extensions: ['.mp4', '.mov', '.avi'],
  selection_criteria_max_chars: 300,
  prompt_max_chars: 1000,
} as const;

export const TOKEN_PACKAGES = {
  packages: [
    { id: 'pack-1000', tokens: 1000, price_usd: 10, discount_rate: 0 },
    { id: 'pack-5000', tokens: 5000, price_usd: 45, discount_rate: 0.1 },
    { id: 'pack-15000', tokens: 15000, price_usd: 120, discount_rate: 0.2 },
  ],
  custom_price_per_token_usd: 0.01,
  custom_min_tokens: 100,
  local_currency: 'ARS',
  usd_to_local_rate: 1450,
} as const;

export const USER_IDS = {
  lucia: mid(KIND.user, 1),
  martin: mid(KIND.user, 2),
  carla: mid(KIND.user, 3),
  diego: mid(KIND.user, 4),
  nuevo: mid(KIND.user, 5),
} as const;

export const WORKSPACE_IDS = { estudio: mid(KIND.workspace, 1), canal: mid(KIND.workspace, 2), nuevo: mid(KIND.workspace, 3) } as const;

function round2(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

/** Same formula as the backend, for consistent seeded costs. */
export function priceOf(durationSeconds: number, resolution: RenderResolution, useSeo: boolean, discountRate: number): number {
  const base = Math.floor(durationSeconds) * PRICING.base_cost_per_second_720p * PRICING.resolution_multipliers[resolution];
  const total = (base + (useSeo ? PRICING.feature_costs.seo_optimization : 0)) * (1 - discountRate);
  return Math.max(PRICING.min_cost, round2(total));
}

/* ── Text pools ─────────────────────────────────────────────────────────── */

const SENTENCES = [
  'Hoy quiero contarte algo que me cambió la forma de trabajar.',
  'La mayoría de la gente arranca por el final y por eso se frustra.',
  'Si no medís lo que hacés, no sabés si estás mejorando.',
  'Lo primero es entender a quién le estás hablando.',
  'Nadie compra un producto, compra el resultado que le prometés.',
  'Cuando bajé el precio, vendí menos. Parece raro, pero pasó.',
  'El error más común es querer convencer en lugar de escuchar.',
  'Probé tres formatos distintos y uno solo funcionó.',
  'No hace falta tener el mejor equipo para empezar.',
  'Lo que sí hace falta es publicar todas las semanas.',
  'Mirá este número, porque es el que explica todo.',
  'Te lo muestro con un ejemplo real de la semana pasada.',
  'Al principio nadie lo veía, y después explotó.',
  'La clave está en los primeros tres segundos.',
  'Si la persona no se queda, el resto no importa.',
  'Esto no lo vas a encontrar en ningún curso.',
  'Hicimos la prueba con cien clientes y el resultado fue claro.',
  'Vamos paso por paso, que es más fácil de lo que parece.',
  'Y acá viene la parte que casi nadie cuenta.',
  'Con esto cerramos, pero antes una última idea.',
];

const CLIP_TITLES = [
  'El error que todos cometen al vender',
  'Por qué bajar el precio te hace vender menos',
  'Los primeros tres segundos lo deciden todo',
  'Nadie compra un producto: compra un resultado',
  'Lo que aprendí después de cien clientes',
  'Dejá de convencer y empezá a escuchar',
  'El formato que sí funcionó',
  'No necesitás el mejor equipo para empezar',
  'El número que explica todo',
  'Publicar todas las semanas cambia el juego',
  'La parte que casi nadie cuenta',
  'Cómo arrancar sin frustrarte',
];

const REASONS = [
  'Gancho fuerte en los primeros segundos · Idea completa · Cierre claro. Arranca con una afirmación que contradice lo esperado y la resuelve sin depender del contexto anterior.',
  'Historia personal · Dato concreto · Buen ritmo. Cuenta un caso real con un número que se recuerda y termina en una frase que se puede citar.',
  'Pregunta directa al espectador · Tensión · Remate. Plantea un problema que el público reconoce y lo responde antes del corte.',
  'Consejo accionable · Lenguaje simple. Explica un paso que se puede aplicar hoy, aunque el arranque es un poco lento.',
  'Tema popular · Tono cercano. El contenido interesa, pero la idea queda a medio cerrar y depende de lo que se dijo antes.',
];

/** Deterministic pseudo-random generator (mulberry32). */
export function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Word-level transcript (`start`, `end`, `text`), like `_stage_transcribe` stores it.
 * Generated on demand from a seed so the mock database stays small.
 */
export function generateTranscript(seed: number, durationSeconds: number): TranscriptItem[] {
  const random = rng(seed);
  const items: TranscriptItem[] = [];
  let time = 0.4;
  let guard = 0;
  while (time < durationSeconds - 1 && guard < 40_000) {
    const sentence = SENTENCES[Math.floor(random() * SENTENCES.length)] ?? SENTENCES[0] ?? '';
    for (const word of sentence.split(' ')) {
      const length = 0.18 + word.length * 0.045;
      if (time + length >= durationSeconds) return items;
      items.push({ start: round2(time), end: round2(time + length), text: word });
      time += length + 0.04;
      guard += 1;
    }
    time += 0.35 + random() * 0.9;
  }
  return items;
}

/* ── Builders ───────────────────────────────────────────────────────────── */

const DEFAULT_PREFERENCES = { project_ready: true, render_finished: true, project_failed: true, post_failed: true, low_balance: true };

function user(id: string, name: string, email: string, overrides: Partial<MockUser> = {}): MockUser {
  return {
    id,
    name,
    email,
    is_email_verified: true,
    tour_completed: true,
    terms_accepted_at: '2026-09-01T12:00:00Z',
    pending_email: null,
    onboarding_use_case: 'podcast',
    active_workspace_id: null,
    notification_preferences: { ...DEFAULT_PREFERENCES },
    ...overrides,
  };
}

const iso = (ms: number) => new Date(ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

const INGEST_ORDER: PipelineStage[] = ['uploaded', 'audio_extracted', 'transcribed', 'moderated', 'clips_selected'];

/** `pipeline_stage_status` for a project whose last finished stage is `stage`. */
export function stageStatusFor(stage: PipelineStage, running: StageStatus | null): Record<string, StageStatus> {
  const map: Record<string, StageStatus> = {};
  const index = INGEST_ORDER.indexOf(stage);
  const done = index === -1 ? INGEST_ORDER.length - 1 : index;
  INGEST_ORDER.forEach((name, position) => {
    if (position <= done) map[name] = 'completed';
    else if (position === done + 1 && running) map[name] = running;
    else map[name] = 'pending';
  });
  return map;
}

export interface ClipSpec {
  count: number;
  /** Status of each clip, in order; the last value repeats. */
  statuses: Array<MockClip['status']>;
}

export function buildClips(projectId: string, projectIndex: number, durationSeconds: number, spec: ClipSpec, createdAt: number): MockClip[] {
  const random = rng(projectIndex * 7919 + 13);
  const slot = durationSeconds / (spec.count + 1);
  return Array.from({ length: spec.count }, (_, index) => {
    const status = spec.statuses[Math.min(index, spec.statuses.length - 1)] ?? 'draft';
    const length = Math.min(Math.max(18, Math.round(20 + random() * 38)), Math.max(8, Math.floor(slot)));
    const start = round2(Math.max(0, slot * (index + 0.5) + random() * 6));
    const id = mid(KIND.clip, projectIndex * 100 + index + 1);
    const rendered = status === 'completed' || status === 'published';
    return {
      id,
      project: projectId,
      title: CLIP_TITLES[(projectIndex * 3 + index) % CLIP_TITLES.length] ?? `Clip ${index + 1}`,
      s3_object_key: rendered ? `clips/${projectId}/${id}.mp4` : null,
      start_time: start,
      end_time: round2(Math.min(durationSeconds, start + length)),
      virality_score: Math.max(41, 94 - index * 5 - Math.floor(random() * 4)),
      ai_reasoning: REASONS[Math.min(REASONS.length - 1, Math.floor(index / 2))] ?? '',
      status,
      created_at: iso(createdAt),
      layers: [],
      preview_url: null,
      thumbnail_url: null,
      error_code: status === 'failed' ? 'RenderError' : null,
      posts: [],
    };
  });
}

export interface ProjectSpec {
  index: number;
  title: string;
  status: ProjectStatus;
  stage: PipelineStage;
  /** Status of the stage after `stage` while ingesting. */
  running?: StageStatus | null;
  errorCode?: string;
  durationSeconds: number;
  uploadedBy: string;
  ageMs: number;
  source?: 'file' | 'link';
  projectType?: 'repurpose' | 'single_edit';
  resolution?: RenderResolution;
  useSeo?: boolean;
  clips?: ClipSpec;
  workspace?: string;
  discountRate?: number;
  overrides?: Partial<MockProject>;
  /** Extra `metadata` keys, merged over the built ones (e.g. the auto-render marks). */
  metadata?: Partial<MockProject['metadata']>;
  costState?: 'reserved' | 'charged' | 'refunded';
  prompt?: { text: string; status: 'applied' | 'failed' };
  /** "Renderizar sin revisar" could not be paid: the project waits in the review. */
  autoRenderSkipped?: 'insufficient_funds' | 'plan_limit';
  noSpeech?: boolean;
}

const USER_NAMES: Record<string, string> = {
  [USER_IDS.lucia]: 'Lucía Fernández',
  [USER_IDS.martin]: 'Martín Sosa',
  [USER_IDS.carla]: 'Carla Ruiz',
  [USER_IDS.diego]: 'Diego Paz',
  [USER_IDS.nuevo]: 'Sofía Méndez',
};

export function buildProject(spec: ProjectSpec, now: number, brandKitId: string | null): MockProject {
  const id = mid(KIND.project, spec.index);
  const createdAt = now - spec.ageMs;
  const resolution = spec.resolution ?? '720p';
  const source = spec.source ?? 'file';
  const discount = spec.discountRate ?? 0.1;
  const amount = source === 'link' ? PRICING.link_flat_cost : priceOf(spec.durationSeconds, resolution, Boolean(spec.useSeo), discount);
  const transcribed = ['transcribed', 'moderated', 'clips_selected', 'render_dispatched', 'completed', 'partial'].includes(spec.stage) &&
    !(spec.status === 'failed' && spec.errorCode === 'AINoSpeechDetectedError');
  let clips = spec.clips ? buildClips(id, spec.index, spec.durationSeconds, spec.clips, createdAt + 8 * MIN) : [];
  if (spec.projectType === 'single_edit') {
    // The whole video is one clip: it spans the source, has the project's title and no score.
    clips = clips.slice(0, 1).map((clip) => ({ ...clip, title: spec.title, start_time: 0, end_time: spec.durationSeconds, virality_score: 0, ai_reasoning: '' }));
  }
  const costState = spec.costState ?? (spec.status === 'failed' ? 'refunded' : spec.status === 'completed' || spec.status === 'partial' ? 'charged' : 'reserved');
  const timings: MockProject['stage_timings'] = {};
  const doneIndex = INGEST_ORDER.indexOf(spec.stage) === -1 ? INGEST_ORDER.length - 1 : INGEST_ORDER.indexOf(spec.stage);
  const stageDurations = [42, 195, 38, 71, 12];
  let cursor = createdAt + 20_000;
  INGEST_ORDER.forEach((name, position) => {
    const seconds = stageDurations[position] ?? 30;
    if (position <= doneIndex && spec.status !== 'uploaded') {
      timings[name] = { started_at: iso(cursor), finished_at: iso(cursor + seconds * 1000), duration_s: seconds };
      cursor += seconds * 1000;
    } else if (position === doneIndex + 1 && spec.status === 'ingesting') {
      timings[name] = { started_at: spec.running === 'pending_capacity' ? null : iso(cursor), finished_at: null, duration_s: null };
    }
  });
  const pte = spec.prompt
    ? spec.prompt.status === 'applied'
      ? {
          status: 'applied' as const,
          applied: { render_layout: 'blurred', max_clips: 5, selection_criteria: 'las partes más graciosas', subtitle_color: '#FFFF00' },
          ignored: [
            { field: 'render_resolution', reason: 'plan' },
            { field: 'aspect_ratio', reason: 'explicit' },
          ],
        }
      : { status: 'failed' as const, applied: {}, ignored: [], error_code: 'CapacityTimeoutError' }
    : undefined;
  return {
    id,
    workspace: spec.workspace ?? WORKSPACE_IDS.estudio,
    uploaded_by: spec.uploadedBy,
    title: spec.title,
    original_r2_key: source === 'file' ? `videos/raw/${id}.mp4` : null,
    proxy_r2_key: transcribed ? `videos/proxy/${id}.mp4` : null,
    final_export_r2_key: null,
    project_type: spec.projectType ?? 'repurpose',
    editing_style: 'dynamic',
    intelligence_level: 'fast',
    auto_render_bypass: spec.projectType === 'single_edit',
    brand_kit: brandKitId,
    ...(brandKitId ? { brand_kit_name: 'Estudio Norte' } : {}),
    aspect_ratio: '9:16',
    render_layout: 'fill',
    gameplay_position: 'center',
    camera_selection_position: 'center',
    manual_camera_x: 0.5,
    manual_camera_y: 0.2,
    manual_camera_zoom: 1,
    pip_corner: 'bottom-right',
    trending_color: '#FF2D55',
    trending_text: '',
    speaker_tracking: false,
    add_subtitles: true,
    subtitle_color: '#F2C94C',
    subtitle_with_emojis: false,
    subtitle_words_per_segment: 3,
    subtitle_size: 'medium',
    subtitle_position: 'bottom',
    use_facetracking: true,
    remove_silences: false,
    source_file: source === 'file' ? `/media/videos/raw/2026/11/${id}.mp4` : null,
    video_url: source === 'link' ? 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' : null,
    metadata: {
      max_clips: spec.clips?.count ?? 8,
      render_resolution: resolution,
      use_seo: Boolean(spec.useSeo),
      ...(source === 'file'
        ? { source_media: { duration_s: spec.durationSeconds, width: 1920, height: 1080, mime: 'video/mp4' }, priced_duration_s: spec.durationSeconds }
        : { duration: spec.durationSeconds }),
      reservation_id: mid(KIND.transaction, spec.index * 10 + 1),
      ...(spec.prompt ? { pte_prompt: spec.prompt.text, pte_explicit: ['aspect_ratio'] } : {}),
      ...(pte ? { pte } : {}),
      ...(spec.autoRenderSkipped ? { auto_render_skipped: spec.autoRenderSkipped } : {}),
      ...(spec.noSpeech ? { no_speech: true, full_text: '' } : {}),
      ...spec.metadata,
    },
    ai_rationale_log: clips.length > 0 ? { suggestions_count: clips.length } : {},
    approved_segments:
      spec.status === 'rendering' || spec.status === 'completed' || spec.status === 'partial'
        ? clips.map((clip) => ({ start: clip.start_time, end: clip.end_time }))
        : null,
    status: spec.status,
    pipeline_stage: spec.stage,
    pipeline_stage_status:
      spec.status === 'failed'
        ? { ...stageStatusFor(spec.stage, 'failed') }
        : stageStatusFor(spec.stage, spec.status === 'ingesting' ? (spec.running ?? 'running') : null),
    pipeline_attempts: spec.status === 'uploaded' ? 0 : 1,
    pipeline_error_code: spec.errorCode ?? '',
    pipeline_error_at: spec.errorCode ? iso(createdAt + 6 * MIN) : null,
    created_at: iso(createdAt),
    clips,
    proxy_url: null,
    uploaded_by_detail: { id: spec.uploadedBy, name: USER_NAMES[spec.uploadedBy] ?? 'Alguien' },
    thumbnail_url: null,
    duration_seconds: spec.durationSeconds,
    cost: { amount, state: costState, transaction_id: mid(KIND.transaction, spec.index * 10 + 1) },
    stage_timings: timings,
    eta_seconds: spec.status === 'ingesting' || spec.status === 'uploaded' ? Math.max(60, Math.round(spec.durationSeconds / 8)) : null,
    _transcriptSeed: transcribed ? spec.index * 101 + 7 : null,
    _reservations: [mid(KIND.transaction, spec.index * 10 + 1)],
    ...spec.overrides,
  };
}

function transactionFor(project: MockProject, index: number, walletId: string): MockTransaction {
  const cost = project.cost ?? { amount: 0, state: 'reserved' as const, transaction_id: null };
  const refunded = cost.state === 'refunded';
  return {
    id: mid(KIND.transaction, index * 10 + 1),
    workspace: project.workspace,
    wallet: walletId,
    created_by: project.uploaded_by,
    amount: cost.amount,
    transaction_type: 'SPEND',
    status: refunded ? 'FAILED' : cost.state === 'charged' ? 'COMPLETED' : 'RESERVED',
    description: `Procesamiento: ${project.title}`,
    created_at: project.created_at,
    kind: refunded ? 'released' : cost.state === 'charged' ? 'charged' : 'reserved',
    created_by_detail: project.uploaded_by_detail ?? null,
    project: { id: project.id, title: project.title },
    category: 'project',
  };
}

export function socialAccounts(workspace: string, states: Partial<Record<Platform, 'connected' | 'expired' | 'disconnected'>>, now: number): MockSocialAccount[] {
  const limits: Record<Platform, MockSocialAccount['limits']> = {
    TIKTOK: { caption_max_chars: 2200, hashtags_max: 30, max_duration_seconds: 600, aspect_ratios: ['9:16'] },
    INSTAGRAM_REELS: { caption_max_chars: 2200, hashtags_max: 30, max_duration_seconds: 90, aspect_ratios: ['9:16'] },
    YOUTUBE_SHORTS: { caption_max_chars: 100, hashtags_max: 15, max_duration_seconds: 60, aspect_ratios: ['9:16'] },
  };
  return PLATFORMS.map((platform) => {
    const status = states[platform] ?? 'disconnected';
    return {
      workspace,
      platform,
      status,
      username: status === 'disconnected' ? null : '@estudionorte',
      connected_at: status === 'disconnected' ? null : iso(now - 40 * DAY),
      limits: limits[platform],
      tiktok:
        platform === 'TIKTOK' && status !== 'disconnected'
          ? {
              privacy_options: ['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'SELF_ONLY'],
              comment_disabled: false,
              duet_disabled: true,
              stitch_disabled: false,
            }
          : null,
    };
  });
}

export interface SeedOptions {
  scenario: string;
  /** Who is signed in. Null: logged out. */
  as?: keyof typeof USER_IDS | null;
  plan?: PlanKey;
  balance?: number;
  /** Which projects the main workspace has. */
  projects?: 'default' | 'none' | 'all-states' | 'many';
  brandKits?: boolean;
  social?: Partial<Record<Platform, 'connected' | 'expired' | 'disconnected'>>;
  /** Adds a pending invitation so the plan's seats are full (H2). */
  seatsFull?: boolean;
  flags?: Partial<MockDb['flags']>;
}

function defaultProjects(discountRate: number): ProjectSpec[] {
  const d = discountRate;
  return [
    { index: 1, title: 'Clase 3: Finanzas personales', status: 'ingesting', stage: 'audio_extracted', durationSeconds: 3122, uploadedBy: USER_IDS.lucia, ageMs: 12 * MIN, discountRate: d },
    { index: 2, title: 'Entrevista a Paula Gómez', status: 'ingesting', stage: 'uploaded', running: 'pending_capacity', durationSeconds: 2695, uploadedBy: USER_IDS.carla, ageMs: 21 * MIN, discountRate: d },
    {
      index: 3,
      title: 'Podcast Ep. 42 — Cómo vender sin vender',
      status: 'awaiting_approval',
      stage: 'clips_selected',
      durationSeconds: 4360,
      uploadedBy: USER_IDS.lucia,
      ageMs: 28 * MIN,
      useSeo: true,
      clips: { count: 9, statuses: ['draft'] },
      discountRate: d,
    },
    {
      index: 4,
      title: 'Stream Valorant del viernes',
      status: 'rendering',
      stage: 'render_dispatched',
      durationSeconds: 10090,
      uploadedBy: USER_IDS.martin,
      ageMs: 40 * MIN,
      source: 'link',
      clips: { count: 8, statuses: ['completed', 'completed', 'completed', 'completed', 'rendering'] },
      discountRate: d,
      overrides: { render_layout: 'split', editing_style: 'dynamic' },
    },
    {
      index: 5,
      title: 'Vlog Mendoza',
      status: 'completed',
      stage: 'completed',
      durationSeconds: 1124,
      uploadedBy: USER_IDS.carla,
      ageMs: 1 * DAY + 3 * HOUR,
      resolution: '1080p',
      useSeo: true,
      clips: { count: 6, statuses: ['completed'] },
      discountRate: d,
      overrides: { editing_style: 'vlog' },
    },
    {
      index: 6,
      title: 'Webinar de lanzamiento',
      status: 'failed',
      stage: 'audio_extracted',
      errorCode: 'AINoSpeechDetectedError',
      durationSeconds: 3900,
      uploadedBy: USER_IDS.lucia,
      ageMs: 3 * DAY,
      discountRate: d,
    },
    {
      index: 7,
      title: 'Tutorial de Notion en 8 minutos',
      status: 'completed',
      stage: 'completed',
      durationSeconds: 492,
      uploadedBy: USER_IDS.martin,
      ageMs: 4 * DAY,
      projectType: 'single_edit',
      resolution: '1080p',
      clips: { count: 1, statuses: ['completed'] },
      discountRate: d,
      overrides: { editing_style: 'minimalist' },
    },
    {
      index: 8,
      title: 'Charla en la UTN: emprender sin capital',
      status: 'partial',
      stage: 'partial',
      durationSeconds: 2410,
      uploadedBy: USER_IDS.lucia,
      ageMs: 6 * DAY,
      clips: { count: 6, statuses: ['completed', 'completed', 'completed', 'completed', 'completed', 'failed'] },
      discountRate: d,
      prompt: { text: 'Sacá 5 clips de 30 s de las partes más graciosas, subtítulos grandes amarillos, fondo borroso en 4K', status: 'applied' },
    },
  ];
}

function allStateProjects(discountRate: number): ProjectSpec[] {
  const d = discountRate;
  const base = defaultProjects(d);
  let index = 20;
  const next = () => {
    index += 1;
    return index;
  };
  const extra: ProjectSpec[] = [
    { index: next(), title: 'En cola: recién subido', status: 'uploaded', stage: 'uploaded', running: null, durationSeconds: 900, uploadedBy: USER_IDS.lucia, ageMs: 1 * MIN, discountRate: d },
    { index: next(), title: 'Paso 1: preparando el video', status: 'ingesting', stage: 'uploaded', durationSeconds: 1500, uploadedBy: USER_IDS.lucia, ageMs: 2 * MIN, discountRate: d },
    { index: next(), title: 'Paso 3: revisando el contenido', status: 'ingesting', stage: 'transcribed', durationSeconds: 1800, uploadedBy: USER_IDS.martin, ageMs: 9 * MIN, discountRate: d },
    { index: next(), title: 'Paso 4: eligiendo los mejores momentos', status: 'ingesting', stage: 'moderated', durationSeconds: 2100, uploadedBy: USER_IDS.carla, ageMs: 11 * MIN, discountRate: d },
    { index: next(), title: 'En espera en la transcripción', status: 'ingesting', stage: 'audio_extracted', running: 'pending_capacity', durationSeconds: 2400, uploadedBy: USER_IDS.lucia, ageMs: 25 * MIN, discountRate: d },
    { index: next(), title: 'Despachando el render', status: 'ingesting', stage: 'clips_selected', running: null, durationSeconds: 1300, uploadedBy: USER_IDS.lucia, ageMs: 14 * MIN, clips: { count: 5, statuses: ['rendering'] }, discountRate: d, overrides: { auto_render_bypass: true } },
    { index: next(), title: 'Revisión sin sugerencias', status: 'awaiting_approval', stage: 'clips_selected', durationSeconds: 1500, uploadedBy: USER_IDS.lucia, ageMs: 2 * HOUR, clips: { count: 0, statuses: [] }, discountRate: d },
    { index: next(), title: 'Video completo renderizando', status: 'rendering', stage: 'render_dispatched', durationSeconds: 380, uploadedBy: USER_IDS.carla, ageMs: 6 * MIN, projectType: 'single_edit', clips: { count: 1, statuses: ['rendering'] }, discountRate: d },
    { index: next(), title: 'Video completo que no se pudo generar', status: 'partial', stage: 'partial', durationSeconds: 410, uploadedBy: USER_IDS.carla, ageMs: 2 * DAY, projectType: 'single_edit', clips: { count: 1, statuses: ['failed'] }, discountRate: d, costState: 'refunded' },
    { index: next(), title: 'Sin revisar: no alcanzó el saldo', status: 'awaiting_approval', stage: 'clips_selected', durationSeconds: 1900, uploadedBy: USER_IDS.lucia, ageMs: 50 * MIN, clips: { count: 6, statuses: ['draft'] }, discountRate: d, overrides: { auto_render_bypass: true }, autoRenderSkipped: 'insufficient_funds' },
    { index: next(), title: 'Sin revisar: el plan no lo permite', status: 'awaiting_approval', stage: 'clips_selected', durationSeconds: 2000, uploadedBy: USER_IDS.martin, ageMs: 70 * MIN, clips: { count: 5, statuses: ['draft'] }, discountRate: d, overrides: { auto_render_bypass: true }, autoRenderSkipped: 'plan_limit' },
    { index: next(), title: 'Video sin voz, editado sin subtítulos', status: 'completed', stage: 'completed', durationSeconds: 300, uploadedBy: USER_IDS.carla, ageMs: 9 * DAY, projectType: 'single_edit', clips: { count: 1, statuses: ['completed'] }, discountRate: d, noSpeech: true },
    { index: next(), title: 'Prompt que no se pudo interpretar', status: 'awaiting_approval', stage: 'clips_selected', durationSeconds: 1700, uploadedBy: USER_IDS.martin, ageMs: 5 * HOUR, clips: { count: 4, statuses: ['draft'] }, discountRate: d, prompt: { text: 'Hacé algo que se vea bien', status: 'failed' } },
  ];
  const failures: ProjectSpec[] = KNOWN_PIPELINE_ERROR_CODES.map((code, position) => ({
    index: next(),
    title: `Falló: ${code}`,
    status: 'failed',
    stage: code === 'AutoRenderNotPaid' ? 'render_dispatched' : code === 'AIEmptySelectionError' ? 'moderated' : code.includes('Moderation') || code === 'UnsafeContentError' ? 'transcribed' : 'audio_extracted',
    errorCode: code,
    // Re-dispatched without tokens for what was left: the clips stay as they were and nothing new was charged.
    ...(code === 'AutoRenderNotPaid' ? { clips: { count: 4, statuses: ['completed', 'draft'] as Array<'completed' | 'draft'> }, costState: 'charged' as const, overrides: { auto_render_bypass: true } } : {}),
    durationSeconds: 1200 + position * 300,
    uploadedBy: USER_IDS.lucia,
    ageMs: (2 + position) * DAY,
    discountRate: d,
  }));
  failures.push({ index: next(), title: 'Falló: error desconocido', status: 'failed', stage: 'uploaded', errorCode: 'RuntimeError', durationSeconds: 800, uploadedBy: USER_IDS.lucia, ageMs: 12 * DAY, discountRate: d });
  // "Renderizar sin revisar" (map C1) with the render already reserved. The two reasons it stops at the
  // review instead are above. Added after the rest so the ids of the projects above do not move.
  const autoRender: ProjectSpec[] = [
    { index: next(), title: 'Sin revisar: render reservado', status: 'rendering', stage: 'render_dispatched', durationSeconds: 1400, uploadedBy: USER_IDS.lucia, ageMs: 16 * MIN, clips: { count: 5, statuses: ['completed', 'completed', 'rendering'] }, discountRate: d, overrides: { auto_render_bypass: true }, metadata: { auto_render_reservation_id: mid(KIND.transaction, 9100), auto_render_seconds: 172 } },
  ];
  return [...base, ...extra, ...failures, ...autoRender];
}

function manyProjects(discountRate: number): ProjectSpec[] {
  const base = defaultProjects(discountRate);
  const topics = ['Podcast', 'Clase', 'Entrevista', 'Stream', 'Vlog', 'Webinar', 'Charla', 'Tutorial'];
  const extra: ProjectSpec[] = Array.from({ length: 56 }, (_, position) => ({
    index: 100 + position,
    title: `${topics[position % topics.length]} ${position + 1}`,
    status: 'completed' as const,
    stage: 'completed' as const,
    durationSeconds: 600 + ((position * 137) % 3000),
    uploadedBy: [USER_IDS.lucia, USER_IDS.martin, USER_IDS.carla][position % 3] ?? USER_IDS.lucia,
    ageMs: (8 + position) * DAY,
    clips: { count: 3 + (position % 5), statuses: ['completed'] },
    discountRate,
  }));
  return [...base, ...extra];
}

/** Builds a whole mock database for a scenario. Dates are relative to `now`. */
export function seedDb(options: SeedOptions, now: number = Date.now()): MockDb {
  const plans = seedPlans();
  const planKey = options.plan ?? 'pro';
  const plan = plans.find((item) => item.id === PLAN_IDS[planKey]) ?? plans[2];
  const discount = plan?.base_discount_rate ?? 0;
  const as = options.as === undefined ? 'lucia' : options.as;
  const isNew = as === 'nuevo';

  const users: MockUser[] = [
    // Lucía has not finished the first-time tour: B2 shows «Primeros pasos» for her until she closes or completes it.
    // The other members have (scenarios rol-admin, rol-editor and rol-lector), so for them it stays hidden.
    user(USER_IDS.lucia, 'Lucía Fernández', 'lucia@estudionorte.com', { active_workspace_id: WORKSPACE_IDS.estudio, tour_completed: false }),
    user(USER_IDS.martin, 'Martín Sosa', 'martin@estudionorte.com', { active_workspace_id: WORKSPACE_IDS.estudio, onboarding_use_case: 'gaming' }),
    user(USER_IDS.carla, 'Carla Ruiz', 'carla@estudionorte.com', { active_workspace_id: WORKSPACE_IDS.estudio }),
    user(USER_IDS.diego, 'Diego Paz', 'diego@clientes.com', { active_workspace_id: WORKSPACE_IDS.estudio, onboarding_use_case: 'other' }),
    user(USER_IDS.nuevo, 'Sofía Méndez', 'sofia@ejemplo.com', {
      active_workspace_id: WORKSPACE_IDS.nuevo,
      tour_completed: options.scenario !== 'usuario-nuevo',
      onboarding_use_case: null,
      terms_accepted_at: iso(now - 5 * MIN),
    }),
  ];

  const workspaces = [
    { id: WORKSPACE_IDS.estudio, name: 'Estudio Norte', owner: USER_IDS.lucia, subscription_plan: PLAN_IDS[planKey], created_at: iso(now - 90 * DAY), renews_at: planKey === 'free' ? null : iso(now + 12 * DAY), pending_plan: null },
    { id: WORKSPACE_IDS.canal, name: 'Canal de Martín', owner: USER_IDS.martin, subscription_plan: PLAN_IDS.free, created_at: iso(now - 200 * DAY), renews_at: null, pending_plan: null },
    { id: WORKSPACE_IDS.nuevo, name: isNew && options.scenario === 'usuario-nuevo' ? 'Mi Espacio' : 'Estudio de Sofía', owner: USER_IDS.nuevo, subscription_plan: PLAN_IDS[isNew ? (options.plan ?? 'free') : 'free'], created_at: iso(now - 5 * MIN), renews_at: null, pending_plan: null },
  ];

  const member = (n: number, workspace: string, userId: string | null, email: string, role: MemberRole, status: 'pending' | 'active' = 'active', token: string | null = null): MockMember => ({
    id: mid(KIND.member, n),
    workspace,
    user: userId,
    email,
    role,
    status,
    joined_at: iso(now - (60 - n) * DAY),
    invited_by: userId === USER_IDS.lucia ? null : USER_IDS.lucia,
    token,
  });
  const members: MockMember[] = [
    member(1, WORKSPACE_IDS.estudio, USER_IDS.lucia, 'lucia@estudionorte.com', 'admin'),
    member(2, WORKSPACE_IDS.estudio, USER_IDS.martin, 'martin@estudionorte.com', 'admin'),
    member(3, WORKSPACE_IDS.estudio, USER_IDS.carla, 'carla@estudionorte.com', 'editor'),
    member(4, WORKSPACE_IDS.estudio, USER_IDS.diego, 'diego@clientes.com', 'viewer'),
    member(5, WORKSPACE_IDS.canal, USER_IDS.martin, 'martin@estudionorte.com', 'admin'),
    member(6, WORKSPACE_IDS.canal, USER_IDS.lucia, 'lucia@estudionorte.com', 'editor'),
    member(7, WORKSPACE_IDS.nuevo, USER_IDS.nuevo, 'sofia@ejemplo.com', 'admin'),
  ];
  if (options.seatsFull) members.push(member(8, WORKSPACE_IDS.estudio, null, 'juan@estudionorte.com', 'editor', 'pending', 'valida'));

  const mainWorkspace = isNew ? WORKSPACE_IDS.nuevo : WORKSPACE_IDS.estudio;
  const kitId = mid(KIND.brandKit, 1);
  const withKits = options.brandKits ?? !isNew;
  const brandKits: BrandKit[] = withKits
    ? [
        { id: kitId, workspace: mainWorkspace, user: USER_IDS.lucia, name: 'Estudio Norte', description: 'Colores y tipografía del estudio.', primary_font: '/media/assets/fonts/Inter-Bold.ttf', secondary_font: null, primary_color: '#FFFFFF', secondary_color: '#111113', accent_color: '#F2C94C', watermark_logo: '/media/assets/logos/estudio-norte.png', intro_video: null, outro_video: '/media/assets/videos/outro.mp4', is_default: true, watermark_position: 'bottom_right', created_at: iso(now - 80 * DAY) },
        { id: mid(KIND.brandKit, 2), workspace: mainWorkspace, user: USER_IDS.martin, name: 'Gaming nocturno', description: 'Para los streams.', primary_font: null, secondary_font: null, primary_color: '#EDEDF0', secondary_color: '#0A0A0B', accent_color: '#3FCF8E', watermark_logo: null, intro_video: null, outro_video: null, is_default: false, watermark_position: 'bottom_right', created_at: iso(now - 30 * DAY) },
        { id: mid(KIND.brandKit, 3), workspace: mainWorkspace, user: USER_IDS.carla, name: 'Cliente: Bodega Andes', description: '', primary_font: null, secondary_font: null, primary_color: '#FFF8F0', secondary_color: '#3A1F2B', accent_color: '#A13D63', watermark_logo: '/media/assets/logos/bodega.png', intro_video: '/media/assets/videos/intro-bodega.mp4', outro_video: null, is_default: false, watermark_position: 'bottom_right', created_at: iso(now - 9 * DAY) },
      ]
    : [];

  const mode = options.projects ?? (isNew ? 'none' : 'default');
  const specs = mode === 'none' ? [] : mode === 'all-states' ? allStateProjects(discount) : mode === 'many' ? manyProjects(discount) : defaultProjects(discount);
  const projects = specs.map((spec) => buildProject({ ...spec, workspace: mainWorkspace }, now, withKits ? kitId : null));

  const walletId = mid(KIND.wallet, 1);
  const transactions: MockTransaction[] = projects.map((project, position) => transactionFor(project, specs[position]?.index ?? position, walletId));
  // "Renderizar sin revisar" with the render under way: its own reservation, still open.
  for (const project of projects) {
    const reservationId = project.metadata.auto_render_reservation_id;
    if (!reservationId || project.status !== 'rendering') continue;
    transactions.push({
      id: reservationId,
      workspace: project.workspace,
      wallet: walletId,
      created_by: project.uploaded_by,
      amount: priceOf(Math.ceil(project.metadata.auto_render_seconds ?? 60), project.metadata.render_resolution ?? '720p', false, discount),
      transaction_type: 'SPEND',
      status: 'RESERVED',
      description: `Render sin revisar: ${project.title}`,
      created_at: project.created_at,
      kind: 'reserved',
      created_by_detail: project.uploaded_by_detail ?? null,
      project: { id: project.id, title: project.title },
      category: 'render',
    });
    project._reservations.push(reservationId);
  }
  // Purchases, so Movimientos shows every kind of row the backend writes. A partial charge has no row of
  // its own: commit_partial_reservation lowers the SPEND to what was charged and returns the rest silently.
  const purchase = (n: number, tokens: number, ageMs: number): MockTransaction => ({
    id: mid(KIND.transaction, 9000 + n),
    workspace: mainWorkspace,
    wallet: walletId,
    created_by: USER_IDS.lucia,
    amount: tokens,
    transaction_type: 'DEPOSIT',
    status: 'COMPLETED',
    description: `Compra de ${tokens} tokens`,
    created_at: iso(now - ageMs),
    kind: 'purchase',
    created_by_detail: { id: USER_IDS.lucia, name: 'Lucía Fernández' },
    project: null,
    category: 'purchase',
  });
  if (mode !== 'none') {
    transactions.push(purchase(1, 5000, 20 * DAY), purchase(2, 15000, 45 * DAY));
    transactions.push({
      id: mid(KIND.transaction, 9200),
      workspace: mainWorkspace,
      wallet: walletId,
      created_by: USER_IDS.lucia,
      amount: 1000,
      transaction_type: 'DEPOSIT',
      status: 'PENDING',
      description: 'Compra de 1000 tokens',
      created_at: iso(now - 3 * MIN),
      kind: 'pending',
      created_by_detail: { id: USER_IDS.lucia, name: 'Lucía Fernández' },
      project: null,
      category: 'purchase',
    });
  }
  transactions.sort((a, b) => b.created_at.localeCompare(a.created_at));

  const reserved = round2(transactions.filter((tx) => tx.status === 'RESERVED').reduce((sum, tx) => sum + tx.amount, 0));
  const balance = options.balance ?? (isNew || planKey === 'free' ? 300 : 4860);
  const wallets = [
    { id: walletId, workspace: mainWorkspace, available_balance: balance, reserved_balance: reserved, updated_at: iso(now - 12 * MIN) },
    { id: mid(KIND.wallet, 2), workspace: isNew ? WORKSPACE_IDS.estudio : WORKSPACE_IDS.canal, available_balance: 300, reserved_balance: 0, updated_at: iso(now - 30 * DAY) },
    { id: mid(KIND.wallet, 3), workspace: isNew ? WORKSPACE_IDS.canal : WORKSPACE_IDS.nuevo, available_balance: 300, reserved_balance: 0, updated_at: iso(now - 5 * MIN) },
  ];

  const social = socialAccounts(
    mainWorkspace,
    options.social ?? (isNew || mode === 'none' ? {} : { TIKTOK: 'connected', INSTAGRAM_REELS: 'connected', YOUTUBE_SHORTS: 'disconnected' }),
    now,
  );

  // Publications on the clips of the finished project, one per state.
  const posts: MockPost[] = [];
  const vlog = projects.find((project) => project.status === 'completed' && project.project_type === 'repurpose');
  if (vlog) {
    const plan: Array<{ clip: number; platform: Platform; status: PostStatus; offsetMs: number }> = [
      { clip: 0, platform: 'TIKTOK', status: 'PUBLISHED', offsetMs: -2 * DAY },
      { clip: 0, platform: 'INSTAGRAM_REELS', status: 'PUBLISHED', offsetMs: -2 * DAY + HOUR },
      { clip: 1, platform: 'TIKTOK', status: 'SCHEDULED', offsetMs: 1 * DAY + 4 * HOUR },
      { clip: 2, platform: 'INSTAGRAM_REELS', status: 'SCHEDULED', offsetMs: 2 * DAY + 4 * HOUR },
      { clip: 3, platform: 'TIKTOK', status: 'FAILED', offsetMs: -6 * HOUR },
      { clip: 4, platform: 'INSTAGRAM_REELS', status: 'QUEUED', offsetMs: 4 * MIN },
      { clip: 5, platform: 'TIKTOK', status: 'PROCESSING', offsetMs: -1 * MIN },
      { clip: 1, platform: 'INSTAGRAM_REELS', status: 'DRAFT', offsetMs: 3 * DAY },
    ];
    plan.forEach((item, position) => {
      const clip = vlog.clips[item.clip];
      if (!clip) return;
      const publishAt = iso(now + item.offsetMs);
      const failed = item.status === 'FAILED';
      const post: MockPost = {
        id: mid(KIND.post, position + 1),
        workspace: mainWorkspace,
        video_clip: clip.id,
        platform: item.platform,
        publish_at: publishAt,
        status: item.status,
        error_log: failed ? 'AyrshareError: Instagram/TikTok rejected the media (code 137)' : null,
        retry_count: failed ? 2 : 0,
        generated_caption: 'Subimos a 3.000 metros en una hora y esto fue lo que pasó.',
        generated_hashtags: ['mendoza', 'vlog', 'montaña', 'viajes'],
        created_at: iso(now - 3 * DAY),
        updated_at: iso(now - 1 * HOUR),
        caption: 'Subimos a 3.000 metros en una hora y esto fue lo que pasó.',
        hashtags: ['mendoza', 'vlog', 'montaña', 'viajes'],
        seo_status: 'ready',
        clip: { id: clip.id, title: clip.title, project_id: vlog.id, project_title: vlog.title, duration_seconds: round2(clip.end_time - clip.start_time), thumbnail_url: null },
        post_url: item.status === 'PUBLISHED' ? `https://example.com/${item.platform.toLowerCase()}/post/${position + 1}` : null,
        error_message: failed ? 'La red rechazó el video. Revisá que la cuenta siga conectada y volvé a intentarlo.' : null,
        error_code: failed ? 'media_rejected' : null,
        tiktok: item.platform === 'TIKTOK' ? { privacy_level: 'PUBLIC_TO_EVERYONE', allow_comment: true, allow_duet: false, allow_stitch: false, brand_organic: false, branded_content: false } : null,
      };
      posts.push(post);
      clip.posts = [...(clip.posts ?? []), { id: post.id, platform: post.platform, status: post.status, publish_at: post.publish_at }];
      if (item.status === 'PUBLISHED') clip.status = 'published';
    });
  }

  const sessionUser = as ? USER_IDS[as] : null;
  const notifications: MockNotification[] = [];
  const activity: MockActivity[] = [];
  if (mode !== 'none' && sessionUser) {
    const review = projects.find((project) => project.status === 'awaiting_approval');
    const failed = projects.find((project) => project.status === 'failed');
    const done = projects.find((project) => project.status === 'completed');
    const failedPost = posts.find((post) => post.status === 'FAILED');
    const add = (n: number, item: Omit<MockNotification, 'id' | 'workspace' | 'user'>) =>
      notifications.push({ id: mid(KIND.notification, n), workspace: mainWorkspace, user: sessionUser, ...item });
    if (review) add(1, { type: 'project_ready', title: `${review.title.split(' — ')[0]} está listo para revisar`, body: `Encontramos ${review.clips.length} clips.`, created_at: iso(now - 12 * MIN), read_at: null, project_id: review.id, post_id: null });
    if (failedPost) add(2, { type: 'post_failed', title: 'No pudimos publicar en TikTok', body: failedPost.clip.title, created_at: iso(now - 6 * HOUR), read_at: null, project_id: failedPost.clip.project_id, post_id: failedPost.id });
    if (done) add(3, { type: 'render_finished', title: `${done.title}: terminó el render`, body: `${done.clips.length} clips listos.`, created_at: iso(now - 1 * DAY), read_at: iso(now - 20 * HOUR), project_id: done.id, post_id: null });
    if (failed) add(4, { type: 'project_failed', title: `${failed.title} no se pudo procesar`, body: `Te devolvimos ${failed.cost?.amount ?? 0} tokens.`, created_at: iso(now - 3 * DAY), read_at: iso(now - 3 * DAY + HOUR), project_id: failed.id, post_id: null });
    if ((options.balance ?? balance) < 500) add(5, { type: 'low_balance', title: 'Te queda poco saldo', body: 'No alcanza para un video promedio.', created_at: iso(now - 2 * HOUR), read_at: null, project_id: null, post_id: null });

    let activityIndex = 0;
    for (const project of projects) {
      const created = new Date(project.created_at).getTime();
      const actor = project.uploaded_by_detail ?? null;
      const push = (type: MockActivity['type'], offsetMs: number, description: string, amount: number | null, who: typeof actor = actor) => {
        activityIndex += 1;
        activity.push({ id: mid(KIND.activity, activityIndex), project: project.id, type, actor: who, created_at: iso(created + offsetMs), description, amount });
      };
      push('project_created', 0, 'Creó el proyecto', null);
      push('tokens_reserved', 1000, 'Reserva por el procesamiento', -(project.cost?.amount ?? 0));
      if (project.metadata.pte) push('prompt_applied', 30_000, 'La IA aplicó la configuración del prompt', null, null);
      if (['awaiting_approval', 'rendering', 'completed', 'partial'].includes(project.status) && project.project_type === 'repurpose')
        push('review_ready', 8 * MIN, `Listo para revisar: ${project.clips.length} clips sugeridos`, null, null);
      if (['rendering', 'completed', 'partial'].includes(project.status) && project.project_type === 'repurpose')
        push('review_approved', 15 * MIN, `Aprobó ${project.clips.length} clips para renderizar`, null);
      if (project.status === 'completed' || project.status === 'partial') {
        push('render_finished', 26 * MIN, 'Terminó el render', null, null);
        push('tokens_charged', 26 * MIN + 1000, 'Cobro del procesamiento', -(project.cost?.amount ?? 0), null);
      }
      if (project.status === 'failed') {
        push('project_failed', 6 * MIN, 'El proceso falló', null, null);
        push('tokens_refunded', 7 * MIN, 'Devolución de la reserva', project.cost?.amount ?? 0, null);
      }
    }
    for (const post of posts) {
      activityIndex += 1;
      activity.push({
        id: mid(KIND.activity, activityIndex),
        project: post.clip.project_id,
        type: post.status === 'PUBLISHED' ? 'post_published' : post.status === 'FAILED' ? 'post_failed' : 'post_scheduled',
        actor: { id: USER_IDS.carla, name: 'Carla Ruiz' },
        created_at: post.created_at,
        description: post.status === 'PUBLISHED' ? `Publicó «${post.clip.title}»` : post.status === 'FAILED' ? `Falló la publicación de «${post.clip.title}»` : `Programó «${post.clip.title}»`,
        amount: null,
        platform: post.platform,
      });
    }
  }

  return {
    version: DB_VERSION,
    scenario: options.scenario,
    seededAt: now,
    sessionUserId: sessionUser,
    users,
    workspaces,
    members,
    plans,
    wallets,
    transactions,
    projects,
    brandKits,
    socialAccounts: [
      ...social,
      ...socialAccounts(isNew ? WORKSPACE_IDS.estudio : WORKSPACE_IDS.canal, {}, now),
      ...socialAccounts(isNew ? WORKSPACE_IDS.canal : WORKSPACE_IDS.nuevo, {}, now),
    ],
    posts,
    notifications,
    activity,
    purchases: [],
    previews: [],
    invitations: [
      { token: 'valida', member: null, workspace_id: WORKSPACE_IDS.estudio, workspace_name: 'Estudio Norte', inviter_name: 'Lucía Fernández', role: 'editor', email: 'juan@estudionorte.com', status: 'pending', has_account: false },
      { token: 'con-cuenta', member: null, workspace_id: WORKSPACE_IDS.estudio, workspace_name: 'Estudio Norte', inviter_name: 'Lucía Fernández', role: 'admin', email: 'sofia@ejemplo.com', status: 'pending', has_account: true },
      { token: 'vencida', member: null, workspace_id: WORKSPACE_IDS.estudio, workspace_name: 'Estudio Norte', inviter_name: 'Lucía Fernández', role: 'editor', email: 'juan@estudionorte.com', status: 'expired', has_account: false },
      { token: 'usada', member: null, workspace_id: WORKSPACE_IDS.estudio, workspace_name: 'Estudio Norte', inviter_name: 'Lucía Fernández', role: 'viewer', email: 'juan@estudionorte.com', status: 'used', has_account: true },
    ],
    flags: { sessionExpired: false, maintenance: false, serverDown: false, rejectPayments: false, unverifiedLogin: false, planCheckout: false, ...options.flags },
    resendAt: {},
  };
}
