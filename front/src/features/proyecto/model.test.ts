import { describe, expect, it } from 'vitest';
import type { ActivityItem, Clip, Project } from '@/api/contracts/projects';
import { KNOWN_PIPELINE_ERROR_CODES } from '@/api/contracts/projects';
import { seedDb } from '@/api/mocks/seed';
import { serializeProject } from '@/api/mocks/serialize';
import { describeProjectStatus } from '@/lib/project-status';
import {
  activityLink,
  allReservationsEnded,
  autoRenderNotice,
  buildConfigGroups,
  buildProcessSteps,
  buildProjectTabs,
  clipCounts,
  costLabel,
  currentTab,
  describeClip,
  explainCost,
  explainMovements,
  failedStepNumber,
  findMatches,
  foldText,
  groupActivityByDay,
  groupLinesByClip,
  headerStatusText,
  isWholeVideo,
  previewBlockedReason,
  refundAfterFailure,
  pteAppliedLines,
  pteIgnoredLines,
  rulerTicks,
  sourceInfo,
  foldedMovement,
  movementKind,
  tokenMovements,
} from './model';

const NOW = new Date('2026-11-24T18:00:00Z').getTime();
const db = seedDb({ scenario: 'todos-los-estados', projects: 'all-states' }, NOW);
const byTitle = (title: string): Project => {
  const found = db.projects.find((project) => project.title.startsWith(title));
  if (!found) throw new Error(`No hay proyecto «${title}»`);
  return serializeProject(db, found, { full: true });
};
const states = (project: Project) => buildProcessSteps(project, NOW).map((step) => `${step.key}:${step.state}`);

describe('línea de tiempo del proceso', () => {
  it('en cola: todo pendiente y el primer paso dice «En cola»', () => {
    const steps = buildProcessSteps(byTitle('En cola'), NOW);
    expect(steps.every((step) => step.state === 'pending')).toBe(true);
    expect(steps[0]?.stateLabel).toBe('En cola');
  });

  it('marca terminado lo anterior y en curso la etapa siguiente a pipeline_stage', () => {
    expect(states(byTitle('Paso 1'))).toEqual(['prepare:run', 'transcribe:pending', 'moderate:pending', 'select:pending', 'render:pending']);
    expect(states(byTitle('Clase 3'))).toEqual(['prepare:done', 'transcribe:run', 'moderate:pending', 'select:pending', 'render:pending']);
    expect(states(byTitle('Paso 4'))).toEqual(['prepare:done', 'transcribe:done', 'moderate:done', 'select:run', 'render:pending']);
  });

  it('en espera por demanda: la etapa queda en espera con el texto de la sección 3', () => {
    const steps = buildProcessSteps(byTitle('En espera en la transcripción'), NOW);
    const waiting = steps.find((step) => step.state === 'wait');
    expect(waiting?.key).toBe('transcribe');
    expect(waiting?.description).toMatch(/Hay mucha demanda/);
  });

  it('el render espera la revisión, salvo con «Renderizar sin revisar»', () => {
    const review = buildProcessSteps(byTitle('Podcast Ep. 42'), NOW).find((step) => step.key === 'render');
    expect(review).toMatchObject({ state: 'pending', gated: true });
    const dispatching = buildProcessSteps(byTitle('Despachando el render'), NOW).find((step) => step.key === 'render');
    expect(dispatching).toMatchObject({ state: 'run' });
    // Stopped at the review for lack of balance: it waits for the user again.
    const skipped = buildProcessSteps(byTitle('Sin revisar: no alcanzó'), NOW).find((step) => step.key === 'render');
    expect(skipped).toMatchObject({ state: 'pending', gated: true });
  });

  it('renderizando, listo y parcial', () => {
    expect(buildProcessSteps(byTitle('Stream Valorant'), NOW).at(-1)).toMatchObject({ state: 'run' });
    expect(states(byTitle('Vlog Mendoza')).every((value) => value.endsWith(':done'))).toBe(true);
    expect(buildProcessSteps(byTitle('Charla en la UTN'), NOW).at(-1)?.description).toMatch(/5 clips listos y 1 no se pudo generar/);
  });

  it('un proyecto fallido muestra dónde se detuvo y junta lo que no empezó', () => {
    const steps = buildProcessSteps(byTitle('Webinar de lanzamiento'), NOW);
    expect(steps.map((step) => step.state)).toEqual(['done', 'fail', 'pending']);
    expect(failedStepNumber(steps)).toBe(2);
    expect(steps[1]?.description).toBe('No encontramos voz en el audio.');
    expect(steps[2]).toMatchObject({ label: '3 pasos no llegaron a empezar', description: 'Revisar contenido, Elegir clips y Renderizar.', collapsed: true });
  });

  it('cada pipeline_error_code deja exactamente una etapa fallida', () => {
    for (const code of [...KNOWN_PIPELINE_ERROR_CODES, 'error desconocido']) {
      const steps = buildProcessSteps(byTitle(`Falló: ${code}`), NOW);
      expect(steps.filter((step) => step.state === 'fail'), code).toHaveLength(1);
    }
  });

  it('un reintento interno no se muestra: la etapa marcada failed sigue «En curso» mientras el proyecto procesa', () => {
    const retrying = {
      ...byTitle('Clase 3'),
      pipeline_stage_status: { audio_extracted: 'completed' as const, transcribed: 'failed' as const },
      pipeline_error_code: 'RetryableAIError',
    };
    const step = buildProcessSteps(retrying, NOW).find((item) => item.key === 'transcribe');
    expect(step).toMatchObject({ state: 'run', stateLabel: 'En curso', description: 'Pasando a texto todo lo que se dice.' });
    expect(buildProcessSteps(retrying, NOW).some((item) => item.state === 'fail')).toBe(false);
    // The same map on a project that did fail is a failure.
    const failed = buildProcessSteps({ ...retrying, status: 'failed' }, NOW).find((item) => item.key === 'transcribe');
    expect(failed?.state).toBe('fail');
  });

  it('la etapa en curso muestra cuándo arrancó y cuenta lo que lleva', () => {
    const started = new Date(NOW - 75_000).toISOString();
    const running = {
      ...byTitle('Clase 3'),
      stage_timings: {
        audio_extracted: { started_at: new Date(NOW - 120_000).toISOString(), finished_at: new Date(NOW - 78_000).toISOString(), duration_s: 42 },
        transcribed: { started_at: started, finished_at: null, duration_s: null },
      },
    };
    const step = buildProcessSteps(running, NOW).find((item) => item.key === 'transcribe');
    expect(step).toMatchObject({ state: 'run', startedAt: started, durationSeconds: 75 });
    expect(buildProcessSteps(running, NOW + 5_000).find((item) => item.key === 'transcribe')?.durationSeconds).toBe(80);
    // The steps that did not start show no time, even with one left by an earlier attempt.
    const stale = { ...running, stage_timings: { ...running.stage_timings, moderated: { started_at: started, finished_at: null, duration_s: null } } };
    expect(buildProcessSteps(stale, NOW).find((item) => item.key === 'moderate')).toMatchObject({ state: 'pending', startedAt: null, durationSeconds: null });
  });

  it('la etapa que falló muestra cuándo arrancó y cuánto duró hasta que el proyecto se detuvo', () => {
    const started = new Date(NOW - 600_000).toISOString();
    const failed = {
      ...byTitle('Webinar de lanzamiento'),
      stage_timings: {
        audio_extracted: { started_at: new Date(NOW - 660_000).toISOString(), finished_at: new Date(NOW - 618_000).toISOString(), duration_s: 42 },
        transcribed: { started_at: started, finished_at: new Date(NOW - 510_000).toISOString(), duration_s: null },
      },
    };
    const step = buildProcessSteps(failed, NOW).find((item) => item.state === 'fail');
    // It does not keep counting: it stopped when the project failed.
    expect(step).toMatchObject({ key: 'transcribe', startedAt: started, durationSeconds: 90 });
    expect(buildProcessSteps(failed, NOW + 60_000).find((item) => item.state === 'fail')?.durationSeconds).toBe(90);
    // Without the moment it stopped (a backend that does not know it): the start alone.
    const open = { ...failed, stage_timings: { ...failed.stage_timings, transcribed: { started_at: started, finished_at: null, duration_s: null } } };
    expect(buildProcessSteps(open, NOW).find((item) => item.state === 'fail')).toMatchObject({ startedAt: started, durationSeconds: null });
  });

  it('una ingesta trabada que el barrido dio por fallida muestra los tiempos de la etapa donde quedó', () => {
    const started = new Date(NOW - 600_000).toISOString();
    // The sweep fails the project and leaves the stage as `running` in the map.
    const swept = {
      ...byTitle('Webinar de lanzamiento'),
      pipeline_error_code: 'IngestionStuck',
      pipeline_stage_status: { audio_extracted: 'completed' as const, transcribed: 'running' as const },
      stage_timings: {
        audio_extracted: { started_at: new Date(NOW - 660_000).toISOString(), finished_at: new Date(NOW - 618_000).toISOString(), duration_s: 42 },
        transcribed: { started_at: started, finished_at: new Date(NOW - 300_000).toISOString(), duration_s: null },
      },
    };
    const step = buildProcessSteps(swept, NOW).find((item) => item.state === 'fail');
    expect(step).toMatchObject({ key: 'transcribe', startedAt: started, durationSeconds: 300 });
  });

  it('AutoRenderNotPaid: el render es lo que se detuvo, con su texto', () => {
    const steps = buildProcessSteps(byTitle('Falló: AutoRenderNotPaid'), NOW);
    expect(steps.at(-1)).toMatchObject({ key: 'render', state: 'fail', description: 'No alcanzó el saldo para renderizar los clips que faltaban.' });
  });

  it('un single_edit sin precio de video completo sigue el flujo de clips, con revisión', () => {
    const legacy = { ...byTitle('Tutorial de Notion'), metadata: {} };
    expect(isWholeVideo(legacy)).toBe(false);
    expect(isWholeVideo(byTitle('Tutorial de Notion'))).toBe(true);
    expect(buildProcessSteps(legacy, NOW).map((step) => step.key)).toContain('select');
    expect(buildProjectTabs(legacy).map((tab) => tab.key)).toContain('clips');
  });

  it('video completo: sin «Elegir clips» y sin esperar una revisión', () => {
    const steps = buildProcessSteps(byTitle('Video completo renderizando'), NOW);
    expect(steps.map((step) => step.key)).toEqual(['prepare', 'transcribe', 'moderate', 'render']);
    expect(steps.at(-1)).toMatchObject({ label: 'Editar el video', state: 'run', gated: false });
    expect(buildProcessSteps(byTitle('Video completo que no se pudo generar'), NOW).at(-1)?.state).toBe('fail');
  });
});

describe('cabecera y pestañas', () => {
  it('arma el texto de estado con el estimado, la espera o cuándo falló', () => {
    const processing = byTitle('Clase 3');
    expect(headerStatusText(processing, describeProjectStatus(processing), NOW)).toMatch(/^Paso 2 de 4 · faltan ≈ \d+ min para revisar$/);
    const waiting = byTitle('En espera en la transcripción');
    expect(headerStatusText(waiting, describeProjectStatus(waiting), NOW)).toMatch(/^En espera hace \d+ min\.$/);
    const failed = byTitle('Webinar de lanzamiento');
    expect(headerStatusText(failed, describeProjectStatus(failed), NOW)).toBe('Falló hace 2 días.');
    const ready = byTitle('Vlog Mendoza');
    expect(headerStatusText(ready, describeProjectStatus(ready), NOW)).toBe('6 clips listos.');
  });

  it('origen y costo', () => {
    expect(sourceInfo({ video_url: null }).label).toBe('Archivo');
    expect(sourceInfo({ video_url: 'https://www.youtube.com/watch?v=x' }).label).toBe('Link de YouTube');
    expect(costLabel({ amount: 1404.9, state: 'reserved', transaction_id: null })).toBe('1.404,90 tokens reservados');
    expect(costLabel({ amount: 1755, state: 'refunded', transaction_id: null })).toBe('1.755 tokens devueltos');
    expect(costLabel({ amount: 1, state: 'charged', transaction_id: null })).toBe('1 token cobrado');
  });

  it('las pestañas que todavía no tienen nada quedan deshabilitadas con el motivo', () => {
    const processing = buildProjectTabs(byTitle('Paso 1'));
    expect(processing.map((tab) => tab.label)).toEqual(['Resumen', 'Clips', 'Transcripción', 'Actividad']);
    expect(processing[1]?.disabledReason).toBe('Disponible cuando elijamos los clips');
    expect(processing[2]?.disabledReason).toBe('Disponible cuando termine de transcribir');
    const failed = buildProjectTabs(byTitle('Webinar de lanzamiento'));
    expect(failed[1]?.disabledReason).toBe('El proyecto falló antes de llegar a este paso');
    const ready = buildProjectTabs(byTitle('Vlog Mendoza'));
    expect(ready[1]).toMatchObject({ count: 6, disabledReason: undefined });
    expect(ready[2]?.disabledReason).toBeUndefined();
  });

  it('un video completo tiene Resultado en lugar de Clips', () => {
    const tabs = buildProjectTabs(byTitle('Tutorial de Notion'));
    expect(tabs.map((tab) => tab.key)).toEqual(['resumen', 'resultado', 'transcripcion', 'actividad']);
  });

  it('reconoce la pestaña por la ruta', () => {
    expect(currentTab('/proyectos/abc', 'abc')).toBe('resumen');
    expect(currentTab('/proyectos/abc/clips/', 'abc')).toBe('clips');
    expect(currentTab('/proyectos/abc/resultado', 'abc')).toBe('resultado');
    expect(currentTab('/proyectos/abc/transcripcion', 'abc')).toBe('transcripcion');
    expect(currentTab('/proyectos/abc/actividad', 'abc')).toBe('actividad');
  });
});

describe('avisos de «Renderizar sin revisar»', () => {
  it('saldo insuficiente y límite del plan detienen el proyecto en la revisión', () => {
    expect(autoRenderNotice(byTitle('Sin revisar: no alcanzó'))).toMatchObject({
      kind: 'insufficient_funds',
      title: 'No alcanzó el saldo para renderizar sin revisar',
      text: 'Revisá y aprobá los clips que quieras.',
    });
    expect(autoRenderNotice(byTitle('Sin revisar: el plan'))).toMatchObject({ kind: 'plan_limit', title: 'Tu plan no permite renderizar estos clips sin revisar' });
  });

  it('avisa la reserva mientras renderiza y nada en los demás casos', () => {
    const reserved = byTitle('Sin revisar: render reservado');
    const reservationId = reserved.metadata.auto_render_reservation_id ?? '';
    expect(autoRenderNotice(reserved, [reservationId])?.kind).toBe('reserved');
    // A later re-render keeps the id in metadata, but that reservation is closed: no notice again.
    expect(autoRenderNotice(reserved, [])).toBeNull();
    expect(autoRenderNotice(reserved, null)).toBeNull();
    expect(autoRenderNotice(byTitle('Podcast Ep. 42'))).toBeNull();
    expect(autoRenderNotice(byTitle('Stream Valorant'))).toBeNull();
    expect(autoRenderNotice(byTitle('Video completo renderizando'))).toBeNull();
  });
});

describe('configuración aplicada', () => {
  it('agrupa en Salida, Estilo e IA', () => {
    const groups = buildConfigGroups(byTitle('Vlog Mendoza'));
    expect(groups.map((group) => group.title)).toEqual(['Salida', 'Estilo', 'IA']);
    const flat = Object.fromEntries(groups.flatMap((group) => group.items.map((item) => [item.label, item.value])));
    expect(flat['Qué hacer']).toBe('Sacar clips');
    expect(flat['Subtítulos']).toBe('Medianos · abajo · amarillo · 3 palabras por línea');
    expect(flat['Renderizar sin revisar']).toBe('No: se detuvo en la revisión');
    expect(Object.keys(Object.fromEntries(buildConfigGroups(byTitle('Tutorial de Notion')).flatMap((group) => group.items.map((item) => [item.label, 1]))))).not.toContain('Clips');
  });

  it('traduce lo que el prompt aplicó y por qué ignoró el resto', () => {
    expect(pteAppliedLines({ render_layout: 'blurred', max_clips: 5, subtitle_color: '#FFFF00', use_facetracking: true }).map((line) => line.title)).toEqual([
      'Layout Fondo desenfocado',
      '5 clips',
      'Subtítulos en amarillo',
      'Seguir caras',
    ]);
    expect(pteIgnoredLines([{ field: 'render_resolution', reason: 'plan' }, { field: 'aspect_ratio', reason: 'explicit' }, { field: 'musica', reason: 'otra' }])).toEqual([
      { key: 'render_resolution', title: 'Resolución', detail: 'Tu plan no lo incluye.' },
      { key: 'aspect_ratio', title: 'Formato', detail: 'Ya lo habías elegido a mano, y eso gana.' },
      { key: 'musica', title: 'musica', detail: 'Todavía no está disponible.' },
    ]);
  });
});

describe('clips', () => {
  const clip = (status: Clip['status'], posts: Clip['posts'] = []) => ({ status, posts });
  const post = (status: NonNullable<Clip['posts']>[number]['status'], publish_at: string) => ({ id: 'p', platform: 'TIKTOK' as const, status, publish_at });

  it('el estado de la tarjeta sale del render y de las publicaciones', () => {
    expect(describeClip(clip('completed'))).toMatchObject({ group: 'ready', label: 'Listo', ready: true });
    expect(describeClip(clip('rendering'))).toMatchObject({ group: 'rendering', ready: false });
    expect(describeClip(clip('failed'))).toMatchObject({ group: 'failed', label: 'Error' });
    expect(describeClip(clip('draft'))).toMatchObject({ group: 'draft', label: 'Sugerido' });
    // A paid render puts the clip back to `draft` until a worker takes it: that is a render under way, not a suggestion.
    expect(describeClip({ ...clip('draft'), render_queued: true })).toMatchObject({ group: 'rendering', label: 'Renderizando', ready: false });
    expect(describeClip({ ...clip('draft'), render_queued: true }, 'partial')).toMatchObject({ group: 'rendering' });
    // Without the field (an older backend): a draft of a project that is rendering.
    expect(describeClip(clip('draft'), 'rendering')).toMatchObject({ group: 'rendering', label: 'Renderizando' });
    expect(describeClip(clip('draft'), 'awaiting_approval')).toMatchObject({ group: 'draft', label: 'Sugerido' });
    expect(describeClip({ ...clip('draft'), render_queued: false }, 'completed')).toMatchObject({ group: 'draft' });
    expect(clipCounts([clip('draft'), clip('completed')], 'rendering')).toMatchObject({ rendering: 1, draft: 0, ready: 1 });
    expect(describeClip(clip('completed', [post('SCHEDULED', '2026-11-26T21:00:00Z')]))).toMatchObject({ group: 'scheduled', label: 'Programado', icon: 'pub-scheduled' });
    expect(describeClip(clip('completed', [post('SCHEDULED', '2026-11-26T21:00:00Z'), post('PUBLISHED', '2026-11-23T22:30:00Z')]))).toMatchObject({
      group: 'published',
      where: { platform: 'TIKTOK', publishAt: '2026-11-23T22:30:00Z' },
    });
  });

  it('cuenta por filtro', () => {
    expect(clipCounts([clip('completed'), clip('completed', [post('PUBLISHED', '2026-11-23T22:30:00Z')]), clip('failed'), clip('rendering')])).toEqual({
      all: 4,
      ready: 1,
      published: 1,
      scheduled: 0,
      rendering: 1,
      failed: 1,
      draft: 0,
    });
  });

  it('la vista previa necesita el archivo original y no existe para el video completo', () => {
    expect(previewBlockedReason({ project_type: 'repurpose', metadata: {}, source_file: '/media/x.mp4' })).toBeNull();
    expect(previewBlockedReason({ project_type: 'repurpose', metadata: {}, source_file: null })).toMatch(/archivo original/);
    expect(previewBlockedReason({ project_type: 'single_edit', metadata: { priced_duration_s: 300 }, source_file: '/media/x.mp4' })).toMatch(/no tiene vista previa/);
  });
});

describe('transcripción', () => {
  const lines = [
    { start: 0, end: 4, text: 'Paramos a comprar empanadas en Cacheuta.' },
    { start: 5, end: 9, text: 'Las EMPANADAS de acá son de horno de barro.' },
    { start: 10, end: 14, text: 'Subimos al Aconcágua después.' },
  ];

  it('busca sin distinguir mayúsculas ni acentos y devuelve posiciones del texto original', () => {
    const matches = findMatches(lines, 'empanadas');
    expect(matches).toHaveLength(2);
    const second = matches[1];
    expect(second && lines[second.line]?.text.slice(second.start, second.end)).toBe('EMPANADAS');
    const accent = findMatches(lines, 'aconcagua')[0];
    expect(accent && lines[accent.line]?.text.slice(accent.start, accent.end)).toBe('Aconcágua');
    expect(findMatches(lines, 'mendoza')).toEqual([]);
    expect(findMatches(lines, 'e')).toEqual([]);
    expect(foldText('Ñandú ÁÉ')).toHaveLength('Ñandú ÁÉ'.length);
  });

  it('separa los tramos que se volvieron clip', () => {
    const clips = [{ id: 'c1', start_time: 4.5, end_time: 9.5 }] as Clip[];
    expect(groupLinesByClip(lines, clips).map((group) => [group.clip?.id ?? null, group.from, group.to])).toEqual([
      [null, 0, 0],
      ['c1', 1, 1],
      [null, 2, 2],
    ]);
  });

  it('la regla tiene pocas marcas y termina en la duración', () => {
    const ticks = rulerTicks(1124);
    expect(ticks[0]).toBe(0);
    expect(ticks.at(-1)).toBe(1124);
    expect(ticks.length).toBeLessThanOrEqual(8);
  });
});

describe('actividad', () => {
  const item = (id: string, type: ActivityItem['type'], created_at: string, amount: number | null = null): ActivityItem => ({
    id,
    type,
    actor: null,
    created_at,
    description: type,
    amount,
  });
  const now = new Date(2026, 10, 24, 15, 0).getTime();
  const at = (day: number, hour: number) => new Date(2026, 10, day, hour, 0).toISOString();

  it('agrupa por día, de lo más nuevo a lo más viejo', () => {
    const days = groupActivityByDay([item('a', 'project_created', at(20, 9)), item('b', 'render_finished', at(24, 11)), item('c', 'review_approved', at(23, 11)), item('d', 'review_ready', at(23, 10))], now);
    expect(days.map((day) => day.label)).toEqual(['Hoy · mar 24 nov', 'Ayer · lun 23 nov', 'vie 20 nov 2026']);
    expect(days[1]?.items.map((entry) => entry.id)).toEqual(['c', 'd']);
  });

  it('la devolución de un proyecto que falló se lee de la actividad: lo devuelto después de la última reserva o cobro', () => {
    // What the backend sends for a project that failed in the ingest: the newest first.
    expect(refundAfterFailure([item('d', 'tokens_refunded', at(24, 12), 6.3), item('c', 'project_failed', at(24, 12)), item('b', 'tokens_reserved', at(24, 11), -6.3), item('a', 'project_created', at(24, 11))])).toBe(6.3);
    // Whatever the order of the list, and only what came back after the last charge.
    expect(
      refundAfterFailure([item('a', 'tokens_refunded', at(20, 9), 40), item('b', 'tokens_reserved', at(21, 9), -18), item('c', 'tokens_charged', at(21, 10), -18), item('d', 'tokens_reserved', at(22, 9), -9), item('e', 'tokens_refunded', at(22, 10), 9)]),
    ).toBe(9);
    // Charged and nothing came back; no movements at all.
    expect(refundAfterFailure([item('b', 'tokens_charged', at(24, 12), -18), item('a', 'tokens_reserved', at(24, 11), -18)])).toBe(0);
    expect(refundAfterFailure([item('a', 'project_created', at(24, 11))])).toBe(0);
    // Still reserved: the refund is not written yet.
    expect(refundAfterFailure([item('b', 'project_failed', at(24, 12)), item('a', 'tokens_reserved', at(24, 11), -18)])).toBeNull();
  });

  it('los movimientos son los eventos con monto', () => {
    const items = [item('a', 'project_created', at(20, 9)), item('b', 'tokens_reserved', at(20, 9), -763.2), item('c', 'tokens_refunded', at(21, 9), 763.2)];
    expect(tokenMovements(items).map((entry) => entry.id)).toEqual(['b', 'c']);
  });

  it('los ítems de una reserva suman lo que se movió el saldo: solo lo que no es contexto lleva signo', () => {
    const of = (type: ActivityItem['type'], amount: number, state: ActivityItem['reservation_state']) => ({ ...item(type, type, at(24, 11), amount), reservation_state: state });
    /** What the screen shows signed: the amount of every item that is not folded. */
    const signed = (group: ActivityItem[]) => group.filter((entry) => !foldedMovement(entry)).reduce((sum, entry) => sum + (entry.amount ?? 0), 0);
    const labels = (group: ActivityItem[]) => group.map((entry) => movementKind(entry)?.label);

    // Charged in full (18): the reservation is context of the charge. The balance went down 18.
    const charged = [of('tokens_reserved', -18, 'charged'), of('tokens_charged', -18, 'charged')];
    expect(signed(charged)).toBe(-18);
    expect(labels(charged)).toEqual(['Reserva cobrada', 'Cobrado']);
    expect(charged.map((entry) => foldedMovement(entry)?.note)).toEqual(['Ya cobrada: es el mismo monto del cobro, no otro.', undefined]);

    // Charged in part (12 reserved, 8 charged): the balance went down 8, not 4.
    const partial = [of('tokens_reserved', -12, 'partially_charged'), of('tokens_charged', -8, 'partially_charged'), of('tokens_refunded', 4, 'partially_charged')];
    expect(signed(partial)).toBe(-8);
    expect(labels(partial)).toEqual(['Reserva cobrada en parte', 'Cobrado', 'No cobrado']);
    expect(partial.map((entry) => Boolean(foldedMovement(entry)))).toEqual([true, false, true]);

    // Returned (20): nothing is folded, the reservation and its refund cancel out. Never a lone +20.
    const refunded = [of('tokens_reserved', -20, 'refunded'), of('tokens_refunded', 20, 'refunded')];
    expect(signed(refunded)).toBe(0);
    expect(refunded.map((entry) => foldedMovement(entry))).toEqual([null, null]);
    expect(labels(refunded)).toEqual(['Reserva devuelta', 'Devuelto']);

    // Still reserved, or a backend that does not say: as before, signed.
    const open = [of('tokens_reserved', -18, 'reserved'), of('tokens_reserved', -18, null), item('x', 'tokens_reserved', at(24, 11), -18), item('y', 'tokens_refunded', at(24, 12), 18)];
    expect(open.map((entry) => foldedMovement(entry))).toEqual([null, null, null, null]);
    expect(labels(open)).toEqual(['Reservado', 'Reservado', 'Reservado', 'Devuelto']);
    expect(movementKind(item('c', 'project_created', at(24, 12)))).toBeNull();
  });

  it('sin costo de creación, «Cómo se calculó» suma los cobros y las reservas abiertas de sus movimientos', () => {
    const reservation = (id: string, hour: number, amount: number, state: ActivityItem['reservation_state']) => ({ ...item(id, 'tokens_reserved', at(24, hour), -amount), description: 'Reserva por el render', reservation_state: state });
    const charge = (id: string, hour: number, amount: number) => ({ ...item(id, 'tokens_charged', at(24, hour), -amount), description: 'Cobro del render' });
    // The render of the rehearsal: reserved 18 and charged 18 is ONE charge of 18, not 36.
    expect(explainMovements([charge('b', 12, 18), reservation('a', 11, 18, 'charged')])).toMatchObject({
      lines: [{ label: 'Cobro del render', value: '18' }],
      totalLabel: 'Total cobrado',
      total: '18 tokens',
    });
    // A retry still rendering adds its reservation.
    const withRetry = explainMovements([reservation('c', 13, 9, 'reserved'), charge('b', 12, 18), reservation('a', 11, 18, 'charged')]);
    expect(withRetry).toMatchObject({ totalLabel: 'Total cobrado y reservado', total: '27 tokens' });
    expect(withRetry?.lines.map((line) => [line.label, line.note, line.value])).toEqual([
      ['Cobro del render', undefined, '18'],
      ['Reserva por el render', 'Reservado, todavía sin cobrar', '9'],
    ]);
    expect(explainMovements([reservation('a', 11, 18, 'reserved')])).toMatchObject({ totalLabel: 'Total reservado', total: '18 tokens' });
    // A partial charge: what was charged, not what had been set aside.
    expect(explainMovements([item('c', 'tokens_refunded', at(24, 12), 4), charge('b', 12, 8), reservation('a', 11, 12, 'partially_charged')])).toMatchObject({ total: '8 tokens' });
    // Two reservations, one charged in part (20 → 5) and one still open (20): 25. No guessing which charge closes which.
    expect(explainMovements([reservation('d', 13, 20, 'reserved'), item('c', 'tokens_refunded', at(24, 12), 15), charge('b', 12, 5), reservation('a', 11, 20, 'partially_charged')])).toMatchObject({ total: '25 tokens' });
    // A reservation whose end is not known is not counted as open.
    expect(explainMovements([charge('b', 12, 18), reservation('a', 11, 18, undefined)])).toMatchObject({ totalLabel: 'Total cobrado', total: '18 tokens' });
    expect(explainMovements([reservation('a', 11, 18, undefined)])).toBeNull();
    expect(allReservationsEnded([reservation('a', 11, 18, undefined)])).toBe(false);
    // Everything came back, or nothing moved: nothing to explain.
    const returned = [item('b', 'tokens_refunded', at(24, 12), 18), reservation('a', 11, 18, 'refunded')];
    expect(explainMovements(returned)).toBeNull();
    expect(allReservationsEnded(returned)).toBe(true);
    expect(explainMovements([item('a', 'project_created', at(24, 11))])).toBeNull();
  });

  it('cada evento lleva a donde se puede seguir', () => {
    const project = { id: 'p1', project_type: 'repurpose' as const, metadata: {}, status: 'completed' as const };
    expect(activityLink({ type: 'render_finished' }, project)).toEqual({ href: '/proyectos/p1/clips', label: 'Ver clips' });
    expect(activityLink({ type: 'render_finished' }, { ...project, project_type: 'single_edit', metadata: { priced_duration_s: 300 } })?.href).toBe('/proyectos/p1/resultado');
    expect(activityLink({ type: 'review_ready' }, project)).toBeNull();
    expect(activityLink({ type: 'review_ready' }, { ...project, status: 'awaiting_approval' })?.href).toBe('/proyectos/p1/revision');
    expect(activityLink({ type: 'tokens_charged' }, project)).toBeNull();
  });

  it('explica el costo con la fórmula del motor de precios', () => {
    const pricing = {
      base_cost_per_second_720p: 0.5,
      resolution_multipliers: { '720p': 1, '1080p': 1.5, '4K': 3 },
      feature_costs: { seo_optimization: 5, ai_thumbnail: 10 },
      link_flat_cost: 10,
      min_cost: 0.01,
    } as Parameters<typeof explainCost>[1];
    const project = {
      cost: { amount: 763.2, state: 'charged' as const, transaction_id: null },
      video_url: null,
      duration_seconds: 1124,
      metadata: { render_resolution: '1080p' as const, use_seo: true, priced_duration_s: 1124 },
    };
    const cost = explainCost(project, pricing, { base_discount_rate: 0.1, name: 'Pro' });
    expect(cost?.lines.map((line) => [line.label, line.value])).toEqual([
      ['Duración', '562'],
      ['Resolución 1080p', '843'],
      ['SEO', '+5'],
      ['Descuento del plan Pro', '−84,80'],
    ]);
    expect(cost).toMatchObject({ totalLabel: 'Total cobrado', total: '763,20 tokens' });
    expect(explainCost({ ...project, cost: null }, pricing, null)).toBeNull();
    expect(explainCost({ ...project, video_url: 'https://youtu.be/x', cost: { amount: 10, state: 'refunded', transaction_id: null } }, pricing, null)).toMatchObject({
      totalLabel: 'Total devuelto',
      lines: [{ key: 'link' }, { key: 'seo' }],
    });
  });
});

describe('exportar la transcripción', () => {
  it('con segmentos enteros (sin tiempos por palabra) los subtítulos siguen siendo cortos', async () => {
    const { transcriptToSrt, groupTranscript } = await import('@/lib/download');
    const segments = Array.from({ length: 4 }, (_, index) => ({
      start: index * 12,
      end: index * 12 + 11.5,
      text: 'este es un segmento largo de la transcripción que no trae los tiempos de cada palabra y sigue varias líneas más sin un punto',
    }));
    const cues = groupTranscript(segments, { maxWords: 8 });
    expect(cues.length).toBeGreaterThan(8);
    for (const cue of cues) {
      expect(cue.text.split(/\s+/).length).toBeLessThanOrEqual(8);
      expect(cue.end - cue.start).toBeLessThan(6);
      expect(cue.end).toBeGreaterThan(cue.start);
    }
    // Nothing is lost or reordered.
    expect(cues.map((cue) => cue.text).join(' ')).toBe(segments.map((segment) => segment.text).join(' '));
    expect(transcriptToSrt(segments)).toMatch(/^1\n00:00:00,000 --> 00:00:0\d,\d{3}\n/);
  });

  it('con palabras sueltas no cambia nada', async () => {
    const { groupTranscript } = await import('@/lib/download');
    const words = [
      { start: 0.5, end: 0.9, text: 'Hola' },
      { start: 0.95, end: 1.4, text: 'gente.' },
      { start: 3, end: 3.2, text: 'Hoy' },
    ];
    expect(groupTranscript(words)).toEqual([
      { start: 0.5, end: 1.4, text: 'Hola gente.' },
      { start: 3, end: 3.2, text: 'Hoy' },
    ]);
  });
});
