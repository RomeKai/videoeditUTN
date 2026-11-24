import { describe, expect, it } from 'vitest';
import {
  DEFAULT_CONFIG,
  MANUAL_CAMERA_DEFAULT,
  PRESETS,
  buildCreateBody,
  buildPromptBody,
  loadLastConfig,
  manualCameraActive,
  matchesPreset,
  presetValuesOf,
  sanitizeConfig,
  saveLastConfig,
  subtitlesSummary,
  titleFromFileName,
  type ProjectConfig,
} from './new-project-config';

const file = { kind: 'file', file: new Blob(['x']), title: 'Podcast' } as const;
const link = { kind: 'link', url: 'https://youtu.be/abc', title: 'Video' } as const;
const config = (patch: Partial<ProjectConfig> = {}): ProjectConfig => ({ ...DEFAULT_CONFIG, ...patch });

describe('buildCreateBody', () => {
  it('manda el archivo con la configuración elegida', () => {
    const body = buildCreateBody(file, config({ render_resolution: '1080p', max_clips: 12, selection_criteria: '  consejos  ', use_seo: true }));
    expect(body.source_file).toBe(file.file);
    expect(body.video_url).toBeUndefined();
    expect(body).toMatchObject({ title: 'Podcast', project_type: 'repurpose', render_resolution: '1080p', max_clips: 12, selection_criteria: 'consejos', use_seo: true, auto_render_bypass: false });
  });

  it('un link siempre va en 720p', () => {
    const body = buildCreateBody(link, config({ render_resolution: '4K' }));
    expect(body).toMatchObject({ video_url: link.url, render_resolution: '720p' });
    expect(body.source_file).toBeUndefined();
  });

  it('el video completo no manda las opciones de clips', () => {
    const body = buildCreateBody(file, config({ project_type: 'single_edit', auto_render_bypass: true }));
    expect(body.project_type).toBe('single_edit');
    expect(body).not.toHaveProperty('max_clips');
    expect(body).not.toHaveProperty('selection_criteria');
    expect(body).not.toHaveProperty('auto_render_bypass');
  });

  it('Quien habla siempre sigue caras', () => {
    expect(buildCreateBody(file, config({ render_layout: 'active', use_facetracking: false })).use_facetracking).toBe(true);
    expect(buildCreateBody(file, config({ render_layout: 'fill', use_facetracking: false })).use_facetracking).toBe(false);
  });

  it('Dividido manda posición del gameplay, lado de la cámara y cámara manual', () => {
    const body = buildCreateBody(
      file,
      config({ render_layout: 'split', gameplay_position: 'left', camera_selection_position: 'right', manual_camera: true, manual_camera_x: 0.3, manual_camera_y: 0.6, manual_camera_zoom: 2 }),
    );
    expect(body).toMatchObject({ gameplay_position: 'left', camera_selection_position: 'right', manual_camera_x: 0.3, manual_camera_y: 0.6, manual_camera_zoom: 2 });
  });

  it('Versus manda solo la cámara manual', () => {
    const body = buildCreateBody(file, config({ render_layout: 'versus', gameplay_position: 'left', manual_camera: true, manual_camera_zoom: 1.5 }));
    expect(body.manual_camera_zoom).toBe(1.5);
    expect(body).not.toHaveProperty('gameplay_position');
    expect(body).not.toHaveProperty('camera_selection_position');
  });

  it('con la cámara manual apagada viajan sus valores por defecto', () => {
    const body = buildCreateBody(file, config({ render_layout: 'split', manual_camera: false, manual_camera_x: 0.9, manual_camera_zoom: 3 }));
    expect(body).toMatchObject({ manual_camera_x: MANUAL_CAMERA_DEFAULT.x, manual_camera_y: MANUAL_CAMERA_DEFAULT.y, manual_camera_zoom: MANUAL_CAMERA_DEFAULT.zoom });
  });

  it('los otros layouts no mandan nada del bloque Avanzado', () => {
    const body = buildCreateBody(file, config({ render_layout: 'fill', manual_camera: true, manual_camera_zoom: 3 }));
    expect(body).not.toHaveProperty('manual_camera_zoom');
    expect(body).not.toHaveProperty('gameplay_position');
  });

  it('sin brand kit no manda el campo', () => {
    expect(buildCreateBody(file, config({ brand_kit: null }))).not.toHaveProperty('brand_kit');
  });
});

describe('buildPromptBody', () => {
  it('manda solo el video y el SEO: el resto lo decide la IA', () => {
    expect(Object.keys(buildPromptBody(link, true)).sort()).toEqual(['title', 'use_seo', 'video_url']);
  });
});

describe('cámara manual', () => {
  it('está apagada mientras tenga sus valores por defecto', () => {
    expect(manualCameraActive(config({ render_layout: 'split', manual_camera: true }))).toBe(false);
    expect(manualCameraActive(config({ render_layout: 'split', manual_camera: true, manual_camera_zoom: 1.2 }))).toBe(true);
    expect(manualCameraActive(config({ render_layout: 'fill', manual_camera: true, manual_camera_zoom: 1.2 }))).toBe(false);
  });
});

describe('puntos de partida', () => {
  it('cada preset define estilo, layout, subtítulos e inteligencia', () => {
    expect(PRESETS.map((preset) => preset.title)).toEqual(['Podcast', 'Gaming', 'Clase', 'Vlog']);
    for (const preset of PRESETS) {
      const applied = config(preset.values);
      expect(matchesPreset(applied, preset.values)).toBe(true);
      expect(matchesPreset({ ...applied, subtitle_words_per_segment: 7 }, preset.values)).toBe(false);
      expect(presetValuesOf(applied)).toEqual(preset.values);
    }
  });

  it('resume los subtítulos en una línea', () => {
    expect(subtitlesSummary(config({ subtitle_size: 'large', subtitle_color: '#FFFF00' }))).toBe('Activados · grandes · abajo · amarillo · 3 palabras por línea');
    expect(subtitlesSummary(config({ add_subtitles: false }))).toBe('Desactivados');
    expect(subtitlesSummary(config({ subtitle_color: '#F4F1EA', subtitle_words_per_segment: 1 }), '#f4f1ea')).toContain('color del brand kit · 1 palabra por línea');
  });
});

describe('última configuración', () => {
  it('se guarda por usuario y vuelve sin el criterio ni el tipo de proyecto', () => {
    saveLastConfig('u1', config({ project_type: 'single_edit', selection_criteria: 'algo', render_layout: 'versus', max_clips: 14, use_seo: true }));
    expect(loadLastConfig('u2')).toBeNull();
    expect(loadLastConfig('u1')).toMatchObject({ project_type: 'repurpose', selection_criteria: '', render_layout: 'versus', max_clips: 14, use_seo: true });
  });

  it('descarta lo que ya no es válido', () => {
    const clean = sanitizeConfig({ render_layout: 'inventado', max_clips: 99, manual_camera_zoom: 9, subtitle_color: 'rojo', aspect_ratio: '1:1' });
    expect(clean).toMatchObject({ render_layout: 'fill', max_clips: 20, manual_camera_zoom: 4, subtitle_color: DEFAULT_CONFIG.subtitle_color, aspect_ratio: '1:1' });
    expect(sanitizeConfig('nada')).toBeNull();
    window.localStorage.setItem('oc.nuevo-proyecto.config.u3', '{roto');
    expect(loadLastConfig('u3')).toBeNull();
  });
});

describe('titleFromFileName', () => {
  it('saca la extensión', () => {
    expect(titleFromFileName('podcast-ep42.mp4')).toBe('podcast-ep42');
    expect(titleFromFileName('.mp4')).toBe('.mp4');
  });
});
