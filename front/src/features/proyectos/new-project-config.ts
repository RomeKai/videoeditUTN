import type { RenderResolution } from '@/api/contracts/common';
import type {
  AspectRatio,
  CreateProjectRequest,
  EditingStyle,
  IntelligenceLevel,
  Layout,
  Position,
  Project,
  ProjectType,
  SubtitlePosition,
  SubtitleSize,
} from '@/api/contracts/projects';
import { normalizeHex } from '@/lib/format';
import { LABELS } from '@/lib/project-status';

/** Everything the form of C1 decides, besides the video itself. */
export interface ProjectConfig {
  project_type: ProjectType;
  aspect_ratio: AspectRatio;
  render_resolution: RenderResolution;
  max_clips: number;
  selection_criteria: string;
  intelligence_level: IntelligenceLevel;
  editing_style: Exclude<EditingStyle, 'custom'>;
  render_layout: Layout;
  gameplay_position: Position;
  camera_selection_position: Position;
  /** UI switch: when off, the manual camera fields travel with their defaults (which the backend reads as "off"). */
  manual_camera: boolean;
  manual_camera_x: number;
  manual_camera_y: number;
  manual_camera_zoom: number;
  /** null: no brand kit. undefined: not chosen yet, the workspace default is proposed. */
  brand_kit: string | null | undefined;
  add_subtitles: boolean;
  subtitle_size: SubtitleSize;
  subtitle_position: SubtitlePosition;
  subtitle_color: string;
  subtitle_words_per_segment: number;
  use_facetracking: boolean;
  remove_silences: boolean;
  use_seo: boolean;
  auto_render_bypass: boolean;
}

/** Defaults of the manual camera: with these values it is off (map C1 · Avanzado). */
export const MANUAL_CAMERA_DEFAULT = { x: 0.5, y: 0.2, zoom: 1 } as const;
export const MAX_CLIPS = { min: 1, max: 20, default: 8 } as const;
export const WORDS_PER_LINE = { min: 1, max: 8 } as const;

export const SUBTITLE_COLORS = [
  { value: '#FFFFFF', label: 'Blanco' },
  { value: '#FFFF00', label: 'Amarillo' },
  { value: '#3DD6C4', label: 'Verde' },
  { value: '#FF7AA8', label: 'Rosa' },
] as const;

export const DEFAULT_CONFIG: ProjectConfig = {
  project_type: 'repurpose',
  aspect_ratio: '9:16',
  render_resolution: '720p',
  max_clips: MAX_CLIPS.default,
  selection_criteria: '',
  intelligence_level: 'fast',
  editing_style: 'dynamic',
  render_layout: 'fill',
  gameplay_position: 'center',
  camera_selection_position: 'center',
  manual_camera: false,
  manual_camera_x: MANUAL_CAMERA_DEFAULT.x,
  manual_camera_y: MANUAL_CAMERA_DEFAULT.y,
  manual_camera_zoom: MANUAL_CAMERA_DEFAULT.zoom,
  brand_kit: undefined,
  add_subtitles: true,
  subtitle_size: 'medium',
  subtitle_position: 'bottom',
  subtitle_color: '#FFFF00',
  subtitle_words_per_segment: 3,
  use_facetracking: false,
  remove_silences: false,
  use_seo: false,
  auto_render_bypass: false,
};

/** What a starting point defines: style, layout, subtitles and intelligence (map 9.1.1). */
export type PresetValues = Pick<
  ProjectConfig,
  | 'editing_style'
  | 'render_layout'
  | 'add_subtitles'
  | 'subtitle_size'
  | 'subtitle_position'
  | 'subtitle_color'
  | 'subtitle_words_per_segment'
  | 'use_facetracking'
  | 'remove_silences'
>;

export interface Preset {
  id: 'podcast' | 'gaming' | 'clase' | 'vlog';
  title: string;
  /** Second line of the card; the first one is "Estilo · Layout". */
  subtitles: string;
  values: PresetValues;
}

export const PRESETS: Preset[] = [
  {
    id: 'podcast',
    title: 'Podcast',
    subtitles: 'Subtítulos grandes',
    values: {
      editing_style: 'dynamic',
      render_layout: 'active',
      add_subtitles: true,
      subtitle_size: 'large',
      subtitle_position: 'bottom',
      subtitle_color: '#FFFF00',
      subtitle_words_per_segment: 3,
      use_facetracking: true,
      remove_silences: true,
    },
  },
  {
    id: 'gaming',
    title: 'Gaming',
    subtitles: 'Subtítulos al centro',
    values: {
      editing_style: 'hormozi',
      render_layout: 'split',
      add_subtitles: true,
      subtitle_size: 'large',
      subtitle_position: 'center',
      subtitle_color: '#FFFF00',
      subtitle_words_per_segment: 2,
      use_facetracking: false,
      remove_silences: false,
    },
  },
  {
    id: 'clase',
    title: 'Clase',
    subtitles: 'Subtítulos medianos',
    values: {
      editing_style: 'minimalist',
      render_layout: 'fit',
      add_subtitles: true,
      subtitle_size: 'medium',
      subtitle_position: 'bottom',
      subtitle_color: '#FFFFFF',
      subtitle_words_per_segment: 4,
      use_facetracking: false,
      remove_silences: true,
    },
  },
  {
    id: 'vlog',
    title: 'Vlog',
    subtitles: 'Subtítulos en caja',
    values: {
      editing_style: 'vlog',
      render_layout: 'fill',
      add_subtitles: true,
      subtitle_size: 'medium',
      subtitle_position: 'bottom',
      subtitle_color: '#FFFFFF',
      subtitle_words_per_segment: 3,
      use_facetracking: true,
      remove_silences: false,
    },
  },
];

const PRESET_KEYS: Array<keyof PresetValues> = [
  'editing_style',
  'render_layout',
  'add_subtitles',
  'subtitle_size',
  'subtitle_position',
  'subtitle_color',
  'subtitle_words_per_segment',
  'use_facetracking',
  'remove_silences',
];

export function presetValuesOf(config: ProjectConfig): PresetValues {
  return Object.fromEntries(PRESET_KEYS.map((key) => [key, config[key]])) as PresetValues;
}

export function matchesPreset(config: ProjectConfig, values: PresetValues): boolean {
  return PRESET_KEYS.every((key) => config[key] === values[key]);
}

/** "Dinámico · Quien habla" */
export function styleLine(values: Pick<ProjectConfig, 'editing_style' | 'render_layout'>): string {
  return `${LABELS.editing_style[values.editing_style]} · ${LABELS.render_layout[values.render_layout]}`;
}

const SIZE_PLURAL: Record<SubtitleSize, string> = { small: 'chicos', medium: 'medianos', large: 'grandes' };

/** "Subtítulos grandes" / "Sin subtítulos" */
export function subtitlesLine(values: Pick<ProjectConfig, 'add_subtitles' | 'subtitle_size'>): string {
  return values.add_subtitles ? `Subtítulos ${SIZE_PLURAL[values.subtitle_size]}` : 'Sin subtítulos';
}

export function colorLabel(color: string, brandColor?: string | null): string {
  const known = SUBTITLE_COLORS.find((item) => item.value.toLowerCase() === color.toLowerCase());
  if (known) return known.label.toLowerCase();
  if (brandColor && normalizeHex(brandColor, '') === color.toUpperCase()) return 'color del brand kit';
  return color.toUpperCase();
}

/** Value shown on the closed "Subtítulos" row. */
export function subtitlesSummary(config: ProjectConfig, brandColor?: string | null): string {
  if (!config.add_subtitles) return 'Desactivados';
  const words = config.subtitle_words_per_segment;
  return [
    'Activados',
    SIZE_PLURAL[config.subtitle_size],
    LABELS.subtitle_position[config.subtitle_position].toLowerCase(),
    colorLabel(config.subtitle_color, brandColor),
    `${words} ${words === 1 ? 'palabra' : 'palabras'} por línea`,
  ].join(' · ');
}

/** The layout "Quien habla" always follows faces to know who is speaking. */
export function followsFaces(config: Pick<ProjectConfig, 'render_layout' | 'use_facetracking'>): boolean {
  return config.render_layout === 'active' || config.use_facetracking;
}

export function hasAdvanced(layout: Layout): boolean {
  return layout === 'split' || layout === 'versus';
}

/** The manual camera only counts while it differs from its defaults. */
export function manualCameraActive(config: ProjectConfig): boolean {
  return (
    hasAdvanced(config.render_layout) &&
    config.manual_camera &&
    (config.manual_camera_x !== MANUAL_CAMERA_DEFAULT.x || config.manual_camera_y !== MANUAL_CAMERA_DEFAULT.y || config.manual_camera_zoom !== MANUAL_CAMERA_DEFAULT.zoom)
  );
}

export const LAYOUT_HINTS: Record<Layout, string> = {
  fill: 'Llenar: el video ocupa todo el cuadro y se recorta a los costados.',
  fit: 'Ajustar: el video entra completo, con bandas arriba y abajo.',
  blurred: 'Fondo desenfocado: el video entra completo sobre una copia desenfocada de sí mismo.',
  split: 'Dividido: la cámara arriba y el gameplay abajo.',
  pip: 'PiP: el video de fondo y la cámara en un recuadro.',
  versus: 'Versus: dos personas, una en cada mitad.',
  active: 'Quien habla: el encuadre va a la persona que está hablando.',
};

/** Configuration of an existing project, for "Crear de nuevo" and "Duplicar configuración". */
export function configFromProject(project: Project): ProjectConfig {
  const manual =
    project.manual_camera_x !== MANUAL_CAMERA_DEFAULT.x || project.manual_camera_y !== MANUAL_CAMERA_DEFAULT.y || project.manual_camera_zoom !== MANUAL_CAMERA_DEFAULT.zoom;
  return {
    project_type: project.project_type,
    aspect_ratio: project.aspect_ratio,
    render_resolution: project.metadata.render_resolution ?? '720p',
    max_clips: clamp(project.metadata.max_clips ?? MAX_CLIPS.default, MAX_CLIPS.min, MAX_CLIPS.max),
    selection_criteria: project.metadata.selection_criteria ?? '',
    intelligence_level: project.intelligence_level,
    editing_style: project.editing_style === 'custom' ? 'dynamic' : project.editing_style,
    render_layout: project.render_layout,
    gameplay_position: project.gameplay_position,
    camera_selection_position: project.camera_selection_position,
    manual_camera: manual,
    manual_camera_x: project.manual_camera_x,
    manual_camera_y: project.manual_camera_y,
    manual_camera_zoom: project.manual_camera_zoom,
    brand_kit: project.brand_kit,
    add_subtitles: project.add_subtitles,
    subtitle_size: project.subtitle_size,
    subtitle_position: project.subtitle_position,
    // Colors come back as free text; the form and the request need #RRGGBB.
    subtitle_color: normalizeHex(project.subtitle_color, DEFAULT_CONFIG.subtitle_color),
    subtitle_words_per_segment: clamp(project.subtitle_words_per_segment, WORDS_PER_LINE.min, WORDS_PER_LINE.max),
    use_facetracking: project.use_facetracking,
    remove_silences: project.remove_silences,
    use_seo: Boolean(project.metadata.use_seo),
    auto_render_bypass: project.auto_render_bypass,
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}

/* ── Remembered configuration (map C1 · Recordar) ───────────────────────── */

const STORAGE_PREFIX = 'oc.nuevo-proyecto.config';
const STORAGE_VERSION = 1;

const isOneOf = <T extends string>(options: readonly T[], value: unknown): value is T => typeof value === 'string' && (options as readonly string[]).includes(value);

/** Reads back only what still makes sense: a stored value of an older version of the form never breaks the screen. */
export function sanitizeConfig(raw: unknown): ProjectConfig | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const base = DEFAULT_CONFIG;
  const bool = (key: keyof ProjectConfig) => (typeof value[key] === 'boolean' ? (value[key] as boolean) : (base[key] as boolean));
  const num = (key: keyof ProjectConfig, min: number, max: number) => {
    const item = value[key];
    return typeof item === 'number' && Number.isFinite(item) ? Math.min(max, Math.max(min, item)) : (base[key] as number);
  };
  const positions = ['left', 'center', 'right'] as const;
  return {
    project_type: 'repurpose',
    aspect_ratio: isOneOf(['9:16', '1:1', '16:9'] as const, value.aspect_ratio) ? value.aspect_ratio : base.aspect_ratio,
    render_resolution: isOneOf(['720p', '1080p', '4K'] as const, value.render_resolution) ? value.render_resolution : base.render_resolution,
    max_clips: Math.round(num('max_clips', MAX_CLIPS.min, MAX_CLIPS.max)),
    selection_criteria: '',
    intelligence_level: isOneOf(['fast', 'smart'] as const, value.intelligence_level) ? value.intelligence_level : base.intelligence_level,
    editing_style: isOneOf(['dynamic', 'minimalist', 'vlog', 'hormozi'] as const, value.editing_style) ? value.editing_style : base.editing_style,
    render_layout: isOneOf(['fill', 'fit', 'blurred', 'split', 'pip', 'versus', 'active'] as const, value.render_layout) ? value.render_layout : base.render_layout,
    gameplay_position: isOneOf(positions, value.gameplay_position) ? value.gameplay_position : base.gameplay_position,
    camera_selection_position: isOneOf(positions, value.camera_selection_position) ? value.camera_selection_position : base.camera_selection_position,
    manual_camera: bool('manual_camera'),
    manual_camera_x: num('manual_camera_x', 0, 1),
    manual_camera_y: num('manual_camera_y', 0, 1),
    manual_camera_zoom: num('manual_camera_zoom', 1, 4),
    brand_kit: typeof value.brand_kit === 'string' ? value.brand_kit : value.brand_kit === null ? null : undefined,
    add_subtitles: bool('add_subtitles'),
    subtitle_size: isOneOf(['small', 'medium', 'large'] as const, value.subtitle_size) ? value.subtitle_size : base.subtitle_size,
    subtitle_position: isOneOf(['top', 'center', 'bottom'] as const, value.subtitle_position) ? value.subtitle_position : base.subtitle_position,
    subtitle_color: typeof value.subtitle_color === 'string' && /^#[0-9a-fA-F]{6}$/.test(value.subtitle_color) ? value.subtitle_color : base.subtitle_color,
    subtitle_words_per_segment: Math.round(num('subtitle_words_per_segment', WORDS_PER_LINE.min, WORDS_PER_LINE.max)),
    use_facetracking: bool('use_facetracking'),
    remove_silences: bool('remove_silences'),
    use_seo: bool('use_seo'),
    auto_render_bypass: bool('auto_render_bypass'),
  };
}

/** The last configuration this user created a project with, in this browser. */
export function loadLastConfig(userId: string): ProjectConfig | null {
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}.${userId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { version?: number; config?: unknown };
    return parsed.version === STORAGE_VERSION ? sanitizeConfig(parsed.config) : null;
  } catch {
    return null;
  }
}

/** The criteria and the kind of project belong to one video, so they are not remembered. */
export function saveLastConfig(userId: string, config: ProjectConfig): void {
  try {
    const stored: ProjectConfig = { ...config, selection_criteria: '', project_type: 'repurpose' };
    window.localStorage.setItem(`${STORAGE_PREFIX}.${userId}`, JSON.stringify({ version: STORAGE_VERSION, config: stored }));
  } catch {
    // Storage blocked: nothing to remember.
  }
}

/* ── Request ────────────────────────────────────────────────────────────── */

export type ProjectSourceInput = { kind: 'file'; file: Blob; title: string } | { kind: 'link'; url: string; title?: string };

function sourceFields(source: ProjectSourceInput): Pick<CreateProjectRequest, 'source_file' | 'video_url' | 'title'> {
  const title = source.title?.trim().slice(0, 255);
  return source.kind === 'file' ? { source_file: source.file, ...(title ? { title } : {}) } : { video_url: source.url, ...(title ? { title } : {}) };
}

/** "podcast-ep42.mp4" → "podcast-ep42" */
export function titleFromFileName(name: string): string {
  return name.replace(/\.[a-z0-9]{2,5}$/i, '').trim() || name;
}

/** Body of `POST /projects/` for the manual form. */
export function buildCreateBody(source: ProjectSourceInput, config: ProjectConfig): CreateProjectRequest {
  const single = config.project_type === 'single_edit';
  const advanced = hasAdvanced(config.render_layout);
  const manual = advanced && config.manual_camera;
  const criteria = config.selection_criteria.trim();
  return {
    ...sourceFields(source),
    project_type: config.project_type,
    aspect_ratio: config.aspect_ratio,
    // Links are always processed in 720p.
    render_resolution: source.kind === 'link' ? '720p' : config.render_resolution,
    editing_style: config.editing_style,
    render_layout: config.render_layout,
    ...(config.render_layout === 'split' ? { gameplay_position: config.gameplay_position, camera_selection_position: config.camera_selection_position } : {}),
    ...(advanced
      ? {
          manual_camera_x: manual ? config.manual_camera_x : MANUAL_CAMERA_DEFAULT.x,
          manual_camera_y: manual ? config.manual_camera_y : MANUAL_CAMERA_DEFAULT.y,
          manual_camera_zoom: manual ? config.manual_camera_zoom : MANUAL_CAMERA_DEFAULT.zoom,
        }
      : {}),
    ...(config.brand_kit ? { brand_kit: config.brand_kit } : {}),
    // Always explicit: the contract defaults it to true when it is left out.
    add_subtitles: config.add_subtitles,
    subtitle_size: config.subtitle_size,
    subtitle_position: config.subtitle_position,
    subtitle_color: config.subtitle_color,
    subtitle_words_per_segment: config.subtitle_words_per_segment,
    use_facetracking: followsFaces(config),
    remove_silences: config.remove_silences,
    use_seo: config.use_seo,
    ...(single
      ? {}
      : {
          max_clips: config.max_clips,
          intelligence_level: config.intelligence_level,
          auto_render_bypass: config.auto_render_bypass,
          ...(criteria ? { selection_criteria: criteria } : {}),
        }),
  };
}

/** Body of `from-prompt`: only the video and what the text cannot ask for (SEO). The AI decides the rest. */
export function buildPromptBody(source: ProjectSourceInput, useSeo: boolean): CreateProjectRequest {
  return { ...sourceFields(source), use_seo: useSeo };
}
