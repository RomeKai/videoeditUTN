import { describe, expect, it } from 'vitest';
import type { ActivityItem, Clip, Project } from '@/api/contracts/projects';
import { KNOWN_PIPELINE_ERROR_CODES } from '@/api/contracts/projects';
import { seedDb } from '@/api/mocks/seed';
import { serializeProject } from '@/api/mocks/serialize';
import { describeProjectStatus } from '@/lib/project-status';
import {
  activityLink,
  autoRenderNotice,
  buildConfigGroups,
  buildProcessSteps,
  buildProjectTabs,
  clipCounts,
  costLabel,
  currentTab,
  describeClip,
  explainCost,
  failedStepNumber,
  findMatches,
  foldText,
  groupActivityByDay,
  groupLinesByClip,
  headerStatusText,
  isWholeVideo,
  previewBlockedReason,
  pteAppliedLines,
  pteIgnoredLines,
  rulerTicks,
  sourceInfo,
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

  it('los movimientos son los eventos con monto', () => {
    const items = [item('a', 'project_created', at(20, 9)), item('b', 'tokens_reserved', at(20, 9), -763.2), item('c', 'tokens_refunded', at(21, 9), 763.2)];
    expect(tokenMovements(items).map((entry) => entry.id)).toEqual(['b', 'c']);
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
