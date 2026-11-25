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
  // Not in map §4 yet (added by the auto-render billing): nothing was charged for the clips that were left.
  AutoRenderNotPaid: 'No alcanzó el saldo para renderizar los clips que faltaban. Cargá tokens y volvé a intentarlo; los clips siguen ahí.',
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
    case 'AutoRenderNotPaid':
      return 'No alcanzó el saldo para renderizar';
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

/** The notice of the review when "Renderizar sin revisar" could not be paid (map C1 · `metadata.auto_render_skipped`). */
export function autoRenderSkippedMessage(reason: 'insufficient_funds' | 'plan_limit' | undefined): { title: string; text: string } | null {
  if (!reason) return null;
  return {
    title: reason === 'insufficient_funds' ? 'No alcanzó el saldo para renderizar sin revisar' : 'Tu plan no permite renderizar estos clips sin revisar',
    text: 'Revisá y aprobá los clips que quieras.',
  };
}

export type PlanLimitKind = 'duration' | 'resolution' | 'seo' | 'scheduling' | 'crossposting' | 'seats' | 'unknown';

/** Which limit a plan message of PricingEngine talks about. */
function limitFromText(text: string): PlanLimitKind {
  if (/duration/i.test(text)) return 'duration';
  if (/does not support|rendering/i.test(text)) return 'resolution';
  if (/SEO/i.test(text)) return 'seo';
  return 'unknown';
}

/**
 * Which plan limit a 403 is about (dialog D3). Uses the proposed `code`; falls
 * back to the English messages PricingEngine raises today.
 */
export function planLimitKind(error: unknown): PlanLimitKind {
  if (!(error instanceof ApiError)) return 'unknown';
  const code = error.code;
  if (code === 'duration' || code === 'resolution' || code === 'seo' || code === 'scheduling' || code === 'crossposting') return code;
  if (code === 'seats_exceeded') return 'seats';
  return limitFromText(error.message);
}

export type ApproveErrorKind =
  /** The project left the review (someone approved it, or it is already rendering): tell the user and go to Clips. */
  | 'not_awaiting_approval'
  /** The plan does not allow the duration or the resolution of what was approved: dialog D3. */
  | 'plan_limit'
  /** 402: dialog D2. */
  | 'insufficient_funds'
  /** The segments are not valid: a bug of the screen, shown as a generic error. */
  | 'invalid_segments';

/**
 * What an error of `approve-paper-edit` means. Today the backend answers 400
 * `{ error }` for a project that is no longer in review ("…no está en espera de
 * aprobación. Estado actual: rendering") and for a plan limit ("Error en el
 * motor de precios: …"); 409 only when two approvals race. The proposed 409 and
 * 403 with a `code` are recognised too.
 */
export function approveErrorKind(error: unknown): { kind: ApproveErrorKind; message: string; limit?: PlanLimitKind } | null {
  if (!(error instanceof ApiError)) return null;
  const text = error.message;
  if (error.status === 402) return { kind: 'insufficient_funds', message: 'No te alcanza el saldo' };
  if (error.status === 409 || error.code === 'not_awaiting_approval' || /no est[áa] en espera de aprobaci[óo]n|ya se est[áa] renderizando/i.test(text)) {
    return { kind: 'not_awaiting_approval', message: 'Este proyecto ya se aprobó. Te llevamos a los clips.' };
  }
  if (error.isPlanLimit || /motor de precios/i.test(text)) {
    const limit = error.isPlanLimit ? planLimitKind(error) : limitFromText(text);
    return { kind: 'plan_limit', message: 'Tu plan no permite renderizar estos clips. Sacá alguno o cambiá de plan.', limit };
  }
  if (error.status === 400) return { kind: 'invalid_segments', message: 'No pudimos aprobar estos clips. Revisá los recortes y probá de nuevo.' };
  return null;
}

/**
 * The backend answers in English today (`LANGUAGE_CODE = 'en-us'`): DRF's own
 * messages arrive untranslated. These are the ones a form can show under a field.
 */
const FIELD_MESSAGES: Array<[RegExp, string]> = [
  [/^This field is required\.?$/i, 'Este campo es obligatorio.'],
  [/^This field may not be (blank|null)\.?$/i, 'Este campo no puede quedar vacío.'],
  [/^Enter a valid email address\.?$/i, 'Revisá el email.'],
  [/^Enter a valid URL\.?$/i, 'Revisá el link.'],
  [/^Ensure this field has no more than (\d+) characters\.?$/i, 'Máximo $1 caracteres.'],
  [/^Ensure this field has at least (\d+) characters\.?$/i, 'Mínimo $1 caracteres.'],
  [/^"?.*"? is not a valid choice\.?$/i, 'Elegí una de las opciones.'],
  [/^A valid (integer|number) is required\.?$/i, 'Ingresá un número.'],
  [/^(Not a valid|Must be a valid) (boolean|UUID)\.?$/i, 'El valor no es válido.'],
  [/^No file was submitted\.?$/i, 'Elegí un archivo.'],
  [/^The submitted (data was not a file|file is empty)\.?.*$/i, 'El archivo no es válido.'],
  [/already exists\.?$/i, 'Ya existe.'],
];

const SPANISH_HINT = /[áéíóúñ¿¡]|\b(el|la|los|las|un|una|no|que|de|del|para|con|por|tu|tus|es|está|ya|se|más|podés|probá|proyecto|archivo|video|saldo)\b/i;

/** Whether a backend message can be shown as it is (it is written in Spanish). */
export function isSpanish(text: string): boolean {
  return SPANISH_HINT.test(text);
}

/** A field error for the UI: translated when it is one of DRF's English messages, as-is when it is already Spanish. */
export function translateFieldError(message: string, fallback = 'Revisá este campo.'): string {
  for (const [pattern, replacement] of FIELD_MESSAGES) {
    if (pattern.test(message)) return message.replace(pattern, replacement);
  }
  return isSpanish(message) ? message : fallback;
}

/** First error of a field of a 400, ready to show under the control (or undefined). */
export function fieldError(error: unknown, field: string): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  const message = error.fieldErrors[field]?.[0];
  return message ? translateFieldError(message) : undefined;
}

/** A sentence for any error, for toasts and inline banners. Never leaks raw backend text for 5xx, nor untranslated English. */
export function errorMessage(error: unknown, fallback = 'Algo falló de nuestro lado. Probá de nuevo.'): string {
  if (error instanceof NetworkError) return 'No pudimos conectar';
  if (error instanceof ApiError) {
    if (error.status >= 500) return fallback;
    if (error.status === 402) return 'No te alcanza el saldo';
    if (error.isPlanLimit) return 'Tu plan no incluye esto.';
    if (error.status === 401) return 'El email o la contraseña no coinciden';
    if (error.status === 403) return 'No tenés acceso a esto. Pedile a un admin del workspace.';
    if (error.status === 404) return 'No encontramos lo que buscabas.';
    if (error.status === 429) return isSpanish(error.message) ? error.message : 'Demasiados intentos. Probá en unos segundos.';
    const first = Object.values(error.fieldErrors)[0]?.[0];
    if (first && first === error.message) return translateFieldError(first, fallback);
    return error.message && isSpanish(error.message) ? error.message : fallback;
  }
  return fallback;
}
