import type { UploadErrorCode } from '@/api/contracts/projects';
import { ApiError, NetworkError } from '@/api/errors';
import { formatMinutes } from './format';

const MODERATION = 'No podemos procesar este video porque su contenido no cumple nuestras políticas. Si creés que es un error, escribinos.';
const GENERIC = 'Algo falló de nuestro lado. Ya te devolvimos los tokens; podés intentarlo de nuevo.';
const INGESTION_STUCK = 'El proceso se trabó y te devolvimos los tokens. Podés intentarlo de nuevo.';

const PIPELINE_MESSAGES: Record<string, string> = {
  // Only the content itself. `ModerationBlockedError` is ours (the moderation key or its quota): it gets the
  // generic message, never a text that blames the video (back/apps/core/security.py).
  UnsafeContentError: MODERATION,
  CapacityTimeoutError: 'Hay mucha demanda en este momento y no pudimos procesar tu video. Volvé a intentarlo en unos minutos.',
  AINoSpeechDetectedError: 'No encontramos voz en el video. OneCreator elige clips a partir de lo que se dice.',
  AIEmptySelectionError: 'No encontramos momentos que funcionen como clips. Probá con otro video o cambiá el criterio.',
  SoftTimeLimitExceeded: 'El video tardó demasiado en procesarse. Probá con uno más corto.',
  // Not in map §4 yet (added by the auto-render billing): nothing was charged for the clips that were left.
  AutoRenderNotPaid: 'No alcanzó el saldo para renderizar los clips que faltaban. Cargá tokens y volvé a intentarlo; los clips siguen ahí.',
  // Not in map §4 yet: the creation could not queue the processing (broker down); the reservation went back.
  IngestionNotQueued: 'No pudimos empezar a procesar tu video y no se cobró nada. Creá el proyecto de nuevo en unos minutos.',
  // Link projects (back/apps/videos/tasks/shared.py · download_from_youtube): refused when the video is fetched, never retried.
  UnsupportedLinkError:
    'No pudimos usar este link. Tiene que ser el link de un solo video público de YouTube, Vimeo, Twitch o Google Drive: no una lista, un canal ni una transmisión en vivo.',
  // Over the plan's limit only: another plan would take the video.
  LinkTooLongError: 'El video del link dura más de lo que permite tu plan. Probá con uno más corto o cambiá de plan.',
  // Over the app's limit (AI_UPLOAD_MAX_DURATION_SECONDS, 3 h): no plan would take it.
  LinkTooLongForAppError: 'El video del link dura más de 3 horas, el máximo que acepta OneCreator. Probá con uno más corto.',
  LinkTooLargeError: 'El video del link pesa más de lo que podemos descargar. Probá con una versión más liviana o subí el archivo.',
  // Not in map §4 yet: the ingestion made no progress and recover_stuck_renders returned the reservation (it can be re-dispatched).
  IngestionStuck: INGESTION_STUCK,
};

/**
 * The same messages without "te devolvimos los tokens", for a box that already says how many came back
 * ("Te devolvimos 6,30 tokens"): the sentence is not said twice.
 */
const WITHOUT_REFUND: Record<string, string> = {
  [GENERIC]: 'Algo falló de nuestro lado. Podés intentarlo de nuevo.',
  [INGESTION_STUCK]: 'El proceso se trabó. Podés intentarlo de nuevo.',
};

/**
 * `pipeline_error_code` (class name of the exception) → message for the user (map §4). Unknown codes get the
 * generic one. `refundShown`: the amount returned is shown next to it, so the message does not repeat it.
 */
export function pipelineErrorMessage(code: string | null | undefined, { refundShown = false }: { refundShown?: boolean } = {}): string {
  const message = (code && PIPELINE_MESSAGES[code]) || GENERIC;
  return (refundShown && WITHOUT_REFUND[message]) || message;
}

/**
 * True when moderation stopped the project because of its content: opens dialog D5 (never says which category).
 * `ModerationBlockedError` is not that: the moderation service could not be used, whatever the video says.
 */
export function isModerationError(code: string | null | undefined): boolean {
  return code === 'UnsafeContentError';
}

/** Short titles for the red box of a failed project. */
export function pipelineErrorTitle(code: string | null | undefined): string {
  switch (code) {
    case 'UnsafeContentError':
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
    case 'IngestionNotQueued':
      return 'No pudimos empezar a procesar el video';
    case 'UnsupportedLinkError':
      return 'No pudimos usar este link';
    case 'LinkTooLongError':
      return 'El video del link es muy largo para tu plan';
    case 'LinkTooLongForAppError':
      return 'El video del link dura más de 3 horas';
    case 'LinkTooLargeError':
      return 'El video del link es muy pesado';
    case 'IngestionStuck':
      return 'El proceso se trabó';
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

/**
 * The backend's sentence for a project that left the review. Its source file is
 * saved with the wrong encoding today, so the text arrives as "no estÃ¡ en espera
 * de aprobaciÃ³n. Estado actual: rendering"; the pattern only uses the parts that
 * read the same broken and fixed.
 */
const NOT_AWAITING_APPROVAL = /espera de aprobaci|Estado actual:|ya se est\S{1,2} renderizando/i;

/**
 * Text that was UTF-8 read as Latin-1 ("estÃ¡") back to what it meant ("está").
 * Anything that is not that kind of damage comes back untouched.
 */
export function repairEncoding(text: string): string {
  if (!/[ÃÂ][\u0080-\u00BF]/.test(text)) return text;
  try {
    const bytes = Uint8Array.from(text, (char) => char.charCodeAt(0));
    if (Array.from(text).some((char) => char.charCodeAt(0) > 0xff)) return text;
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return text;
  }
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
/** "Los segmentos aprobados son inválidos: cada segmento puede durar hasta 1800 segundos. Dividí los más largos en varios." */
const SEGMENT_TOO_LONG = /cada segmento puede durar hasta \d+ segundos/i;

export function approveErrorKind(error: unknown): { kind: ApproveErrorKind; message: string; limit?: PlanLimitKind } | null {
  if (!(error instanceof ApiError)) return null;
  const text = error.message;
  if (error.status === 402) return { kind: 'insufficient_funds', message: 'No te alcanza el saldo' };
  if (error.status === 409 || error.code === 'not_awaiting_approval' || NOT_AWAITING_APPROVAL.test(text)) {
    return { kind: 'not_awaiting_approval', message: 'Este proyecto ya se aprobó. Te llevamos a los clips.' };
  }
  if (error.isPlanLimit || /motor de precios/i.test(text)) {
    const limit = error.isPlanLimit ? planLimitKind(error) : limitFromText(text);
    return { kind: 'plan_limit', message: 'Tu plan no permite renderizar estos clips. Sacá alguno o cambiá de plan.', limit };
  }
  if (error.status === 400) {
    // The one case where the server's sentence says what to do (PR #124): a segment over the render's limit.
    const tooLong = SEGMENT_TOO_LONG.test(text);
    return { kind: 'invalid_segments', message: tooLong ? repairEncoding(text) : 'No pudimos aprobar estos clips. Revisá los recortes y probá de nuevo.' };
  }
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

/** The sentences of a role 403 (back/apps/core/permissions.py · DENIED and EDITOR_DELETE_DENIED). */
const ROLE_DENIED = /^(Con el rol \S+ podés ver|Solo el dueño|Como Editor solo podés)/;

/** For a write that timed out (`NetworkError.outcomeUnknown`). The data it could have changed is read again by the query client. */
export const OUTCOME_UNKNOWN_MESSAGE = 'No recibimos la respuesta. Puede que se haya hecho igual: revisá antes de probar de nuevo.';

/** A sentence for any error, for toasts and inline banners. Never leaks raw backend text for 5xx, nor untranslated English. */
export function errorMessage(error: unknown, fallback = 'Algo falló de nuestro lado. Probá de nuevo.'): string {
  // A write that got no answer may have been done: "try again" would be the wrong advice.
  if (error instanceof NetworkError) return error.outcomeUnknown ? OUTCOME_UNKNOWN_MESSAGE : 'No pudimos conectar';
  if (error instanceof ApiError) {
    // 501: something this server does not offer yet (payments without a provider) says so in Spanish.
    if (error.status === 501 && isSpanish(error.message)) return repairEncoding(error.message);
    if (error.status >= 500) return fallback;
    if (error.status === 402) return 'No te alcanza el saldo';
    if (error.isPlanLimit) return 'Tu plan no incluye esto.';
    if (error.status === 401) return 'El email o la contraseña no coinciden';
    // The backend says which role is missing, in Spanish (`WorkspaceRolePermission`): that sentence is the message.
    if (error.status === 403) return ROLE_DENIED.test(error.message) ? repairEncoding(error.message) : 'No tenés acceso a esto. Pedile a un admin del workspace.';
    if (error.status === 404) return 'No encontramos lo que buscabas.';
    if (error.status === 429) return isSpanish(error.message) ? error.message : 'Demasiados intentos. Probá en unos segundos.';
    const first = Object.values(error.fieldErrors)[0]?.[0];
    if (first && first === error.message) return translateFieldError(first, fallback);
    const message = repairEncoding(error.message);
    return message && isSpanish(message) ? message : fallback;
  }
  return fallback;
}
