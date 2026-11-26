import type { Clip, ClipStatus, PipelineStage, PostStatus, Project } from '@/api/contracts/projects';
import type { TransactionKind } from '@/api/contracts/billing';
import type { IconName } from '@/ui/Icon';
import { formatTokens, plural } from './format';
import { pipelineErrorMessage } from './error-messages';

export type StatusTone = 'neutral' | 'ok' | 'warn' | 'err' | 'info' | 'accent';

export type ProjectStatusKey =
  | 'queued'
  | 'preparing'
  | 'transcribing'
  | 'moderating'
  | 'selecting'
  | 'waiting'
  | 'to_review'
  | 'rendering'
  | 'ready'
  | 'partial'
  | 'failed';

export interface ProjectStatusView {
  key: ProjectStatusKey;
  /** Exact label of map §3. Never abbreviated. */
  label: string;
  tone: StatusTone;
  /** Full text under the label (project screen, tooltip of the list). */
  text: string;
  /** Short text for the one-line state of the projects list (map 9.1.3). */
  shortText: string;
  /** "Paso N de 4" while ingesting. Rendering is not counted. */
  step: { current: number; total: 4 } | null;
  /** Primary action of the header. */
  action: 'review' | 'clips' | 'result' | 'recreate' | null;
  /** Something is running: the screen polls every 5 s. */
  inProgress: boolean;
}

type ProjectLike = Pick<
  Project,
  'status' | 'pipeline_stage' | 'pipeline_stage_status' | 'pipeline_error_code' | 'clips' | 'project_type'
> & { cost?: Project['cost'] };

const INGEST_STEPS: Partial<Record<PipelineStage, { key: ProjectStatusKey; label: string; step: number }>> = {
  uploaded: { key: 'preparing', label: 'Preparando el video', step: 1 },
  audio_extracted: { key: 'transcribing', label: 'Transcribiendo', step: 2 },
  transcribed: { key: 'moderating', label: 'Revisando el contenido', step: 3 },
  moderated: { key: 'selecting', label: 'Eligiendo los mejores momentos', step: 4 },
};

const READY_CLIP: ClipStatus[] = ['completed', 'published'];

export function countClips(clips: ReadonlyArray<Pick<Clip, 'status'>>): { total: number; ready: number; failed: number } {
  return {
    total: clips.length,
    ready: clips.filter((clip) => READY_CLIP.includes(clip.status)).length,
    failed: clips.filter((clip) => clip.status === 'failed').length,
  };
}

/** The refund sentence of a failed project, or '' when nothing was refunded. */
export function refundText(project: { cost?: Project['cost'] }): string {
  const cost = project.cost;
  return cost && cost.state === 'refunded' ? `Te devolvimos ${formatTokens(cost.amount)}.` : '';
}

/**
 * What the user sees for a project (map §3), from `status`, `pipeline_stage`
 * (last finished stage) and `pipeline_stage_status`.
 * A project that shows "Listo para revisar" never goes back; internal retries keep the same label.
 */
export function describeProjectStatus(project: ProjectLike): ProjectStatusView {
  const { status, pipeline_stage: stage } = project;
  const clips = countClips(project.clips);
  const single = project.project_type === 'single_edit';
  const base = { step: null, action: null, inProgress: false } as const;

  if (status === 'failed') {
    const refund = refundText(project);
    return {
      ...base,
      key: 'failed',
      label: 'Error',
      tone: 'err',
      text: [pipelineErrorMessage(project.pipeline_error_code), refund].filter(Boolean).join(' '),
      shortText: refund ? refund.replace(/\.$/, '') : 'No se pudo procesar',
      action: 'recreate',
    };
  }
  if (status === 'completed') {
    return {
      ...base,
      key: 'ready',
      label: 'Listo',
      tone: 'ok',
      text: single ? 'Tu video está listo.' : `${plural(clips.ready, 'clip listo', 'clips listos')}.`,
      shortText: single ? 'Video listo' : plural(clips.ready, 'clip listo', 'clips listos'),
      action: single ? 'result' : 'clips',
    };
  }
  if (status === 'partial') {
    return {
      ...base,
      key: 'partial',
      label: 'Listo con errores',
      tone: 'warn',
      text: single
        ? 'El video no se pudo generar.'
        : `${plural(clips.ready, 'clip listo', 'clips listos')} y ${clips.failed} no se ${clips.failed === 1 ? 'pudo' : 'pudieron'} generar.`,
      shortText: single ? 'No se pudo generar' : `${clips.ready} de ${clips.total} clips listos`,
      action: single ? 'result' : 'clips',
    };
  }
  if (status === 'rendering' || (status === 'ingesting' && (stage === 'clips_selected' || stage === 'render_dispatched'))) {
    const progress = single ? 'Estamos editando tu video.' : `${clips.ready} de ${clips.total} clips listos`;
    return { ...base, key: 'rendering', label: 'Renderizando', tone: 'info', text: progress, shortText: progress, inProgress: true };
  }
  if (status === 'awaiting_approval') {
    return {
      ...base,
      key: 'to_review',
      label: 'Listo para revisar',
      tone: 'accent',
      text: `Encontramos ${plural(clips.total, 'clip', 'clips')}.`,
      shortText: `Encontramos ${plural(clips.total, 'clip', 'clips')}`,
      action: 'review',
    };
  }
  // uploaded or ingesting from here on.
  const waiting = Object.values(project.pipeline_stage_status ?? {}).includes('pending_capacity');
  if (waiting) {
    return {
      ...base,
      key: 'waiting',
      label: 'En espera',
      tone: 'warn',
      text: 'Hay mucha demanda. Tu video sigue en la fila y arranca solo; no tenés que hacer nada.',
      shortText: 'Hay mucha demanda. Arranca solo.',
      inProgress: true,
    };
  }
  if (status === 'uploaded') {
    return {
      ...base,
      key: 'queued',
      label: 'En cola',
      tone: 'neutral',
      text: 'Tu video está en la fila para procesarse.',
      shortText: 'En la fila para procesarse',
      inProgress: true,
    };
  }
  const ingest = INGEST_STEPS[stage] ?? INGEST_STEPS.uploaded;
  const current = ingest?.step ?? 1;
  return {
    ...base,
    key: ingest?.key ?? 'preparing',
    label: ingest?.label ?? 'Preparando el video',
    tone: 'info',
    text: `Paso ${current} de 4`,
    shortText: `Paso ${current} de 4`,
    step: { current, total: 4 },
    inProgress: true,
  };
}

/** Filter groups of the projects list (map B2). */
export function projectStatusGroup(project: ProjectLike): 'in_progress' | 'to_review' | 'ready' | 'failed' {
  const view = describeProjectStatus(project);
  if (view.key === 'to_review') return 'to_review';
  if (view.key === 'ready' || view.key === 'partial') return 'ready';
  if (view.key === 'failed') return 'failed';
  return 'in_progress';
}

export const CLIP_STATUS: Record<ClipStatus, { label: string; tone: StatusTone }> = {
  draft: { label: 'Sugerido', tone: 'neutral' },
  rendering: { label: 'Renderizando', tone: 'info' },
  completed: { label: 'Listo', tone: 'ok' },
  published: { label: 'Publicado', tone: 'ok' },
  failed: { label: 'Error', tone: 'err' },
};

/** Publication states: an icon instead of the dot (kit README). */
export const POST_STATUS: Record<PostStatus, { label: string; plural: string; tone: StatusTone; icon: IconName | null }> = {
  DRAFT: { label: 'Borrador', plural: 'Borradores', tone: 'neutral', icon: 'edit' },
  SCHEDULED: { label: 'Programado', plural: 'Programados', tone: 'info', icon: 'pub-scheduled' },
  QUEUED: { label: 'En cola', plural: 'En cola', tone: 'warn', icon: 'pub-queued' },
  PROCESSING: { label: 'Publicando', plural: 'Publicando', tone: 'info', icon: 'pub-processing' },
  PUBLISHED: { label: 'Publicado', plural: 'Publicados', tone: 'ok', icon: 'pub-published' },
  FAILED: { label: 'Error', plural: 'Con error', tone: 'err', icon: 'pub-failed' },
};

/** Movement labels (map G2). */
export const TRANSACTION_KIND: Record<TransactionKind, { label: string; tone: StatusTone }> = {
  purchase: { label: 'Compra', tone: 'ok' },
  reserved: { label: 'Reservado', tone: 'warn' },
  charged: { label: 'Cobrado', tone: 'neutral' },
  refund: { label: 'Devolución', tone: 'ok' },
  released: { label: 'Devuelto', tone: 'ok' },
  pending: { label: 'Pendiente', tone: 'neutral' },
};

/** Transaction type + status → what the table shows. Used when the backend does not send `kind`. */
export function transactionKind(type: 'DEPOSIT' | 'SPEND' | 'REFUND', status: 'PENDING' | 'RESERVED' | 'COMPLETED' | 'FAILED'): TransactionKind {
  if (status === 'PENDING') return 'pending';
  if (type === 'DEPOSIT') return 'purchase';
  if (type === 'REFUND') return 'refund';
  if (status === 'RESERVED') return 'reserved';
  if (status === 'FAILED') return 'released';
  return 'charged';
}

/** Labels of the project's options, as the creation form names them (map C1). */
export const LABELS = {
  project_type: { repurpose: 'Sacar clips', single_edit: 'Editar el video completo' },
  editing_style: { dynamic: 'Dinámico', minimalist: 'Minimalista', vlog: 'Vlog', hormozi: 'Hormozi', custom: 'Personalizado' },
  render_layout: {
    fill: 'Llenar',
    fit: 'Ajustar',
    blurred: 'Fondo desenfocado',
    split: 'Dividido',
    pip: 'PiP',
    versus: 'Versus',
    active: 'Quien habla',
    reaction: 'Reacción',
    cinematic: 'Cinemático',
    podcast: 'Podcast',
    trending: 'Trending',
  },
  intelligence_level: { fast: 'Rápido', smart: 'Inteligente' },
  subtitle_size: { small: 'Chico', medium: 'Mediano', large: 'Grande' },
  subtitle_position: { top: 'Arriba', center: 'Centro', bottom: 'Abajo' },
  position: { left: 'Izquierda', center: 'Centro', right: 'Derecha' },
  pip_corner: { 'bottom-right': 'Abajo a la derecha', 'bottom-left': 'Abajo a la izquierda', 'top-right': 'Arriba a la derecha', 'top-left': 'Arriba a la izquierda' },
  role: { owner: 'Dueño', admin: 'Admin', editor: 'Editor', viewer: 'Lector' },
} as const;
