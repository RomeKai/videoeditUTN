import type { Plan, Pricing } from '@/api/contracts/billing';
import type { Platform } from '@/api/contracts/common';
import type { ActivityItem, Clip, Project, StageStatus } from '@/api/contracts/projects';
import { groupTranscript, type TranscriptLine } from '@/lib/download';
import { autoRenderSkippedMessage } from '@/lib/error-messages';
import { formatDuration, formatMinutes, formatNumber, formatRelative, formatTime, formatTokens, plural } from '@/lib/format';
import { estimateCost, type CostEstimate } from '@/lib/pricing';
import { countClips, describeProjectStatus, LABELS, type ProjectStatusView, type StatusTone } from '@/lib/project-status';
import type { IconName } from '@/ui/Icon';
import type { StageState } from '@/ui/StageStepper';

/**
 * "Editar el video completo" as the backend decides it (`_is_whole_video`): a
 * `single_edit` project priced at creation for its whole duration. A
 * `single_edit` row without `metadata.priced_duration_s` (a link, an older
 * project) was never priced that way and keeps the clips flow, with review.
 */
export function isWholeVideo(project: Pick<Project, 'project_type' | 'metadata'>): boolean {
  return project.project_type === 'single_edit' && project.metadata?.priced_duration_s != null;
}

/* ── Process timeline (map D1 › Resumen) ────────────────────────────────── */

export type StepKey = 'prepare' | 'transcribe' | 'moderate' | 'select' | 'render';

export interface ProcessStep {
  key: StepKey;
  label: string;
  state: StageState;
  /** Short state word of the "Estado" column. */
  stateLabel: string;
  description: string;
  startedAt: string | null;
  /** Seconds; null when it has not run (or has no time yet). */
  durationSeconds: number | null;
  /** The render waits for the review: dashed marker and the "Necesita tu revisión" tag. */
  gated?: boolean;
  /** Collapsed row of a failed project: "3 pasos no llegaron a empezar". */
  collapsed?: boolean;
}

/** Stages in pipeline order; a step is keyed by the stage its work produces (pipeline_state.py · STAGE_ORDER). */
const STAGE_ORDER = ['uploaded', 'audio_extracted', 'transcribed', 'moderated', 'clips_selected', 'render_dispatched', 'completed'] as const;

const STEP_STAGE: Record<StepKey, string> = {
  prepare: 'audio_extracted',
  transcribe: 'transcribed',
  moderate: 'moderated',
  select: 'clips_selected',
  render: 'render_dispatched',
};

const STEP_TEXT: Record<StepKey, { label: string; pending: string; running: string; done: string }> = {
  prepare: {
    label: 'Preparar',
    pending: 'Leemos el video y separamos el audio.',
    running: 'Leyendo el video y separando el audio.',
    done: 'Leímos el video y separamos el audio.',
  },
  transcribe: {
    label: 'Transcribir',
    pending: 'Pasamos a texto todo lo que se dice.',
    running: 'Pasando a texto todo lo que se dice.',
    done: 'Pasamos a texto todo lo que se dice.',
  },
  moderate: {
    label: 'Revisar contenido',
    pending: 'Chequeamos que cumpla nuestras políticas.',
    running: 'Chequeando que cumpla nuestras políticas.',
    done: 'Cumple nuestras políticas.',
  },
  select: {
    label: 'Elegir clips',
    pending: 'La IA busca los mejores momentos.',
    running: 'La IA está buscando los mejores momentos.',
    done: 'La IA eligió los mejores momentos.',
  },
  render: {
    label: 'Renderizar',
    pending: 'Genera los videos finales.',
    running: 'Generando los videos finales.',
    done: 'Los videos están listos.',
  },
};

export const WAITING_TEXT = 'Hay mucha demanda. Tu video sigue en la fila y arranca solo; no tenés que hacer nada.';

const STATE_LABEL: Record<StageState, string> = { done: 'Listo', run: 'En curso', wait: 'En espera', fail: 'Falló', pending: 'Pendiente' };

/** Short reason shown in the failed step of the timeline. `ModerationBlockedError` is not here: the service failed, not the video. */
const FAIL_STEP_TEXT: Record<string, string> = {
  UnsafeContentError: 'El contenido no cumple nuestras políticas.',
  CapacityTimeoutError: 'Había mucha demanda y no pudimos procesarlo.',
  AINoSpeechDetectedError: 'No encontramos voz en el audio.',
  AIEmptySelectionError: 'No encontramos momentos que funcionen como clips.',
  SoftTimeLimitExceeded: 'Tardó demasiado en procesarse.',
  AutoRenderNotPaid: 'No alcanzó el saldo para renderizar los clips que faltaban.',
};

function stageIndex(stage: string): number {
  return STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number]);
}

function fromStatus(status: StageStatus | undefined): StageState | null {
  switch (status) {
    case 'completed':
      return 'done';
    case 'running':
      return 'run';
    case 'pending_capacity':
      return 'wait';
    case 'failed':
      return 'fail';
    case 'pending':
      return 'pending';
    default:
      return null;
  }
}

/** How many ingest stages are finished, from `pipeline_stage` (or from the status map once the stage is terminal). */
function finishedIndex(project: Pick<Project, 'pipeline_stage' | 'pipeline_stage_status' | 'status'>): number {
  const direct = stageIndex(project.pipeline_stage);
  if (direct >= 0) return direct;
  if (project.pipeline_stage === 'partial') return stageIndex('render_dispatched');
  // failed: the last stage the map marks as completed.
  let last = 0;
  STAGE_ORDER.forEach((stage, index) => {
    if (project.pipeline_stage_status?.[stage] === 'completed') last = Math.max(last, index);
  });
  return last;
}

type StepsProject = Pick<
  Project,
  'status' | 'pipeline_stage' | 'pipeline_stage_status' | 'pipeline_error_code' | 'clips' | 'project_type' | 'auto_render_bypass' | 'stage_timings' | 'metadata'
>;

/**
 * Rows of the process timeline. A whole-video project has no "Elegir clips" and
 * its render does not wait for a review. `now` gives the running step its live duration.
 */
export function buildProcessSteps(project: StepsProject, now: number = Date.now()): ProcessStep[] {
  const single = isWholeVideo(project);
  const keys: StepKey[] = single ? ['prepare', 'transcribe', 'moderate', 'render'] : ['prepare', 'transcribe', 'moderate', 'select', 'render'];
  const finished = finishedIndex(project);
  const clips = countClips(project.clips);
  const queued = project.status === 'uploaded';
  const failed = project.status === 'failed';
  const active = project.status === 'ingesting' || project.status === 'rendering';
  // Stopped at the review: the render waits for the user even if they asked to skip it (no balance, or the plan).
  const bypass = project.auto_render_bypass && project.status !== 'awaiting_approval';

  const steps = keys.map((key): ProcessStep => {
    const stage = STEP_STAGE[key];
    const index = stageIndex(stage);
    const text = STEP_TEXT[key];
    const timing = project.stage_timings?.[stage];
    let state: StageState;
    if (key === 'render') {
      if (project.status === 'completed') state = 'done';
      else if (project.status === 'partial') state = clips.ready > 0 ? 'done' : 'fail';
      else if (project.status === 'rendering' || (project.status === 'ingesting' && finished >= stageIndex('clips_selected'))) state = 'run';
      else state = 'pending';
    } else if (queued) state = 'pending';
    else if (finished >= index) state = 'done';
    else {
      const mapped = fromStatus(project.pipeline_stage_status?.[stage]);
      // The backend marks a stage `failed` on every failure it is going to retry, while the project stays
      // `ingesting` during the backoff. Internal retries are not shown (map §3): it keeps saying "En curso".
      if (mapped === 'fail' && !failed) state = 'run';
      else if (mapped && mapped !== 'done') state = mapped;
      else if (finished === index - 1 && active) state = 'run';
      else state = 'pending';
      if (!active && !failed && (state === 'run' || state === 'wait')) state = 'pending';
      if (failed && state !== 'fail') state = 'pending';
    }

    let description = state === 'done' ? text.done : state === 'run' ? text.running : text.pending;
    if (state === 'wait') description = WAITING_TEXT;
    if (state === 'fail') description = FAIL_STEP_TEXT[project.pipeline_error_code] ?? 'Algo falló de nuestro lado.';
    if (key === 'select' && state === 'done' && clips.total > 0) description = `Encontramos ${plural(clips.total, 'clip', 'clips')}.`;
    if (key === 'render') {
      if (single) {
        description = state === 'done' ? 'Tu video está listo.' : state === 'run' ? 'Estamos editando tu video.' : state === 'fail' ? 'El video no se pudo generar.' : 'Editamos el video completo: no hay revisión.';
      } else if (state === 'run') description = `${clips.ready} de ${clips.total} clips listos.`;
      else if (project.status === 'completed') description = `${clips.ready} de ${clips.total} clips listos.`;
      else if (project.status === 'partial')
        description = `${plural(clips.ready, 'clip listo', 'clips listos')} y ${clips.failed} no se ${clips.failed === 1 ? 'pudo' : 'pudieron'} generar.`;
      else if (bypass) description = 'Arranca sola cuando elijamos los clips: pediste renderizar sin revisar.';
      else description = 'No arranca sola: empieza cuando revises y apruebes los clips.';
    }

    const startedAt = timing?.started_at ?? null;
    let durationSeconds = timing?.duration_s ?? null;
    if (durationSeconds === null && state === 'run' && startedAt) durationSeconds = Math.max(0, (now - new Date(startedAt).getTime()) / 1000);
    const gated = key === 'render' && !single && !bypass && state === 'pending' && !failed;
    return { key, label: single && key === 'render' ? 'Editar el video' : text.label, state, stateLabel: STATE_LABEL[state], description, startedAt, durationSeconds, gated };
  });

  if (queued && steps[0]) {
    steps[0] = { ...steps[0], stateLabel: 'En cola', description: 'Tu video está en la fila para procesarse.' };
  }
  if (!failed) return steps;

  // A failed project always shows where it stopped, then one row for what never started.
  let failAt = steps.findIndex((step) => step.state === 'fail');
  if (failAt === -1) {
    failAt = steps.findIndex((step) => step.state !== 'done');
    const target = failAt === -1 ? undefined : steps[failAt];
    if (target) steps[failAt] = { ...target, state: 'fail', stateLabel: STATE_LABEL.fail, description: FAIL_STEP_TEXT[project.pipeline_error_code] ?? 'Algo falló de nuestro lado.' };
  }
  if (failAt === -1) return steps;
  const rest = steps.slice(failAt + 1);
  const shown = steps.slice(0, failAt + 1);
  if (rest.length === 0) return shown;
  const names = rest.map((step) => step.label);
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} y ${names[names.length - 1]}`;
  shown.push({
    key: rest[0]?.key ?? 'render',
    label: rest.length === 1 ? '1 paso no llegó a empezar' : `${rest.length} pasos no llegaron a empezar`,
    state: 'pending',
    stateLabel: 'No se hizo',
    description: `${list}.`,
    startedAt: null,
    durationSeconds: null,
    collapsed: true,
  });
  return shown;
}

/** 1-based position of the step where a failed project stopped. */
export function failedStepNumber(steps: ProcessStep[]): number | null {
  const index = steps.findIndex((step) => step.state === 'fail');
  return index === -1 ? null : index + 1;
}

/** Seconds from the first stage's start to the last one's end ("Listo en 8:17"). */
export function totalProcessSeconds(project: Pick<Project, 'stage_timings'>): number | null {
  const total = Object.entries(project.stage_timings ?? {})
    .filter(([stage]) => stage !== 'uploaded')
    .reduce((sum, [, timing]) => sum + (timing.duration_s ?? 0), 0);
  return total > 0 ? total : null;
}

/* ── Header ─────────────────────────────────────────────────────────────── */

type HeaderProject = Pick<Project, 'status' | 'pipeline_error_at' | 'eta_seconds' | 'stage_timings' | 'project_type' | 'metadata' | 'created_at'>;

/** Line under the title: the text of map §3, with the estimate, the wait or when it failed. */
export function headerStatusText(project: HeaderProject, view: ProjectStatusView, now: number = Date.now()): string {
  if (view.step) {
    const eta = project.eta_seconds;
    const goal = isWholeVideo(project) ? 'para terminar' : 'para revisar';
    return eta ? `${view.text} · faltan ≈ ${formatMinutes(Math.max(60, eta))} ${goal}` : view.text;
  }
  if (view.key === 'waiting') {
    const finishedAt = Object.values(project.stage_timings ?? {})
      .map((timing) => timing.finished_at)
      .filter((value): value is string => Boolean(value))
      .sort()
      .pop();
    const since = finishedAt ?? project.created_at;
    const minutes = Math.floor((now - new Date(since).getTime()) / 60_000);
    return minutes >= 1 ? `En espera hace ${formatMinutes(minutes * 60)}.` : 'En espera.';
  }
  if (view.key === 'failed') {
    return project.pipeline_error_at ? `Falló ${formatRelative(project.pipeline_error_at, now)}.` : 'No se pudo procesar.';
  }
  return view.text;
}

const PROVIDERS: Array<[RegExp, string]> = [
  [/youtube\.com|youtu\.be/, 'YouTube'],
  [/vimeo\.com/, 'Vimeo'],
  [/twitch\.tv/, 'Twitch'],
  [/drive\.google\.com/, 'Google Drive'],
];

/** "Archivo", or "Link de YouTube" for projects created from a URL. */
export function sourceInfo(project: Pick<Project, 'video_url'>): { icon: IconName; label: string } {
  if (!project.video_url) return { icon: 'file', label: 'Archivo' };
  let host = '';
  try {
    host = new URL(project.video_url).hostname;
  } catch {
    host = '';
  }
  const provider = PROVIDERS.find(([pattern]) => pattern.test(host))?.[1];
  return { icon: 'link', label: provider ? `Link de ${provider}` : 'Link' };
}

export function projectDuration(project: Pick<Project, 'duration_seconds' | 'metadata'>): number | null {
  return project.duration_seconds ?? project.metadata.source_media?.duration_s ?? project.metadata.duration ?? null;
}

const COST_WORD = { reserved: ['reservado', 'reservados'], charged: ['cobrado', 'cobrados'], refunded: ['devuelto', 'devueltos'] } as const;

/** "1.404,90 tokens reservados", "763,20 tokens cobrados", "1.755 tokens devueltos". */
export function costLabel(cost: NonNullable<Project['cost']>): string {
  const words = COST_WORD[cost.state];
  return `${formatTokens(cost.amount)} ${Math.abs(cost.amount) === 1 ? words[0] : words[1]}`;
}

/* ── Tabs ───────────────────────────────────────────────────────────────── */

export type ProjectTabKey = 'resumen' | 'clips' | 'resultado' | 'transcripcion' | 'actividad';

export interface ProjectTab {
  key: ProjectTabKey;
  href: string;
  label: string;
  count?: number;
  /** Why the tab cannot be opened yet. */
  disabledReason?: string;
}

type TabsProject = Pick<Project, 'id' | 'status' | 'project_type' | 'metadata' | 'clips' | 'transcript_data' | 'pipeline_stage' | 'pipeline_stage_status'>;

/** Tabs of a project: Resumen, Clips (or Resultado for a whole video), Transcripción and Actividad. */
export function buildProjectTabs(project: TabsProject): ProjectTab[] {
  const base = `/proyectos/${project.id}`;
  const failed = project.status === 'failed';
  const single = isWholeVideo(project);
  const hasClips = project.clips.length > 0 || ['awaiting_approval', 'rendering', 'completed', 'partial'].includes(project.status);
  const hasTranscript = (project.transcript_data?.length ?? 0) > 0 || (!failed && finishedIndex(project) >= stageIndex('transcribed'));
  const stopped = 'El proyecto falló antes de llegar a este paso';
  return [
    { key: 'resumen', href: base, label: 'Resumen' },
    single
      ? { key: 'resultado', href: `${base}/resultado`, label: 'Resultado', disabledReason: hasClips ? undefined : failed ? stopped : 'Disponible cuando empecemos a editar el video' }
      : {
          key: 'clips',
          href: `${base}/clips`,
          label: 'Clips',
          count: project.clips.length > 0 ? project.clips.length : undefined,
          disabledReason: hasClips ? undefined : failed ? stopped : 'Disponible cuando elijamos los clips',
        },
    {
      key: 'transcripcion',
      href: `${base}/transcripcion`,
      label: 'Transcripción',
      disabledReason: hasTranscript ? undefined : failed ? stopped : 'Disponible cuando termine de transcribir',
    },
    { key: 'actividad', href: `${base}/actividad`, label: 'Actividad' },
  ];
}

export function currentTab(pathname: string, projectId: string): ProjectTabKey {
  const rest = pathname.replace(/\/+$/, '').split(`/proyectos/${projectId}`)[1] ?? '';
  if (rest.startsWith('/clips')) return 'clips';
  if (rest.startsWith('/resultado')) return 'resultado';
  if (rest.startsWith('/transcripcion')) return 'transcripcion';
  if (rest.startsWith('/actividad')) return 'actividad';
  return 'resumen';
}

/* ── Auto-render notices (map C1 › Renderizar sin revisar) ──────────────── */

export type AutoRenderNotice =
  | { kind: 'reserved'; title: string; text: string }
  | { kind: 'insufficient_funds' | 'plan_limit'; title: string; text: string };

/**
 * "Renderizar sin revisar" reserves the render when the clips are chosen. If the
 * balance is short or the plan does not allow it, the project stops at the
 * review and `metadata.auto_render_skipped` says why.
 */
export function autoRenderNotice(
  project: Pick<Project, 'status' | 'metadata' | 'auto_render_bypass' | 'project_type' | 'pipeline_stage'>,
  /** Ids of the workspace's open reservations (`GET /wallet`), or null while they are not known. */
  openReservations: ReadonlyArray<string> | null = null,
): AutoRenderNotice | null {
  const skipped = project.metadata.auto_render_skipped;
  const stopped = project.status === 'awaiting_approval' ? autoRenderSkippedMessage(skipped) : null;
  if (stopped && skipped) return { kind: skipped, ...stopped };
  const rendering = project.status === 'rendering' || (project.status === 'ingesting' && ['clips_selected', 'render_dispatched'].includes(project.pipeline_stage));
  // Only while that reservation is still open: a later paid re-render keeps the old id in metadata.
  const reservationId = project.metadata.auto_render_reservation_id;
  if (rendering && !isWholeVideo(project) && reservationId && openReservations?.includes(reservationId)) {
    return {
      kind: 'reserved',
      title: 'Reservamos el render de los clips',
      text: 'Pediste renderizar sin revisar: los clips elegidos ya se están generando. Cuesta lo mismo que aprobarlos y se cobra solo lo que se entrega.',
    };
  }
  return null;
}

/* ── Applied configuration ──────────────────────────────────────────────── */

const SIZE_PLURAL = { small: 'Chicos', medium: 'Medianos', large: 'Grandes' } as const;
const POSITION_WORD = { top: 'arriba', center: 'al centro', bottom: 'abajo' } as const;
const COLOR_NAMES: Record<string, string> = {
  '#FFFFFF': 'blanco',
  '#F4F1EA': 'blanco',
  '#000000': 'negro',
  '#FFFF00': 'amarillo',
  '#F2C94C': 'amarillo',
  '#FF0000': 'rojo',
  '#00FF00': 'verde',
  '#3FCF8E': 'verde',
};

export function colorName(hex: string): string {
  return COLOR_NAMES[hex.toUpperCase()] ?? hex.toUpperCase();
}

const RATIO_WORD = { '9:16': 'vertical', '1:1': 'cuadrado', '16:9': 'horizontal' } as const;
export const RESOLUTION_PIXELS: Record<string, Record<string, string>> = {
  '720p': { '9:16': '720 × 1280', '1:1': '720 × 720', '16:9': '1280 × 720' },
  '1080p': { '9:16': '1080 × 1920', '1:1': '1080 × 1080', '16:9': '1920 × 1080' },
  '4K': { '9:16': '2160 × 3840', '1:1': '2160 × 2160', '16:9': '3840 × 2160' },
};

export function ratioLabel(ratio: Project['aspect_ratio']): string {
  return `${ratio} ${RATIO_WORD[ratio]}`;
}

type ConfigProject = Pick<
  Project,
  | 'project_type'
  | 'aspect_ratio'
  | 'metadata'
  | 'editing_style'
  | 'render_layout'
  | 'brand_kit'
  | 'brand_kit_name'
  | 'add_subtitles'
  | 'subtitle_size'
  | 'subtitle_position'
  | 'subtitle_color'
  | 'subtitle_words_per_segment'
  | 'intelligence_level'
  | 'use_facetracking'
  | 'remove_silences'
  | 'auto_render_bypass'
  | 'status'
>;

export function subtitlesSummary(project: Pick<Project, 'add_subtitles' | 'subtitle_size' | 'subtitle_position' | 'subtitle_color' | 'subtitle_words_per_segment'>): string {
  if (!project.add_subtitles) return 'Sin subtítulos';
  const words = project.subtitle_words_per_segment;
  return `${SIZE_PLURAL[project.subtitle_size]} · ${POSITION_WORD[project.subtitle_position]} · ${colorName(project.subtitle_color)} · ${words} ${words === 1 ? 'palabra' : 'palabras'} por línea`;
}

export interface ConfigItem {
  key: string;
  label: string;
  value: string;
  /** Takes the two columns. */
  wide?: boolean;
}

export interface ConfigGroup {
  title: string;
  items: ConfigItem[];
}

/** The applied configuration in the three groups of the artboard: Salida, Estilo and IA. */
export function buildConfigGroups(project: ConfigProject): ConfigGroup[] {
  const single = isWholeVideo(project);
  const past = !['uploaded', 'ingesting'].includes(project.status);
  const yes = (value: boolean) => (value ? 'Sí' : 'No');
  const output: ConfigItem[] = [
    { key: 'type', label: 'Qué hacer', value: LABELS.project_type[project.project_type] },
    { key: 'ratio', label: 'Formato', value: project.aspect_ratio },
    { key: 'resolution', label: 'Resolución', value: project.metadata.render_resolution ?? '720p' },
  ];
  if (!single) output.push({ key: 'clips', label: 'Clips', value: `Hasta ${project.metadata.max_clips ?? 8}` });
  const ai: ConfigItem[] = [
    { key: 'level', label: 'Nivel de IA', value: LABELS.intelligence_level[project.intelligence_level] },
    { key: 'faces', label: 'Seguir caras', value: yes(project.use_facetracking) },
    { key: 'silences', label: 'Quitar silencios', value: yes(project.remove_silences) },
    { key: 'seo', label: 'SEO', value: project.metadata.use_seo ? 'Sí' : 'No' },
  ];
  if (!single) {
    ai.push({
      key: 'bypass',
      label: 'Renderizar sin revisar',
      value: project.auto_render_bypass ? 'Sí: renderiza apenas elige los clips' : past ? 'No: se detuvo en la revisión' : 'No: se detiene en la revisión',
      wide: true,
    });
  }
  return [
    { title: 'Salida', items: output },
    {
      title: 'Estilo',
      items: [
        { key: 'style', label: 'Estilo', value: LABELS.editing_style[project.editing_style] },
        { key: 'layout', label: 'Layout', value: LABELS.render_layout[project.render_layout] },
        { key: 'kit', label: 'Brand kit', value: project.brand_kit ? (project.brand_kit_name ?? 'Brand kit') : 'Sin brand kit', wide: true },
        { key: 'subtitles', label: 'Subtítulos', value: subtitlesSummary(project), wide: true },
      ],
    },
    { title: 'IA', items: ai },
  ];
}

/** One line for the folded card on phones: "Sacar clips · 9:16 · 720p · hasta 8 clips". */
export function configSummary(project: ConfigProject): string {
  const parts: string[] = [LABELS.project_type[project.project_type], project.aspect_ratio, project.metadata.render_resolution ?? '720p'];
  if (!isWholeVideo(project)) parts.push(`hasta ${plural(project.metadata.max_clips ?? 8, 'clip', 'clips')}`);
  return parts.join(' · ');
}

const PTE_FIELD_LABEL: Record<string, string> = {
  aspect_ratio: 'Formato',
  render_resolution: 'Resolución',
  render_layout: 'Layout',
  editing_style: 'Estilo',
  max_clips: 'Cantidad de clips',
  selection_criteria: 'Criterio',
  add_subtitles: 'Subtítulos',
  subtitle_color: 'Color de los subtítulos',
  subtitle_size: 'Tamaño de los subtítulos',
  subtitle_position: 'Posición de los subtítulos',
  subtitle_words_per_segment: 'Palabras por línea',
  use_facetracking: 'Seguir caras',
  speaker_tracking: 'Seguir a quien habla',
  remove_silences: 'Quitar silencios',
  use_seo: 'SEO',
  use_ai_thumbnail: 'Miniatura con IA',
  intelligence_level: 'Nivel de IA',
  brand_kit: 'Brand kit',
};

const PTE_REASON: Record<string, string> = {
  explicit: 'Ya lo habías elegido a mano, y eso gana.',
  plan: 'Tu plan no lo incluye.',
  cost: 'Cambiaría el costo que ya reservaste.',
};

function lookup(table: Record<string, string>, value: unknown): string {
  return typeof value === 'string' ? (table[value] ?? value) : String(value);
}

export interface PteLine {
  key: string;
  title: string;
  detail?: string;
}

/** What the prompt changed, in words ("Layout Fondo desenfocado", "5 clips"). */
export function pteAppliedLines(applied: Record<string, unknown>): PteLine[] {
  return Object.entries(applied).map(([field, value]): PteLine => {
    const key = field;
    switch (field) {
      case 'aspect_ratio':
        return { key, title: `Formato ${String(value)}` };
      case 'render_resolution':
        return { key, title: `Resolución ${String(value)}` };
      case 'render_layout':
        return { key, title: `Layout ${lookup(LABELS.render_layout, value)}` };
      case 'editing_style':
        return { key, title: `Estilo ${lookup(LABELS.editing_style, value)}` };
      case 'max_clips':
        return { key, title: plural(Number(value), 'clip', 'clips'), detail: 'La cantidad que pediste.' };
      case 'selection_criteria':
        return { key, title: 'Criterio para elegir', detail: `«${String(value)}»` };
      case 'subtitle_color':
        return { key, title: `Subtítulos en ${colorName(String(value))}` };
      case 'subtitle_size':
        return { key, title: `Subtítulos ${lookup(SIZE_PLURAL, value).toLowerCase()}` };
      case 'subtitle_position':
        return { key, title: `Subtítulos ${lookup(POSITION_WORD, value)}` };
      case 'intelligence_level':
        return { key, title: `Nivel de IA ${lookup(LABELS.intelligence_level, value)}` };
      default: {
        const label = PTE_FIELD_LABEL[field] ?? field;
        if (typeof value === 'boolean') return { key, title: label, detail: value ? 'Activado.' : 'Desactivado.' };
        return { key, title: `${label}: ${String(value)}` };
      }
    }
  });
}

export function pteIgnoredLines(ignored: ReadonlyArray<{ field: string; reason: string }>): PteLine[] {
  return ignored.map((item) => ({
    key: item.field,
    title: PTE_FIELD_LABEL[item.field] ?? item.field,
    detail: PTE_REASON[item.reason] ?? 'Todavía no está disponible.',
  }));
}

/* ── Clips ──────────────────────────────────────────────────────────────── */

export type ClipGroup = 'ready' | 'published' | 'scheduled' | 'rendering' | 'failed' | 'draft';

export interface ClipDisplay {
  group: ClipGroup;
  label: string;
  tone: StatusTone;
  /** Publication states carry an icon instead of the dot. */
  icon?: IconName;
  /** Where it is published or about to be. */
  where: { platform: Platform; publishAt: string } | null;
  /** The rendered file exists: it can be downloaded and published. */
  ready: boolean;
}

/** State of a clip card: its render status plus where it is published or scheduled. */
/**
 * A `draft` clip that is not a suggestion: a paid render of it is queued and no worker took it yet
 * (the backend puts the clip back to `draft` when a render, a retry or a rerender is reserved).
 * `render_queued` says so; without it, a draft of a project that is rendering is one.
 */
export function isRenderQueued(clip: Pick<Clip, 'status' | 'render_queued'>, projectStatus?: Project['status']): boolean {
  return clip.status === 'draft' && (clip.render_queued === true || projectStatus === 'rendering');
}

/** `projectStatus`: the status of the clip's project, to tell a queued render from a suggestion. */
export function describeClip(clip: Pick<Clip, 'status' | 'posts' | 'render_queued'>, projectStatus?: Project['status']): ClipDisplay {
  const posts = clip.posts ?? [];
  const ready = clip.status === 'completed' || clip.status === 'published';
  if (clip.status === 'rendering' || isRenderQueued(clip, projectStatus)) return { group: 'rendering', label: 'Renderizando', tone: 'info', where: null, ready };
  if (clip.status === 'failed') return { group: 'failed', label: 'Error', tone: 'err', where: null, ready };
  if (clip.status === 'draft') return { group: 'draft', label: 'Sugerido', tone: 'neutral', where: null, ready };
  const published = posts.filter((post) => post.status === 'PUBLISHED').sort((a, b) => b.publish_at.localeCompare(a.publish_at))[0];
  if (published || clip.status === 'published') {
    return {
      group: 'published',
      label: 'Publicado',
      tone: 'ok',
      icon: 'pub-published',
      where: published ? { platform: published.platform, publishAt: published.publish_at } : null,
      ready,
    };
  }
  const scheduled = posts
    .filter((post) => post.status === 'SCHEDULED' || post.status === 'QUEUED' || post.status === 'PROCESSING')
    .sort((a, b) => a.publish_at.localeCompare(b.publish_at))[0];
  if (scheduled) {
    return { group: 'scheduled', label: 'Programado', tone: 'info', icon: 'pub-scheduled', where: { platform: scheduled.platform, publishAt: scheduled.publish_at }, ready };
  }
  return { group: 'ready', label: 'Listo', tone: 'ok', where: null, ready };
}

export type ClipFilter = 'all' | ClipGroup;

export function clipCounts(clips: ReadonlyArray<Pick<Clip, 'status' | 'posts' | 'render_queued'>>, projectStatus?: Project['status']): Record<ClipFilter, number> {
  const counts: Record<ClipFilter, number> = { all: clips.length, ready: 0, published: 0, scheduled: 0, rendering: 0, failed: 0, draft: 0 };
  for (const clip of clips) counts[describeClip(clip, projectStatus).group] += 1;
  return counts;
}

export function clipDuration(clip: Pick<Clip, 'start_time' | 'end_time'>): number {
  return Math.max(0, clip.end_time - clip.start_time);
}

/** Best score first, as the artboards order them. */
export function sortClips<T extends Pick<Clip, 'virality_score' | 'start_time'>>(clips: ReadonlyArray<T>): T[] {
  return [...clips].sort((a, b) => b.virality_score - a.virality_score || a.start_time - b.start_time);
}

const SHORT_MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'] as const;
const SHORT_DAYS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'] as const;

/** "23 nov, 19:30": the year is left out on the cards. */
export function shortDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.getDate()} ${SHORT_MONTHS[date.getMonth()]}, ${formatTime(date)}`;
}

/** Why a clip cannot get a free preview (PTE-03), or null when it can. */
export function previewBlockedReason(project: Pick<Project, 'project_type' | 'metadata' | 'source_file'>): string | null {
  if (isWholeVideo(project)) return 'El video completo no tiene vista previa: el render sale enseguida';
  if (!project.source_file) return 'La vista previa necesita el archivo original del video';
  return null;
}

/* ── Transcript ─────────────────────────────────────────────────────────── */

export interface TranscriptGroup {
  /** Clip this stretch became, or null for plain text. */
  clip: Clip | null;
  /** Indexes into the lines array. */
  from: number;
  to: number;
}

export function transcriptLines(project: Pick<Project, 'transcript_data'>): TranscriptLine[] {
  return groupTranscript(project.transcript_data ?? []);
}

export function wordCount(project: Pick<Project, 'transcript_data'>): number {
  return (project.transcript_data ?? []).reduce((sum, item) => sum + item.text.split(/\s+/).filter(Boolean).length, 0);
}

/** Splits the lines into stretches: the ones that became a clip and the text between them. */
export function groupLinesByClip(lines: ReadonlyArray<TranscriptLine>, clips: ReadonlyArray<Clip>): TranscriptGroup[] {
  const ordered = [...clips].sort((a, b) => a.start_time - b.start_time);
  const groups: TranscriptGroup[] = [];
  lines.forEach((line, index) => {
    const middle = (line.start + line.end) / 2;
    const clip = ordered.find((item) => middle >= item.start_time && middle <= item.end_time) ?? null;
    const last = groups[groups.length - 1];
    if (last && (last.clip?.id ?? null) === (clip?.id ?? null)) last.to = index;
    else groups.push({ clip, from: index, to: index });
  });
  return groups;
}

/** Lowercase without accents, one character per character, so match positions map back to the original text. */
export function foldText(text: string): string {
  let out = '';
  for (const char of text) {
    const base = char.normalize('NFD').replace(/[̀-ͯ]/g, '');
    out += (base.length === char.length ? base : char).toLowerCase().padEnd(char.length, ' ').slice(0, char.length);
  }
  return out;
}

export interface TranscriptMatch {
  line: number;
  start: number;
  end: number;
}

export function findMatches(lines: ReadonlyArray<TranscriptLine>, query: string): TranscriptMatch[] {
  const needle = foldText(query.trim());
  if (needle.length < 2) return [];
  const matches: TranscriptMatch[] = [];
  lines.forEach((line, index) => {
    const haystack = foldText(line.text);
    let at = haystack.indexOf(needle);
    while (at !== -1) {
      matches.push({ line: index, start: at, end: at + needle.length });
      at = haystack.indexOf(needle, at + needle.length);
    }
  });
  return matches;
}

/** Ticks of the "Dónde están los clips" ruler: about six, on round minutes. */
export function rulerTicks(durationSeconds: number): number[] {
  if (durationSeconds <= 0) return [0];
  const steps = [15, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 3600];
  const step = steps.find((value) => durationSeconds / value <= 6) ?? 3600;
  const ticks: number[] = [];
  for (let at = 0; at < durationSeconds - step * 0.4; at += step) ticks.push(at);
  ticks.push(durationSeconds);
  return ticks;
}

/* ── Activity ───────────────────────────────────────────────────────────── */

export interface ActivityDay {
  key: string;
  label: string;
  items: ActivityItem[];
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

/** Groups by local day, newest first: "Hoy · mar 24 nov", "Ayer · lun 23 nov", "vie 20 nov 2026". */
export function groupActivityByDay(items: ReadonlyArray<ActivityItem>, now: number = Date.now()): ActivityDay[] {
  const today = new Date(now);
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const days: ActivityDay[] = [];
  for (const item of [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))) {
    const date = new Date(item.created_at);
    const key = dayKey(date);
    let day = days[days.length - 1];
    if (!day || day.key !== key) {
      const base = `${SHORT_DAYS[date.getDay()]} ${date.getDate()} ${SHORT_MONTHS[date.getMonth()]}`;
      const label = key === dayKey(today) ? `Hoy · ${base}` : key === dayKey(yesterday) ? `Ayer · ${base}` : `${base} ${date.getFullYear()}`;
      day = { key, label, items: [] };
      days.push(day);
    }
    day.items.push(item);
  }
  return days;
}

export function activityLink(item: Pick<ActivityItem, 'type'>, project: Pick<Project, 'id' | 'project_type' | 'metadata' | 'status'>): { href: string; label: string } | null {
  const base = `/proyectos/${project.id}`;
  const single = isWholeVideo(project);
  switch (item.type) {
    case 'project_created':
    case 'prompt_applied':
      return { href: base, label: 'Ver la configuración' };
    case 'review_ready':
      return project.status === 'awaiting_approval' ? { href: `${base}/revision`, label: 'Revisar clips' } : null;
    case 'render_finished':
    case 'clip_retried':
      return single ? { href: `${base}/resultado`, label: 'Ver resultado' } : { href: `${base}/clips`, label: 'Ver clips' };
    case 'post_scheduled':
      return { href: '/publicaciones', label: 'Ver en el calendario' };
    case 'post_published':
    case 'post_failed':
      return { href: '/publicaciones', label: 'Ver publicación' };
    case 'project_failed':
      return { href: base, label: 'Ver qué pasó' };
    default:
      return null;
  }
}

export const MOVEMENT_KIND: Partial<Record<ActivityItem['type'], { label: string; tone: StatusTone; hint: string }>> = {
  tokens_reserved: { label: 'Reservado', tone: 'warn', hint: 'Queda apartado del saldo hasta que termine el trabajo.' },
  tokens_charged: { label: 'Cobrado', tone: 'neutral', hint: 'El trabajo terminó y se descontó del saldo.' },
  tokens_refunded: { label: 'Devuelto', tone: 'ok', hint: 'El trabajo falló y los tokens volvieron al saldo.' },
};

export function tokenMovements(items: ReadonlyArray<ActivityItem>): ActivityItem[] {
  return items.filter((item) => item.amount !== null && MOVEMENT_KIND[item.type]);
}

/**
 * What came back to the balance when the project failed, read from its activity: the refunds made after
 * its last reservation or charge. For a backend that does not send `cost` (gap 4), where the activity is
 * the only place that says it. `null`: the last movement is still a reservation, so the refund has not
 * been written yet (it comes right after the failure).
 */
export function refundAfterFailure(items: ReadonlyArray<ActivityItem>): number | null {
  const newestFirst = tokenMovements(items).sort((a, b) => b.created_at.localeCompare(a.created_at));
  let refunded = 0;
  for (const item of newestFirst) {
    if (item.type === 'tokens_refunded') refunded += Math.abs(item.amount ?? 0);
    else if (refunded === 0 && item.type === 'tokens_reserved') return null;
    else break;
  }
  return refunded;
}

export interface CostExplanation {
  lines: Array<{ key: string; label: string; note?: string; value: string }>;
  totalLabel: string;
  total: string;
  estimate: CostEstimate | null;
}

/** "Cómo se calculó": the formula of PricingEngine with the project's numbers. The total is what the backend charged. */
export function explainCost(
  project: Pick<Project, 'cost' | 'video_url' | 'metadata' | 'duration_seconds'>,
  pricing: Pricing | null | undefined,
  plan: Pick<Plan, 'base_discount_rate' | 'name'> | null,
): CostExplanation | null {
  const cost = project.cost;
  if (!cost) return null;
  const totalLabel = cost.state === 'refunded' ? 'Total devuelto' : cost.state === 'charged' ? 'Total cobrado' : 'Total reservado';
  const total = formatTokens(cost.amount);
  if (!pricing) return { lines: [], totalLabel, total, estimate: null };
  const duration = Math.floor(project.metadata.priced_duration_s ?? projectDuration(project) ?? 0);
  const resolution = project.metadata.render_resolution ?? '720p';
  const useSeo = Boolean(project.metadata.use_seo);
  const estimate = estimateCost({ durationSeconds: duration, resolution, useSeo, source: project.video_url ? 'link' : 'file' }, pricing, plan);
  const lines: CostExplanation['lines'] = [];
  if (project.video_url) {
    lines.push({ key: 'link', label: 'Proyecto desde un link', note: 'Precio fijo, siempre en 720p', value: formatNumber(pricing.link_flat_cost) });
    if (useSeo) lines.push({ key: 'seo', label: 'SEO', value: `+${formatNumber(estimate.addOns)}` });
  } else {
    const perSecond = duration * pricing.base_cost_per_second_720p;
    lines.push({
      key: 'duration',
      label: 'Duración',
      note: `${formatDuration(duration)} = ${formatNumber(duration, 0)} s × ${formatNumber(pricing.base_cost_per_second_720p)}`,
      value: formatNumber(perSecond),
    });
    lines.push({ key: 'resolution', label: `Resolución ${resolution}`, note: `× ${formatNumber(estimate.multiplier)}`, value: formatNumber(estimate.base) });
    if (useSeo) lines.push({ key: 'seo', label: 'SEO', value: `+${formatNumber(estimate.addOns)}` });
    if (estimate.discountRate > 0) {
      lines.push({
        key: 'discount',
        label: plan?.name ? `Descuento del plan ${plan.name}` : 'Descuento del plan',
        note: `${formatNumber(estimate.discountRate * 100)} % de ${formatNumber(estimate.base + estimate.addOns)}`,
        value: `−${formatNumber(estimate.discount)}`,
      });
    }
  }
  return { lines, totalLabel, total, estimate };
}

/** Re-exported so screens import the status helper from one place. */
export { describeProjectStatus };
