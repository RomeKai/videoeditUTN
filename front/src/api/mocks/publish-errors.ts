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
