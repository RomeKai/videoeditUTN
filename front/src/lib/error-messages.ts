import type { UploadErrorCode } from '@/api/contracts/projects';
import { ApiError, NetworkError } from '@/api/errors';
import { formatMinutes } from './format';

const MODERATION = 'No podemos procesar este video porque su contenido no cumple nuestras políticas. Si creés que es un error, escribinos.';
const GENERIC = 'Algo falló de nuestro lado. Ya te devolvimos los tokens; podés intentarlo de nuevo.';

const PIPELINE_MESSAGES: Record<string, string> = {
  UnsafeContentError: MODERATION,
  ModerationBlockedError: MODERATION,
  CapacityTimeoutError: 'Hay mucha demanda en este momento y no pudimos procesar tu video. Volvé a intentarlo en unos minutos.',
  AINoSpeechDetectedError: 'No encontramos voz en el video. OneCreator elige clips a partir de lo que se dice.',
  AIEmptySelectionError: 'No encontramos momentos que funcionen como clips. Probá con otro video o cambiá el criterio.',
  SoftTimeLimitExceeded: 'El video tardó demasiado en procesarse. Probá con uno más corto.',
};

/** `pipeline_error_code` (class name of the exception) → message for the user (map §4). Unknown codes get the generic one. */
export function pipelineErrorMessage(code: string | null | undefined): string {
  return (code && PIPELINE_MESSAGES[code]) || GENERIC;
}

/** True when the project was stopped by moderation: opens dialog D5 (never says which category). */
export function isModerationError(code: string | null | undefined): boolean {
  return code === 'UnsafeContentError' || code === 'ModerationBlockedError';
}

/** Short titles for the red box of a failed project. */
export function pipelineErrorTitle(code: string | null | undefined): string {
  switch (code) {
    case 'UnsafeContentError':
    case 'ModerationBlockedError':
      return 'No podemos procesar este video';
    case 'CapacityTimeoutError':
      return 'Hay mucha demanda';
    case 'AINoSpeechDetectedError':
      return 'No encontramos voz en el video';
    case 'AIEmptySelectionError':
      return 'No encontramos momentos para clips';
    case 'SoftTimeLimitExceeded':
      return 'El video tardó demasiado';
    default:
      return 'Algo falló de nuestro lado';
  }
}

/** InvalidUploadError.code → message next to the file field (map §4). `maxSeconds` is the plan's limit for `too_long`. */
export function uploadErrorMessage(code: UploadErrorCode, options: { maxSeconds?: number } = {}): string {
  switch (code) {
    case 'not_a_video':
      return 'Este archivo no es un video.';
    case 'unreadable':
    case 'no_duration':
    case 'no_video_stream':
      return 'No pudimos leer el video. Probá exportarlo de nuevo en MP4.';
    case 'too_long':
      return options.maxSeconds
        ? `El video dura más de lo que permite tu plan (máx. ${formatMinutes(options.maxSeconds)}).`
        : 'El video dura más de lo que permite tu plan.';
    case 'too_large':
      return 'La resolución del video es mayor a 4096 px. Exportalo en 4K o menos y volvé a subirlo.';
    case 'too_small':
      return 'La resolución del video es muy baja (mínimo 240 px). Probá con una versión de mejor calidad.';
  }
}

/**
 * The backend does not send InvalidUploadError.code in the 400 body today, only
 * its fixed message (services/media_validation.py · MESSAGES). Until it does,
 * the code is recognised from the text.
 */
const UPLOAD_MESSAGE_HINTS: Array<[UploadErrorCode, RegExp]> = [
  ['not_a_video', /no es un video válido|Solo se permiten archivos de video/i],
  ['no_video_stream', /no tiene una pista de video/i],
  ['no_duration', /determinar la duración/i],
  ['unreadable', /No pudimos leer el video/i],
  ['too_long', /supera la duración máxima|puede durar hasta/i],
  ['too_large', /supera el máximo admitido/i],
  ['too_small', /demasiado baja/i],
];

export function uploadErrorCode(error: unknown): UploadErrorCode | null {
  if (!(error instanceof ApiError) || error.status !== 400) return null;
  const direct = error.fieldCodes.source_file ?? error.fieldCodes.project_type;
  if (direct && UPLOAD_MESSAGE_HINTS.some(([code]) => code === direct)) return direct as UploadErrorCode;
  const text = [...(error.fieldErrors.source_file ?? []), ...(error.fieldErrors.project_type ?? [])].join(' ');
  for (const [code, pattern] of UPLOAD_MESSAGE_HINTS) if (pattern.test(text)) return code;
  return null;
}

export type PlanLimitKind = 'duration' | 'resolution' | 'seo' | 'scheduling' | 'crossposting' | 'seats' | 'unknown';

/**
 * Which plan limit a 403 is about (dialog D3). Uses the proposed `code`; falls
 * back to the English messages PricingEngine raises today.
 */
export function planLimitKind(error: unknown): PlanLimitKind {
  if (!(error instanceof ApiError) || error.status !== 403) return 'unknown';
  const code = error.code;
  if (code === 'duration' || code === 'resolution' || code === 'seo' || code === 'scheduling' || code === 'crossposting') return code;
  if (code === 'seats_exceeded') return 'seats';
  const text = error.message;
  if (/duration/i.test(text)) return 'duration';
  if (/does not support|rendering/i.test(text)) return 'resolution';
  if (/SEO/i.test(text)) return 'seo';
  return 'unknown';
}

/** A sentence for any error, for toasts and inline banners. Never leaks raw backend text for 5xx. */
export function errorMessage(error: unknown, fallback = 'Algo falló de nuestro lado. Probá de nuevo.'): string {
  if (error instanceof NetworkError) return 'No pudimos conectar';
  if (error instanceof ApiError) {
    if (error.status >= 500) return fallback;
    if (error.status === 403 && !error.isPlanLimit) return 'No tenés acceso a esto. Pedile a un admin del workspace.';
    if (error.status === 404) return 'No encontramos lo que buscabas.';
    if (error.status === 429) return 'Demasiados intentos. Probá en unos segundos.';
    return error.message || fallback;
  }
  return fallback;
}
