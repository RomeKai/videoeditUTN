import type { BrandKit, BrandKitInput, WatermarkPosition } from '@/api/contracts/brandkits';
import { normalizeHex } from '@/lib/format';

export type FileField = 'primary_font' | 'secondary_font' | 'watermark_logo' | 'intro_video' | 'outro_video';
export type ColorField = 'primary_color' | 'secondary_color' | 'accent_color';

export const FILE_FIELDS: FileField[] = ['primary_font', 'secondary_font', 'watermark_logo', 'intro_video', 'outro_video'];
export const COLOR_FIELDS: ColorField[] = ['primary_color', 'secondary_color', 'accent_color'];

/** How the three colors of the model are used: the subtitles' text, their background and the active word. */
export const COLOR_LABEL: Record<ColorField, { label: string; hint: string; inSentence: string }> = {
  primary_color: { label: 'Texto', hint: 'Color principal', inSentence: 'el color del texto' },
  secondary_color: { label: 'Fondo', hint: 'Color secundario', inSentence: 'el color del fondo' },
  accent_color: { label: 'Palabra activa', hint: 'Color de acento', inSentence: 'el color de la palabra activa' },
};

export const POSITIONS: WatermarkPosition[] = ['top_left', 'top_right', 'bottom_left', 'bottom_right'];
export const POSITION_LABEL: Record<WatermarkPosition, string> = {
  top_left: 'Arriba a la izquierda',
  top_right: 'Arriba a la derecha',
  bottom_left: 'Abajo a la izquierda',
  bottom_right: 'Abajo a la derecha',
};
export const DEFAULT_POSITION: WatermarkPosition = 'bottom_right';

const MB = 1024 * 1024;

/** What each file accepts. The limits are the ones the design states next to each section. */
export const FILE_RULES: Record<FileField, { accept: string; extensions: RegExp; maxBytes: number; error: string; tooBig: string }> = {
  primary_font: { accept: '.ttf,.otf', extensions: /\.(ttf|otf)$/i, maxBytes: 5 * MB, error: 'Subí una fuente .ttf u .otf.', tooBig: 'La fuente pesa más de 5 MB.' },
  secondary_font: { accept: '.ttf,.otf', extensions: /\.(ttf|otf)$/i, maxBytes: 5 * MB, error: 'Subí una fuente .ttf u .otf.', tooBig: 'La fuente pesa más de 5 MB.' },
  watermark_logo: { accept: '.png,image/png', extensions: /\.png$/i, maxBytes: 2 * MB, error: 'Subí una imagen PNG.', tooBig: 'El logo pesa más de 2 MB.' },
  intro_video: { accept: '.mp4,video/mp4', extensions: /\.mp4$/i, maxBytes: 50 * MB, error: 'Subí un video MP4.', tooBig: 'El video pesa más de 50 MB.' },
  outro_video: { accept: '.mp4,video/mp4', extensions: /\.mp4$/i, maxBytes: 50 * MB, error: 'Subí un video MP4.', tooBig: 'El video pesa más de 50 MB.' },
};

/** Intro and outro are short on purpose. */
export const MAX_BUMPER_SECONDS = 10;

export function validateFile(field: FileField, file: { name: string; size: number }): string | null {
  const rule = FILE_RULES[field];
  if (!rule.extensions.test(file.name)) return rule.error;
  if (file.size > rule.maxBytes) return rule.tooBig;
  return null;
}

const HEX = /^#[0-9a-fA-F]{6}$/;
export const isHex = (value: string): boolean => HEX.test(value);

/**
 * What the user typed in a hex field, as the form keeps it: "#f2c" → "#FF22CC", "F2C94C" → "#F2C94C"
 * (the app's `normalizeHex`, forgiving a missing "#"). Anything else comes back trimmed and uppercased, for the field to flag it.
 */
export function typedHex(value: string): string {
  const text = value.trim().toUpperCase();
  if (!text) return text;
  const withHash = text.startsWith('#') ? text : `#${text}`;
  return normalizeHex(withHash, '') || withHash;
}

/** The three colors of a draft as they are sent: what was typed, normalised. */
export function withTypedColors(draft: KitDraft): KitDraft {
  return { ...draft, primary_color: typedHex(draft.primary_color), secondary_color: typedHex(draft.secondary_color), accent_color: typedHex(draft.accent_color) };
}

/** Last segment of a path or URL, without the query: "/media/assets/fonts/Inter-Bold.ttf" → "Inter-Bold.ttf". */
export function fileName(url: string | null | undefined): string {
  if (!url) return '';
  const clean = url.split(/[?#]/)[0] ?? '';
  const name = clean.split('/').pop() ?? '';
  try {
    return decodeURIComponent(name);
  } catch {
    return name;
  }
}

/** A word as people write them in a file name: "bold", "Regular", "VF", "2024". A storage suffix is none of these. */
const WORD = /^([A-Z]?[a-z]+|[A-Z]+|\d+)$/;

/** A file in the folder of its kit: ".../assets/logos/<kit id>/logo.png". There the name is the one it was uploaded with. */
const IN_KIT_FOLDER = /\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/[^/]+$/i;

/**
 * The name a stored file (a URL or path of the backend) was uploaded with. Files saved before
 * each kit had its folder got a suffix from the storage when the name was taken (Django: "_" and
 * 7 random letters and digits): "/media/assets/logos/logo_vCHttht.png" → "logo.png".
 * Only for those old paths, and never for a real word ("Inter_Regular.ttf") or a name that is
 * all suffix ("_abcDEF1.png"). Not for a file the user just picked: use its `name`.
 */
export function storedFileName(url: string | null | undefined): string {
  const name = fileName(url);
  if (!url || IN_KIT_FOLDER.test(url.split(/[?#]/)[0] ?? '')) return name;
  return name.replace(/^(.+)_([A-Za-z0-9]{7})(\.[^.]+)?$/, (match, stem: string, suffix: string, extension: string = '') => (WORD.test(suffix) ? match : `${stem}${extension}`));
}

/** Readable family for a font file name or URL, as it is: "InterTight-Bold.ttf" → "Inter Tight". */
export function fontLabel(url: string | null | undefined): string {
  const base = fileName(url).replace(/\.(ttf|otf)$/i, '');
  if (!base) return '';
  const family = base.replace(/[-_ ](thin|extralight|light|regular|medium|semibold|bold|extrabold|black|italic|variable|vf)+$/i, '');
  return family
    .replace(/[-_]+/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .trim();
}

/** The family of a stored font (a URL or path of the backend), without the suffix of the storage. */
export function storedFontLabel(url: string | null | undefined): string {
  return fontLabel(storedFileName(url));
}

/** Only real addresses are loaded in the browser. The mocks answer with paths (`/media/…`) that no server has. */
export function loadableUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return /^(https?:|blob:|data:)/i.test(url) ? url : null;
}

/** The editable copy of a kit. A file is `undefined` (keep what is saved), `null` (remove it) or a File (replace it). */
export interface KitDraft {
  name: string;
  description: string;
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  is_default: boolean;
  watermark_position: WatermarkPosition;
  files: Partial<Record<FileField, File | null>>;
}

export const EMPTY_KIT: Pick<
  BrandKit,
  'name' | 'description' | 'primary_color' | 'secondary_color' | 'accent_color' | 'is_default' | 'watermark_position' | FileField
> = {
  name: '',
  description: '',
  primary_color: '#FFFFFF',
  secondary_color: '#000000',
  accent_color: '#FFFF00',
  is_default: false,
  watermark_position: DEFAULT_POSITION,
  primary_font: null,
  secondary_font: null,
  watermark_logo: null,
  intro_video: null,
  outro_video: null,
};

export type KitSource = typeof EMPTY_KIT;

/**
 * A saved color as the editor can show it. Responses carry any string the backend stored
 * (`red`, `#fff`, lower case); the inputs and the request only take `#RRGGBB`.
 */
export function kitColor(kit: KitSource, field: ColorField): string {
  return normalizeHex(kit[field], EMPTY_KIT[field]);
}

export function draftFrom(kit: KitSource): KitDraft {
  return {
    name: kit.name,
    description: kit.description,
    primary_color: kitColor(kit, 'primary_color'),
    secondary_color: kitColor(kit, 'secondary_color'),
    accent_color: kitColor(kit, 'accent_color'),
    is_default: kit.is_default,
    watermark_position: kit.watermark_position ?? DEFAULT_POSITION,
    files: {},
  };
}

/** What the file field holds after saving the draft: a new File, nothing, or the saved URL. */
export function currentFile(kit: KitSource, draft: KitDraft, field: FileField): { file: File | null; url: string | null } {
  const change = draft.files[field];
  if (change instanceof File) return { file: change, url: null };
  if (change === null) return { file: null, url: null };
  return { file: null, url: kit[field] };
}

const FILE_SENTENCE: Record<FileField, string> = {
  primary_font: 'la fuente principal',
  secondary_font: 'la fuente secundaria',
  watermark_logo: 'el logo',
  intro_video: 'la intro',
  outro_video: 'el outro',
};

/** The parts that differ from what is saved, in the words of the save bar: "el nombre", "el color de la palabra activa"… */
export function changedParts(kit: KitSource, draft: KitDraft): string[] {
  const parts: string[] = [];
  if (draft.name.trim() !== kit.name) parts.push('el nombre');
  if (draft.description !== kit.description) parts.push('la descripción');
  for (const field of COLOR_FIELDS) {
    if (draft[field].toUpperCase() !== kitColor(kit, field)) parts.push(COLOR_LABEL[field].inSentence);
  }
  for (const field of FILE_FIELDS) {
    const change = draft.files[field];
    if (change instanceof File || (change === null && kit[field])) parts.push(FILE_SENTENCE[field]);
  }
  if (draft.watermark_position !== (kit.watermark_position ?? DEFAULT_POSITION)) parts.push('la posición del logo');
  if (draft.is_default !== kit.is_default) parts.push('si es el kit por defecto');
  return parts;
}

/** "Cambiaste el nombre, el color del fondo y la posición del logo." */
export function describeChanges(parts: string[]): string {
  if (parts.length === 0) return '';
  const head = parts.slice(0, -1).join(', ');
  const last = parts[parts.length - 1] as string;
  return `Cambiaste ${head ? `${head} y ${last}` : last}.`;
}

export interface KitErrors {
  name?: string;
  primary_color?: string;
  secondary_color?: string;
  accent_color?: string;
}

export function validateDraft(draft: KitDraft): KitErrors {
  const errors: KitErrors = {};
  const name = draft.name.trim();
  if (!name) errors.name = 'Ponele un nombre al brand kit.';
  else if (name.length > 100) errors.name = 'El nombre puede tener hasta 100 caracteres.';
  for (const field of COLOR_FIELDS) {
    if (!isHex(draft[field])) errors[field] = 'Usá un color hex de 6 dígitos, como #F2C94C.';
  }
  return errors;
}

/** Body for POST (everything) or PATCH (only what changed). Files: a File replaces, '' clears. */
export function buildInput(kit: KitSource, draft: KitDraft, { create, position = true }: { create: boolean; position?: boolean }): Partial<BrandKitInput> {
  const input: Partial<BrandKitInput> = {};
  const name = draft.name.trim();
  if (create || name !== kit.name) input.name = name;
  if (create || draft.description !== kit.description) input.description = draft.description;
  for (const field of COLOR_FIELDS) {
    if (create || draft[field].toUpperCase() !== kitColor(kit, field)) input[field] = draft[field].toUpperCase();
  }
  for (const field of FILE_FIELDS) {
    const change = draft.files[field];
    if (change instanceof File) input[field] = change;
    else if (change === null && kit[field]) input[field] = '';
  }
  // `position: false`: the backend does not have the field yet, so it is not sent.
  if (position && (create || draft.watermark_position !== (kit.watermark_position ?? DEFAULT_POSITION))) input.watermark_position = draft.watermark_position;
  if (create ? draft.is_default : draft.is_default !== kit.is_default) input.is_default = draft.is_default;
  return input;
}
