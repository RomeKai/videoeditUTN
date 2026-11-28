import type { Platform } from '../contracts/common';
import type { MockPost } from './db';

/** The network as the backend names it in its messages (ScheduledPost.get_platform_display). */
export const NETWORK_DISPLAY: Record<Platform, string> = { TIKTOK: 'TikTok', INSTAGRAM_REELS: 'Instagram Reels', YOUTUBE_SHORTS: 'YouTube Shorts' };

/**
 * The codes the publisher writes first in `error_log` and the sentence the API gives for each
 * (back/apps/videos/serializers_social.py · PUBLISH_ERROR_MESSAGES). Any other failure is `publish_failed`.
 */
export const PUBLISH_ERROR_MESSAGES: Record<string, (network: string) => string> = {
  account_not_connected: (network) => `La cuenta de ${network} no está conectada. Conectala y reintentá.`,
  clip_too_long: (network) => `El clip dura más de lo que acepta ${network}. Acortalo y reintentá.`,
  account_expired: (network) => `La cuenta de ${network} venció. Reconectala y reintentá.`,
  quota_exceeded: () => 'Se alcanzó el límite de publicaciones de este mes. Probá de nuevo más adelante.',
  tiktok_options_missing: () => 'Faltó elegir quién puede ver el video en TikTok. Cancelá esta publicación y creala de nuevo.',
  duplicate_content: (network) =>
    `${network} ya tiene una publicación igual de los últimos dos días: puede que esta ya haya salido. Revisá la cuenta antes de reintentar.`,
  provider_rejected: (network) => `${network} rechazó la publicación. Revisá el video y el texto, y reintentá.`,
  provider_unavailable: (network) => `${network} no respondió después de varios intentos. Probá de nuevo en unos minutos.`,
  outcome_unknown: (network) => `No sabemos si se publicó: ${network} no respondió a tiempo. Revisá la cuenta antes de reintentar.`,
};
export const PUBLISH_FAILED_MESSAGE = 'No pudimos publicar el post. Probá de nuevo en unos minutos.';

/** A post the publisher failed, as the backend leaves it: `<code> (<detail>)` in `error_log`, and the API's code and sentence. */
export function failPublish(post: Pick<MockPost, 'platform' | 'status' | 'error_log' | 'error_code' | 'error_message'>, code: string, detail?: string): void {
  const message = PUBLISH_ERROR_MESSAGES[code];
  post.status = 'FAILED';
  post.error_log = detail ? `${code} (${detail})` : code;
  post.error_code = message ? code : 'publish_failed';
  post.error_message = message ? message(NETWORK_DISPLAY[post.platform]) : PUBLISH_FAILED_MESSAGE;
}

/** The codes that mean "it may be out" (back/apps/notifications/services.py · POST_MAYBE_OUT). */
const MAYBE_OUT = new Set(['outcome_unknown', 'duplicate_content']);

type FailedPost = Pick<MockPost, 'id' | 'platform' | 'error_code' | 'error_message'> & { clip: { title: string; project_id: string; project_title?: string } };

/**
 * The bell's `post_failed` of a post that just turned FAILED, as the backend writes it
 * (apps/notifications/services.py · post_failed_message): the clip (its project when it has no
 * title) and the network in the title, the API's `error_message` as the body.
 */
export function postFailedNotification(post: FailedPost): { type: 'post_failed'; title: string; body: string; project_id: string; post_id: string } {
  const name = post.clip.title || post.clip.project_title || '';
  const network = NETWORK_DISPLAY[post.platform];
  const what = MAYBE_OUT.has(post.error_code ?? '') ? 'no sabemos si se publicó en' : 'no se pudo publicar en';
  return { type: 'post_failed', title: `${name}: ${what} ${network}`, body: post.error_message ?? '', project_id: post.clip.project_id, post_id: post.id };
}

/** The one `post_failed` of a disconnected network with several posts that will not go out (network_disconnected_message). */
export function networkDisconnectedNotification(platform: Platform, posts: number): { type: 'post_failed'; title: string; body: string } {
  const network = NETWORK_DISPLAY[platform];
  return { type: 'post_failed', title: `Se desconectó ${network}: ${posts} publicaciones no van a salir`, body: `Reconectá la cuenta de ${network} y reintentalas desde Publicaciones.` };
}
