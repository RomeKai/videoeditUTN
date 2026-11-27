import { describe, expect, it } from 'vitest';
import type { Pricing } from '@/api/contracts/billing';
import type { Project } from '@/api/contracts/projects';
import { ApiError, NetworkError } from '@/api/errors';
import { transcriptToSrt, transcriptToTxt } from './download';
import { approveErrorKind, errorMessage, isModerationError, pipelineErrorMessage, pipelineErrorTitle, planLimitKind, uploadErrorCode, uploadErrorMessage } from './error-messages';
import { formatBytes, formatDateTime, formatDuration, formatMinutes, formatNumber, formatPercent, formatRelative, formatTimecode, formatTokens, formatUsd, initials, plural } from './format';
import { can } from './permissions';
import { estimateCost, firstPlanWith, planAllowsResolution, secondsForTokens } from './pricing';
import { describeProjectStatus, projectStatusGroup, transactionKind } from './project-status';

describe('formatos (es-AR)', () => {
  it('números y tokens: miles con punto, decimales con coma, sin decimales si es entero', () => {
    expect(formatNumber(1404.9)).toBe('1.404,90');
    expect(formatNumber('4860.0000000000')).toBe('4.860');
    expect(formatNumber(-763.2)).toBe('−763,20');
    expect(formatTokens(120)).toBe('120 tokens');
    expect(formatTokens(12.5)).toBe('12,50 tokens');
    expect(formatTokens(1)).toBe('1 token');
    expect(formatTokens(1755, { signed: true, unit: false })).toBe('+1.755');
    expect(formatPercent(0.1)).toBe('10 %');
    expect(formatUsd(49)).toBe('USD 49');
  });

  it('duraciones y tiempos', () => {
    expect(formatDuration(3932)).toBe('1:05:32');
    expect(formatDuration(41)).toBe('0:41');
    expect(formatDuration(null)).toBe('0:00');
    expect(formatTimecode(211)).toBe('03:31');
    expect(formatTimecode(3700)).toBe('1:01:40');
    expect(formatTimecode(211.5, { tenths: true })).toBe('03:31.5');
    expect(formatMinutes(1800)).toBe('30 min');
    expect(formatMinutes(11100)).toBe('3 h 5 min');
    expect(formatBytes(1536)).toBe('1,5 KB');
  });

  it('fechas: «24 nov 2026, 14:30» y relativas', () => {
    const date = new Date(2026, 10, 24, 14, 30);
    expect(formatDateTime(date)).toBe('24 nov 2026, 14:30');
    expect(formatDateTime('no es fecha')).toBe('—');
    const now = date.getTime();
    expect(formatRelative(now - 30_000, now)).toBe('recién');
    expect(formatRelative(now - 12 * 60_000, now)).toBe('hace 12 min');
    expect(formatRelative(now - 3 * 3600_000, now)).toBe('hace 3 h');
    expect(formatRelative(now - 26 * 3600_000, now)).toBe('ayer');
    expect(formatRelative(now - 3 * 86400_000, now)).toBe('hace 3 días');
    expect(formatRelative(now - 30 * 86400_000, now)).toBe('25 oct 2026');
    expect(formatRelative(now + 2 * 86400_000, now)).toBe('en 2 días');
  });

  it('iniciales y plurales', () => {
    expect(initials('Lucía Fernández')).toBe('LF');
    expect(initials('Estudio Norte')).toBe('EN');
    expect(initials('Sofía')).toBe('SO');
    expect(plural(1, 'clip listo', 'clips listos')).toBe('1 clip listo');
    expect(plural(6, 'clip listo', 'clips listos')).toBe('6 clips listos');
  });
});

describe('permisos (tabla 1.7 del mapa)', () => {
  it('cada rol hace lo que dice la tabla', () => {
    for (const role of ['owner', 'admin', 'editor', 'viewer'] as const) {
      expect(can(role, 'project.view')).toBe(true);
      expect(can(role, 'clip.download')).toBe(true);
      expect(can(role, 'wallet.view')).toBe(true);
    }
    expect(can('viewer', 'project.create')).toBe(false);
    expect(can('viewer', 'post.publish')).toBe(false);
    expect(can('editor', 'project.create')).toBe(true);
    expect(can('editor', 'brandkit.manage')).toBe(true);
    expect(can('editor', 'social.manage')).toBe(false);
    expect(can('editor', 'members.manage')).toBe(false);
    expect(can('editor', 'billing.manage')).toBe(false);
    expect(can('admin', 'billing.manage')).toBe(true);
    expect(can('admin', 'workspace.rename')).toBe(true);
    expect(can('admin', 'workspace.delete')).toBe(false);
    expect(can('owner', 'workspace.delete')).toBe(true);
    expect(can('owner', 'workspace.transfer')).toBe(true);
    expect(can(null, 'project.view')).toBe(false);
  });

  it('el Editor borra solo sus proyectos', () => {
    expect(can('editor', 'project.delete', { userId: 'u1', ownerId: 'u1' })).toBe(true);
    expect(can('editor', 'project.delete', { userId: 'u1', ownerId: 'u2' })).toBe(false);
    expect(can('admin', 'project.delete', { userId: 'u1', ownerId: 'u2' })).toBe(true);
    expect(can('viewer', 'project.delete', { userId: 'u1', ownerId: 'u1' })).toBe(false);
  });
});

const PRICING: Pricing = {
  base_cost_per_second_720p: 0.5,
  resolution_multipliers: { '720p': 1, '1080p': 1.5, '4K': 3 },
  feature_costs: { seo_optimization: 5, ai_thumbnail: 15 },
  link_flat_cost: 10,
  min_cost: 0.01,
  token_price_usd: 0.01,
  single_edit_max_duration_seconds: 600,
  upload_max_duration_seconds: 10800,
  upload_max_size_bytes: 524288000,
  upload_extensions: ['.mp4'],
  selection_criteria_max_chars: 500,
  prompt_max_chars: 1000,
};

describe('estimado de costo (misma fórmula que PricingEngine)', () => {
  it('base × resolución + extras − descuento, a 2 decimales', () => {
    // The example of dialog D1: 260 s at 720p with the Pro discount.
    expect(estimateCost({ durationSeconds: 260, resolution: '720p' }, PRICING, { base_discount_rate: 0.1 })).toMatchObject({ base: 130, discount: 13, total: 117 });
    // 30 min at 720p without a plan ≈ 900 tokens (pricing page).
    expect(estimateCost({ durationSeconds: 1800, resolution: '720p' }, PRICING, null).total).toBe(900);
    expect(estimateCost({ durationSeconds: 1124, resolution: '1080p', useSeo: true }, PRICING, { base_discount_rate: 0.1 }).total).toBe(763.2);
    expect(estimateCost({ durationSeconds: 100, resolution: '4K' }, PRICING, null)).toMatchObject({ multiplier: 3, total: 150 });
    expect(estimateCost({ durationSeconds: 0, resolution: '720p' }, PRICING, null).total).toBe(0.01);
  });

  it('los links tienen precio fijo, más el SEO con el descuento del plan', () => {
    expect(estimateCost({ durationSeconds: 9999, resolution: '720p', source: 'link' }, PRICING, { base_discount_rate: 0.1 }).total).toBe(10);
    expect(estimateCost({ durationSeconds: 0, resolution: '720p', source: 'link', useSeo: true }, PRICING, { base_discount_rate: 0.1 }).total).toBe(14.5);
  });

  it('plan y resolución', () => {
    expect(planAllowsResolution({ max_resolution: '1080p' }, '4K')).toBe(false);
    expect(planAllowsResolution({ max_resolution: '1080p' }, '1080p')).toBe(true);
    const plans = [
      { name: 'Pro', sort_order: 3, seo: true },
      { name: 'Gratis', sort_order: 1, seo: false },
      { name: 'Creador', sort_order: 2, seo: true },
    ];
    expect(firstPlanWith(plans, (plan) => plan.seo)?.name).toBe('Creador');
    expect(secondsForTokens(1000, PRICING, { base_discount_rate: 0.1 })).toBe(2222);
  });
});

function project(overrides: Partial<Project>): Parameters<typeof describeProjectStatus>[0] {
  return { status: 'uploaded', pipeline_stage: 'uploaded', pipeline_stage_status: {}, pipeline_error_code: '', clips: [], project_type: 'repurpose', ...overrides };
}
const clip = (status: Project['clips'][number]['status']) => ({ status }) as Project['clips'][number];

describe('estado del proyecto (sección 3 del mapa)', () => {
  it('cada condición da su etiqueta, color y texto', () => {
    expect(describeProjectStatus(project({}))).toMatchObject({ label: 'En cola', tone: 'neutral', text: 'Tu video está en la fila para procesarse.' });
    expect(describeProjectStatus(project({ status: 'ingesting', pipeline_stage: 'uploaded' }))).toMatchObject({ label: 'Preparando el video', tone: 'info', text: 'Paso 1 de 4' });
    expect(describeProjectStatus(project({ status: 'ingesting', pipeline_stage: 'audio_extracted' }))).toMatchObject({ label: 'Transcribiendo', text: 'Paso 2 de 4' });
    expect(describeProjectStatus(project({ status: 'ingesting', pipeline_stage: 'transcribed' }))).toMatchObject({ label: 'Revisando el contenido', text: 'Paso 3 de 4' });
    expect(describeProjectStatus(project({ status: 'ingesting', pipeline_stage: 'moderated' }))).toMatchObject({ label: 'Eligiendo los mejores momentos', step: { current: 4, total: 4 } });
    expect(describeProjectStatus(project({ status: 'ingesting', pipeline_stage: 'audio_extracted', pipeline_stage_status: { transcribed: 'pending_capacity' } }))).toMatchObject({
      label: 'En espera',
      tone: 'warn',
      text: 'Hay mucha demanda. Tu video sigue en la fila y arranca solo; no tenés que hacer nada.',
    });
    expect(describeProjectStatus(project({ status: 'awaiting_approval', pipeline_stage: 'clips_selected', clips: [clip('draft'), clip('draft')] }))).toMatchObject({
      label: 'Listo para revisar',
      tone: 'accent',
      text: 'Encontramos 2 clips.',
      action: 'review',
      inProgress: false,
    });
    // clips_selected while dispatching the render already reads "Renderizando".
    expect(describeProjectStatus(project({ status: 'ingesting', pipeline_stage: 'clips_selected' })).label).toBe('Renderizando');
    expect(describeProjectStatus(project({ status: 'rendering', clips: [clip('completed'), clip('rendering'), clip('rendering')] }))).toMatchObject({ label: 'Renderizando', text: '1 de 3 clips listos', inProgress: true });
    expect(describeProjectStatus(project({ status: 'completed', clips: [clip('completed'), clip('published')] }))).toMatchObject({ label: 'Listo', tone: 'ok', text: '2 clips listos.', action: 'clips' });
    expect(describeProjectStatus(project({ status: 'partial', clips: [clip('completed'), clip('failed')] }))).toMatchObject({ label: 'Listo con errores', tone: 'warn', text: '1 clip listo y 1 no se pudo generar.' });
    expect(describeProjectStatus(project({ status: 'completed', project_type: 'single_edit', clips: [clip('completed')] }))).toMatchObject({ text: 'Tu video está listo.', action: 'result' });
  });

  it('un error muestra el mensaje del código y la devolución', () => {
    const view = describeProjectStatus(project({ status: 'failed', pipeline_error_code: 'AINoSpeechDetectedError', cost: { amount: 1755, state: 'refunded', transaction_id: null } }));
    expect(view).toMatchObject({ label: 'Error', tone: 'err', action: 'recreate', shortText: 'Te devolvimos 1.755 tokens' });
    expect(view.text).toBe('No encontramos voz en el video. OneCreator elige clips a partir de lo que se dice. Te devolvimos 1.755 tokens.');
  });

  it('grupos del filtro y tipo de movimiento', () => {
    expect(projectStatusGroup(project({ status: 'ingesting' }))).toBe('in_progress');
    expect(projectStatusGroup(project({ status: 'partial' }))).toBe('ready');
    expect(projectStatusGroup(project({ status: 'failed' }))).toBe('failed');
    expect(transactionKind('DEPOSIT', 'COMPLETED')).toBe('purchase');
    expect(transactionKind('SPEND', 'RESERVED')).toBe('reserved');
    expect(transactionKind('SPEND', 'COMPLETED')).toBe('charged');
    expect(transactionKind('REFUND', 'COMPLETED')).toBe('refund');
    expect(transactionKind('SPEND', 'FAILED')).toBe('released');
    expect(transactionKind('DEPOSIT', 'PENDING')).toBe('pending');
  });
});

describe('mensajes de error (sección 4 del mapa)', () => {
  it('traduce pipeline_error_code y cae al genérico', () => {
    expect(pipelineErrorMessage('UnsafeContentError')).toMatch(/no cumple nuestras políticas/);
    // The moderation service failed (invalid key, quota): it is ours, and the video is not blamed for it.
    expect(pipelineErrorMessage('ModerationBlockedError')).toBe('Algo falló de nuestro lado. Ya te devolvimos los tokens; podés intentarlo de nuevo.');
    expect(pipelineErrorTitle('ModerationBlockedError')).toBe('Algo falló de nuestro lado');
    expect(isModerationError('ModerationBlockedError')).toBe(false);
    expect(pipelineErrorMessage('CapacityTimeoutError')).toMatch(/mucha demanda/);
    expect(pipelineErrorMessage('AIEmptySelectionError')).toMatch(/No encontramos momentos/);
    expect(pipelineErrorMessage('SoftTimeLimitExceeded')).toMatch(/tardó demasiado/);
    expect(pipelineErrorMessage('IngestionNotQueued')).toBe('No pudimos empezar a procesar tu video y no se cobró nada. Creá el proyecto de nuevo en unos minutos.');
    expect(pipelineErrorTitle('IngestionNotQueued')).toBe('No pudimos empezar a procesar el video');
    // recover_stuck_renders failed it and returned the reservation: it can be tried again.
    expect(pipelineErrorMessage('IngestionStuck')).toBe('El proceso se trabó y te devolvimos los tokens. Podés intentarlo de nuevo.');
    expect(pipelineErrorTitle('IngestionStuck')).toBe('El proceso se trabó');
    // Link projects: what the download refused, in the user's words (only the four providers the backend allows).
    expect(pipelineErrorMessage('UnsupportedLinkError')).toMatch(/un solo video público de YouTube, Vimeo, Twitch o Google Drive/);
    expect(pipelineErrorTitle('UnsupportedLinkError')).toBe('No pudimos usar este link');
    // Over the plan's limit: changing plan helps. Over the app's 3 h: it does not, so it is not offered.
    expect(pipelineErrorMessage('LinkTooLongError')).toBe('El video del link dura más de lo que permite tu plan. Probá con uno más corto o cambiá de plan.');
    expect(pipelineErrorTitle('LinkTooLongError')).toBe('El video del link es muy largo para tu plan');
    expect(pipelineErrorMessage('LinkTooLongForAppError')).toBe('El video del link dura más de 3 horas, el máximo que acepta OneCreator. Probá con uno más corto.');
    expect(pipelineErrorMessage('LinkTooLongForAppError')).not.toMatch(/plan/i);
    expect(pipelineErrorTitle('LinkTooLongForAppError')).toBe('El video del link dura más de 3 horas');
    expect(pipelineErrorMessage('LinkTooLargeError')).toMatch(/pesa más de lo que podemos descargar/);
    expect(pipelineErrorTitle('LinkTooLargeError')).toBe('El video del link es muy pesado');
    for (const code of ['UnsupportedLinkError', 'LinkTooLongError', 'LinkTooLongForAppError', 'LinkTooLargeError']) expect(pipelineErrorMessage(code)).not.toBe(pipelineErrorMessage(''));
    expect(pipelineErrorMessage('AIAuthenticationError')).toBe('Algo falló de nuestro lado. Ya te devolvimos los tokens; podés intentarlo de nuevo.');
    expect(pipelineErrorMessage('CualquierOtro')).toBe(pipelineErrorMessage(''));
    expect(isModerationError('UnsafeContentError')).toBe(true);
    expect(isModerationError('AINoSpeechDetectedError')).toBe(false);
  });

  it('reconoce el código de subida por `code` o por el texto del backend de hoy', () => {
    const today = new ApiError(400, 'projects.create', { source_file: ['El video supera la duración máxima que podemos procesar (3 h).'] });
    expect(uploadErrorCode(today)).toBe('too_long');
    const proposed = new ApiError(400, 'projects.create', { source_file: [{ message: 'x', code: 'too_small' }] });
    expect(uploadErrorCode(proposed)).toBe('too_small');
    expect(uploadErrorCode(new ApiError(400, 'projects.create', { source_file: ['El archivo no es un video válido. Formatos admitidos: MP4, MOV y AVI.'] }))).toBe('not_a_video');
    expect(uploadErrorCode(new ApiError(400, 'projects.create', { title: ['Muy largo'] }))).toBeNull();
    expect(uploadErrorMessage('too_long', { maxSeconds: 1800 })).toBe('El video dura más de lo que permite tu plan (máx. 30 min).');
    expect(uploadErrorMessage('no_duration')).toBe(uploadErrorMessage('unreadable'));
  });

  it('distingue el límite del plan por `code` o por el mensaje de PricingEngine', () => {
    expect(planLimitKind(new ApiError(403, 'x', { error: 'Video duration (4000s) exceeds your plan limit (600s).' }))).toBe('duration');
    expect(planLimitKind(new ApiError(403, 'x', { error: "Your plan 'Gratis' does not support 4K rendering." }))).toBe('resolution');
    expect(planLimitKind(new ApiError(403, 'x', { error: 'SEO Optimization is not allowed in your current plan.' }))).toBe('seo');
    expect(planLimitKind(new ApiError(403, 'x', { error: 'x', code: 'scheduling' }))).toBe('scheduling');
    expect(planLimitKind(new ApiError(403, 'x', { code: 'seats_exceeded' }))).toBe('seats');
  });

  it('da una frase para cualquier error sin filtrar textos internos', () => {
    expect(errorMessage(new NetworkError('x'))).toBe('No pudimos conectar');
    expect(errorMessage(new ApiError(500, 'x', { detail: 'Traceback (most recent call last)…' }))).toBe('Algo falló de nuestro lado. Probá de nuevo.');
    // Payments without a provider (billing.createPurchase): the backend's sentence, not "algo falló".
    expect(errorMessage(new ApiError(501, 'x', { error: 'Los pagos todavía no están habilitados.', code: 'payments_unavailable' }))).toBe('Los pagos todavía no están habilitados.');
    expect(errorMessage(new ApiError(501, 'x', { detail: 'Not implemented' }))).toBe('Algo falló de nuestro lado. Probá de nuevo.');
    expect(errorMessage(new ApiError(403, 'x', { detail: 'No', code: 'permission_denied' }))).toBe('No tenés acceso a esto. Pedile a un admin del workspace.');
    expect(errorMessage(new ApiError(400, 'x', { error: 'El proyecto ya se está renderizando.' }))).toBe('El proyecto ya se está renderizando.');
    // A role 403 of the backend carries its own sentence, and it is never a plan limit (dialog D3).
    const lector = new ApiError(403, 'projects.create', { detail: 'Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin.' });
    expect(lector.isPlanLimit).toBe(false);
    expect(errorMessage(lector)).toBe('Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin.');
    expect(errorMessage(new ApiError(403, 'projects.delete', { detail: 'Como Editor solo podés borrar los proyectos que subiste vos.' }))).toBe('Como Editor solo podés borrar los proyectos que subiste vos.');
    expect(errorMessage(new ApiError(403, 'x', { detail: 'Solo el dueño del espacio puede hacer esto.' }))).toBe('Solo el dueño del espacio puede hacer esto.');
    // Approving a segment over the render's limit: the server's sentence says what to do.
    const tooLong = 'Los segmentos aprobados son inválidos: cada segmento puede durar hasta 1800 segundos. Dividí los más largos en varios.';
    expect(approveErrorKind(new ApiError(400, 'projects.approvePaperEdit', { error: tooLong }))).toEqual({ kind: 'invalid_segments', message: tooLong });
    expect(approveErrorKind(new ApiError(400, 'projects.approvePaperEdit', { error: 'Los segmentos aprobados son inválidos: cada segmento debe tener inicio y fin.' }))?.message).toBe('No pudimos aprobar estos clips. Revisá los recortes y probá de nuevo.');
  });
});

describe('transcripción', () => {
  const words = [
    { start: 0.4, end: 0.8, text: 'Hoy' },
    { start: 0.9, end: 1.3, text: 'arrancamos.' },
    { start: 2.5, end: 2.9, text: 'Vamos' },
    { start: 3.0, end: 3.4, text: 'bien.' },
  ];
  it('arma el .txt por oración y el .srt con tiempos', () => {
    expect(transcriptToTxt(words)).toBe('Hoy arrancamos.\nVamos bien.');
    expect(transcriptToSrt(words)).toBe('1\n00:00:00,400 --> 00:00:01,300\nHoy arrancamos.\n\n2\n00:00:02,500 --> 00:00:03,400\nVamos bien.\n');
  });
});

describe('layout raíz', () => {
  it('no toma valores de módulos de cliente (el script del tema viaja como texto)', async () => {
    const { readFileSync } = await import('node:fs');
    const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');
    // A value from a "use client" module makes <head> wait for the layout's JS: hydration error #418.
    expect(read('./theme-script.ts')).not.toMatch(/^\s*['"]use client['"]/m);
    const layout = read('../app/layout.tsx');
    expect(layout).toMatch(/import \{ THEME_INIT_SCRIPT \} from '@\/lib\/theme-script'/);
    expect(layout).not.toMatch(/^import .* from '@\/lib\/theme'/m);
  });
});
