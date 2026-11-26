import { describe, expect, it } from 'vitest';
import type { Clip, Project } from '@/api/contracts/projects';
import { buildApprovedSegments, draftReducer, draftScope, includedTotals, initialDraft, isDirty, mergeSuggestions, newLocalKey, restoreDraft, saveDraft, sortClips } from './draft';
import { applyEditorDraft, editorScope, readEditorDraft, storeEditorDraft } from './editorDraft';
import { explainReasoning } from './reasoning';
import { describeStyle, isManualCamera } from './style';
import { billedSeconds, formatEdge, parseEdge, rulerTicks } from './time';
import { buildRows, captionAt, snapEnd, snapStart, stepEdge, titleFromText, wordIndexAt, type Word } from './transcript';

const words: Word[] = [
  { start: 0.4, end: 0.8, text: 'Hoy' },
  { start: 0.9, end: 1.4, text: 'quiero' },
  { start: 1.5, end: 2.1, text: 'contarte' },
  { start: 2.2, end: 2.6, text: 'algo.' },
  { start: 4.5, end: 5.0, text: 'Mirá' },
  { start: 5.1, end: 5.6, text: 'este' },
  { start: 5.7, end: 6.3, text: 'número.' },
];

function clip(n: number, score: number, start: number): Clip {
  return {
    id: `00000008-0000-4000-8000-00000000000${n}`,
    project: '00000007-0000-4000-8000-000000000001',
    title: `Clip ${n}`,
    s3_object_key: null,
    start_time: start,
    end_time: start + 30,
    virality_score: score,
    ai_reasoning: 'Gancho fuerte · Idea completa. Arranca con una afirmación.',
    status: 'draft',
    created_at: '2026-11-24T12:00:00Z',
    layers: [],
  };
}

describe('tiempos', () => {
  it('escribe los bordes con décimas después de una coma', () => {
    expect(formatEdge(252)).toBe('04:12,0');
    expect(formatEdge(3920.52)).toBe('1:05:20,5');
    expect(formatEdge(59.96)).toBe('01:00,0');
  });

  it('lee lo que se tipea y rechaza lo que no es un tiempo', () => {
    expect(parseEdge('04:12,5')).toBe(252.5);
    expect(parseEdge('4:12.5')).toBe(252.5);
    expect(parseEdge('1:05:20')).toBe(3920);
    expect(parseEdge('90')).toBe(90);
    expect(parseEdge('4:75')).toBeNull();
    expect(parseEdge('hola')).toBeNull();
    expect(parseEdge('')).toBeNull();
  });

  it('cobra los segundos como el backend: ceil(round(s, 3))', () => {
    expect(billedSeconds(60.0000000001)).toBe(60);
    expect(billedSeconds(59.9996)).toBe(60);
    expect(billedSeconds(60.4)).toBe(61);
    expect(billedSeconds(0.1 + 0.2 + 29.7)).toBe(30);
  });

  it('arma una regla con pocos cortes', () => {
    expect(rulerTicks(232, 318)).toEqual([240, 250, 260, 270, 280, 290, 300, 310]);
    expect(rulerTicks(0, 4360, 8).length).toBeLessThanOrEqual(9);
  });
});

describe('transcripción', () => {
  it('lleva los bordes a palabras completas', () => {
    expect(snapStart(words, 1.45)).toBe(1.5);
    expect(snapStart(words, 0)).toBe(0.4);
    expect(snapEnd(words, 2.3)).toBe(2.1);
    expect(snapEnd(words, 2.5)).toBe(2.6);
    expect(snapEnd(words, 99)).toBe(6.3);
  });

  it('mueve un borde una palabra con el teclado', () => {
    expect(stepEdge(words, 'start', 1.5, 1)).toBe(2.2);
    expect(stepEdge(words, 'start', 1.5, -1)).toBe(0.9);
    expect(stepEdge(words, 'end', 6.3, 1)).toBe(6.3);
  });

  it('separa párrafos en las pausas y encuentra la palabra que suena', () => {
    const rows = buildRows(words);
    expect(rows.map((row) => [row.from, row.to])).toEqual([
      [0, 3],
      [4, 6],
    ]);
    expect(wordIndexAt(words, 1.0)).toBe(1);
    expect(wordIndexAt(words, 3.6)).toBe(-1);
  });

  it('arma el subtítulo de muestra de a N palabras', () => {
    expect(captionAt(words, 1.6, 0.4, 3)).toEqual({ words: ['Hoy', 'quiero', 'contarte'], active: 2 });
    expect(captionAt(words, 2.3, 0.4, 3)).toEqual({ words: ['algo.', 'Mirá', 'este'], active: 0 });
    expect(captionAt(words, 3.6, 0.4, 3)).toBeNull();
  });

  it('titula un clip con sus primeras palabras', () => {
    expect(titleFromText('Hoy quiero contarte algo.')).toBe('Hoy quiero contarte algo');
    expect(titleFromText('palabra '.repeat(20)).endsWith('…')).toBe(true);
  });
});

describe('por qué lo eligió la IA', () => {
  it('usa las etiquetas que el texto ya trae separadas', () => {
    expect(explainReasoning('Gancho fuerte · Idea completa · Cierre claro. Arranca con una afirmación.')).toEqual({
      chips: ['Gancho fuerte', 'Idea completa', 'Cierre claro'],
      sentence: 'Arranca con una afirmación.',
    });
  });

  it('saca etiquetas de las cláusulas cortas y nunca inventa', () => {
    const result = explainReasoning('Arranca con una pregunta directa, tiene una idea completa y cierra con una frase citable.');
    expect(result.chips).toEqual(['Arranca con una pregunta directa', 'Tiene una idea completa', 'Cierra con una frase citable']);
    expect(result.sentence).toContain('Arranca con una pregunta directa');
    expect(explainReasoning('Funciona.').chips).toEqual([]);
    expect(explainReasoning('')).toEqual({ chips: [], sentence: '' });
  });
});

describe('borrador de la revisión', () => {
  const suggestions = [clip(1, 60, 500), clip(2, 92, 100), clip(3, 75, 300)];

  it('ordena por puntaje y deja afuera los flojos', () => {
    const draft = initialDraft(suggestions);
    expect(draft.clips.map((item) => [item.n, item.score, item.included])).toEqual([
      [1, 92, true],
      [2, 75, true],
      [3, 60, false],
    ]);
    expect(isDirty(draft)).toBe(false);
    expect(sortClips(draft.clips, 'time').map((item) => item.start)).toEqual([100, 300, 500]);
  });

  it('incluye todos cuando todos son flojos', () => {
    expect(initialDraft([clip(1, 60, 0), clip(2, 55, 100)]).clips.every((item) => item.included)).toBe(true);
  });

  it('no deja cruzar los bordes ni pasarse del video', () => {
    let draft = initialDraft(suggestions);
    const key = draft.clips[0]?.key ?? '';
    draft = draftReducer(draft, { type: 'edge', key, edge: 'start', value: 999, total: 600 });
    expect(draft.clips[0]?.start).toBe(129);
    draft = draftReducer(draft, { type: 'edge', key, edge: 'end', value: 9999, total: 600 });
    expect(draft.clips[0]?.end).toBe(600);
    expect(isDirty(draft)).toBe(true);
  });

  it('agrega, cuenta y quita clips propios', () => {
    let draft = initialDraft(suggestions);
    draft = draftReducer(draft, { type: 'add', key: newLocalKey(), start: 10, end: 26, title: 'Mío', total: 600 });
    expect(draft.clips).toHaveLength(4);
    expect(draft.selectedKey).toBe(draft.clips[3]?.key);
    expect(includedTotals(draft.clips)).toEqual({ count: 3, seconds: 76 });
    draft = draftReducer(draft, { type: 'remove', key: draft.clips[3]?.key ?? '' });
    expect(draft.clips).toHaveLength(3);
    expect(isDirty(draft)).toBe(false);
  });

  it('dos clips propios en el mismo lugar no comparten clave', () => {
    let draft = initialDraft(suggestions);
    draft = draftReducer(draft, { type: 'add', key: newLocalKey(), start: 10, end: 26, title: 'Uno', total: 600 });
    const first = draft.selectedKey ?? '';
    draft = draftReducer(draft, { type: 'remove', key: first });
    draft = draftReducer(draft, { type: 'add', key: newLocalKey(), start: 10, end: 26, title: 'Dos', total: 600 });
    draft = draftReducer(draft, { type: 'add', key: draft.selectedKey ?? '', start: 10, end: 26, title: 'Tres', total: 600 });
    const keys = draft.clips.map((item) => item.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).not.toContain(first);
  });

  it('arma los segmentos aprobados en orden, con el texto de cada tramo', () => {
    let draft = initialDraft([clip(1, 90, 4), clip(2, 80, 0)]);
    draft = draftReducer(draft, { type: 'edge', key: draft.clips[0]?.key ?? '', edge: 'end', value: 6.3, total: 600 });
    draft = draftReducer(draft, { type: 'edge', key: draft.clips[1]?.key ?? '', edge: 'end', value: 2.6, total: 600 });
    draft = draftReducer(draft, { type: 'add', key: newLocalKey(), start: 100, end: 110, title: ' ', total: 600 });
    const segments = buildApprovedSegments(draft.clips, words);
    expect(segments.map((segment) => segment.start)).toEqual([0, 4, 100]);
    // The render burns the subtitles from `text` only: every segment with speech carries it.
    expect(segments[0]).toEqual({ start: 0, end: 2.6, text: 'Hoy quiero contarte algo.', title: 'Clip 2', clip_id: '00000008-0000-4000-8000-000000000002' });
    expect(segments[1]?.text).toBe('Mirá este número.');
    // No words in the range (silence): no text, and a blank title falls back to the clip number.
    expect(segments[2]).toEqual({ start: 100, end: 110, title: 'Clip 3' });
    draft = draftReducer(draft, { type: 'toggle', key: draft.clips[0]?.key ?? '', included: false });
    expect(buildApprovedSegments(draft.clips, words)).toHaveLength(2);
  });

  it('recupera el borrador guardado sobre las sugerencias de ahora', () => {
    let draft = initialDraft(suggestions);
    draft = draftReducer(draft, { type: 'toggle', key: draft.clips[2]?.key ?? '', included: true });
    draft = draftReducer(draft, { type: 'add', key: newLocalKey(), start: 10, end: 26, title: 'Mío', total: 600 });
    saveDraft('p1', draft);
    // One suggestion is gone by the time the page reloads.
    const restored = restoreDraft('p1', suggestions.slice(1));
    expect(restored.clips.map((item) => item.title)).toEqual(['Clip 2', 'Clip 3', 'Mío']);
    expect(restored.clips[2]?.clipId).toBeNull();
    expect(restoreDraft('otro', suggestions).clips).toHaveLength(3);
  });
});

describe('el borrador sigue al servidor', () => {
  const suggestions = [clip(1, 60, 500), clip(2, 92, 100), clip(3, 75, 300)];
  const byTitle = (draft: ReturnType<typeof initialDraft>, title: string) => draft.clips.find((item) => item.original?.title === title || item.title === title);

  it('un borrador que solo excluyó otro clip no devuelve un recorte viejo', () => {
    let draft = initialDraft(suggestions);
    draft = draftReducer(draft, { type: 'toggle', key: byTitle(draft, 'Clip 3')?.key ?? '', included: false });
    saveDraft('u1.p1', draft);
    // Meanwhile the clip editor saved a new end for clip 2.
    const moved = suggestions.map((item) => (item.title === 'Clip 2' ? { ...item, end_time: item.end_time + 0.5 } : item));
    const restored = restoreDraft('u1.p1', moved);
    expect(byTitle(restored, 'Clip 2')).toMatchObject({ start: 100, end: 130.5 });
    expect(byTitle(restored, 'Clip 2')?.original).toMatchObject({ end: 130.5 });
    // What the person did is still there.
    expect(byTitle(restored, 'Clip 3')?.included).toBe(false);
  });

  it('conserva lo que la persona cambió solo si su punto de partida sigue igual', () => {
    let draft = initialDraft(suggestions);
    const key = byTitle(draft, 'Clip 2')?.key ?? '';
    draft = draftReducer(draft, { type: 'edge', key, edge: 'start', value: 105, total: 600 });
    draft = draftReducer(draft, { type: 'rename', key, title: 'Mi título' });
    // Nothing changed on the server: the edits stay.
    expect(mergeSuggestions(draft, suggestions).clips.find((item) => item.key === key)).toMatchObject({ start: 105, end: 130, title: 'Mi título' });
    // The server moved the start: that field follows it; the title, whose base is the same, stays.
    const moved = suggestions.map((item) => (item.title === 'Clip 2' ? { ...item, start_time: 98 } : item));
    expect(mergeSuggestions(draft, moved).clips.find((item) => item.key === key)).toMatchObject({ start: 98, end: 130, title: 'Mi título' });
  });

  it('al llegar datos nuevos, lo que nadie tocó los sigue y los clips propios quedan', () => {
    let draft = initialDraft(suggestions);
    draft = draftReducer(draft, { type: 'add', key: newLocalKey(), start: 10, end: 26, title: 'Mío', total: 600 });
    const moved = suggestions.slice(0, 2).map((item) => ({ ...item, end_time: item.end_time + 1 }));
    const synced = draftReducer(draft, { type: 'sync', suggestions: moved });
    expect(synced.clips.map((item) => [item.title, item.end])).toEqual([
      ['Clip 2', 131],
      ['Clip 1', 531],
      ['Mío', 26],
    ]);
    expect(synced.selectedKey).toBe(draft.selectedKey);
  });

  it('un borde editado que se cruza con el valor nuevo del otro cede al servidor', () => {
    let draft = initialDraft(suggestions);
    const key = byTitle(draft, 'Clip 2')?.key ?? '';
    draft = draftReducer(draft, { type: 'edge', key, edge: 'start', value: 128, total: 600 });
    const moved = suggestions.map((item) => (item.title === 'Clip 2' ? { ...item, end_time: 120 } : item));
    expect(mergeSuggestions(draft, moved).clips.find((item) => item.key === key)).toMatchObject({ start: 100, end: 120 });
  });

  it('las claves guardadas llevan a la persona', () => {
    expect(draftScope('u1', 'p1')).not.toBe(draftScope('u2', 'p1'));
    expect(editorScope('u1', 'c1')).not.toBe(editorScope('u2', 'c1'));
  });
});

describe('borrador del editor de clip', () => {
  const server = { title: 'Original', start: 10, end: 40 };

  it('solo vuelven los campos que la persona cambió', () => {
    // Only the title was edited; a teammate moved the trim meanwhile.
    storeEditorDraft('u1.c1', { title: 'Nuevo', start: 10, end: 40, base: server });
    const now = { title: 'Original', start: 8, end: 42 };
    expect(applyEditorDraft(now, readEditorDraft('u1.c1'))).toEqual({ title: 'Nuevo', start: 8, end: 42 });
    // Only the start was edited; the title follows the server.
    storeEditorDraft('u1.c1', { title: 'Original', start: 12, end: 40, base: server });
    expect(applyEditorDraft({ title: 'Otro', start: 10, end: 44 }, readEditorDraft('u1.c1'))).toEqual({ title: 'Otro', start: 12, end: 44 });
    expect(applyEditorDraft(now, null)).toEqual(now);
  });

  it('un borrador sin su punto de partida o de otra persona no se usa', () => {
    window.sessionStorage.setItem('oc.editor.u1.c1', JSON.stringify({ title: 'Viejo', start: 1, end: 5 }));
    expect(readEditorDraft('u1.c1')).toBeNull();
    storeEditorDraft(editorScope('u1', 'c1'), { title: 'Nuevo', start: 10, end: 40, base: server });
    expect(readEditorDraft(editorScope('u2', 'c1'))).toBeNull();
    storeEditorDraft(editorScope('u1', 'c1'), null);
    expect(readEditorDraft(editorScope('u1', 'c1'))).toBeNull();
  });
});

describe('estilo del proyecto', () => {
  const base = {
    aspect_ratio: '9:16',
    editing_style: 'dynamic',
    render_layout: 'fill',
    gameplay_position: 'left',
    camera_selection_position: 'right',
    manual_camera_x: 0.5,
    manual_camera_y: 0.2,
    manual_camera_zoom: 1,
    use_facetracking: true,
    add_subtitles: true,
    subtitle_color: '#F2C94C',
    subtitle_size: 'medium',
    subtitle_position: 'bottom',
    subtitle_words_per_segment: 3,
    remove_silences: false,
    metadata: { render_resolution: '1080p' },
  } as unknown as Project;
  const value = (project: Project, key: string) => describeStyle(project).find((row) => row.key === key)?.value;

  it('no muestra opciones avanzadas fuera de Dividido y Versus', () => {
    expect(describeStyle(base).map((row) => row.key)).toEqual(['format', 'style', 'layout', 'faces', 'subtitles', 'silences']);
    expect(value(base, 'format')).toBe('9:16 · 1080p');
    expect(value(base, 'subtitles')).toBe('Mediano · Abajo · 3 palabras');
  });

  it('Dividido: gameplay y lado de la cámara; la cámara manual los reemplaza', () => {
    const split = { ...base, render_layout: 'split' } as Project;
    expect(isManualCamera(split)).toBe(false);
    expect(value(split, 'gameplay')).toBe('Izquierda');
    expect(value(split, 'camera')).toBe('Lado: Derecha');
    const manual = { ...split, manual_camera_zoom: 1.5 } as Project;
    expect(value(manual, 'camera')).toBe('Manual · x 0,50 · y 0,20 · zoom 1,50');
    expect(value(manual, 'faces')).toContain('la cámara manual lo reemplaza');
  });

  it('Versus: solo la cámara manual', () => {
    const versus = { ...base, render_layout: 'versus' } as Project;
    expect(value(versus, 'gameplay')).toBeUndefined();
    expect(value(versus, 'camera')).toBe('Automática');
    expect(value({ ...versus, manual_camera_x: 0.3 } as Project, 'camera')).toContain('Manual');
  });

  it('PiP: esquina y lado de la cámara', () => {
    const pip = { ...base, render_layout: 'pip', pip_corner: 'top-left' } as Project;
    expect(value(pip, 'corner')).toBe('Arriba a la izquierda');
    expect(value(pip, 'camera')).toBe('Lado: Derecha');
    expect(value(pip, 'gameplay')).toBeUndefined();
  });

  it('Reacción y Podcast: como Dividido y como Versus', () => {
    expect(value({ ...base, render_layout: 'reaction' } as Project, 'gameplay')).toBe('Izquierda');
    expect(value({ ...base, render_layout: 'podcast' } as Project, 'camera')).toBe('Automática');
  });

  it('Trending: el color del borde y el texto', () => {
    const trending = { ...base, render_layout: 'trending', trending_color: '#00C2FF', trending_text: 'Mirá esto' } as Project;
    const row = describeStyle(trending).find((item) => item.key === 'trending');
    expect(row).toMatchObject({ value: 'Texto: «Mirá esto»', swatch: '#00C2FF' });
    expect(value({ ...trending, trending_text: '' } as Project, 'trending')).toBe('Sin texto');
    expect(value(trending, 'camera')).toBeUndefined();
  });
});
