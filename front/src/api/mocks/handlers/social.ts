import type { Platform } from '../../contracts/common';
import type { CreatePostInput } from '../../contracts/social';
import * as contract from '../../contracts/social';
import { newId, type MockDb, type MockPost, type MockWorkspace } from '../db';
import { badRequest, conflict, MockHttpError, notFound, paginate, route } from '../http';
import { adoptClip } from '../hybrid';
import { planOf, serializePost } from '../serialize';
import { failPublish } from '../publish-errors';
import { addActivity } from '../sim';

const NETWORK_NAME: Record<Platform, string> = { TIKTOK: 'TikTok', INSTAGRAM_REELS: 'Instagram Reels', YOUTUBE_SHORTS: 'YouTube Shorts' };
/** What a disconnected network fails (fail_network_posts): what was going out on it. */
const GOING_OUT = new Set(['SCHEDULED', 'QUEUED']);
/** Posts that hold their network for the crossposting limit (services/scheduled_posts.py · LIVE). */
const LIVE = new Set(['SCHEDULED', 'QUEUED', 'PROCESSING', 'PUBLISHED']);
/** A `publish_at` this close to now is "now" (NOW_TOLERANCE). */
const NOW_TOLERANCE_MS = 60_000;
/** A post "Publicando" whose last attempt started longer ago than this may be retried or cancelled (POST_STUCK_AFTER). */
export const POST_STUCK_AFTER_MS = 15 * 60_000;
/** One whose worker retry is on its way ("Retry n:"): the broker's visibility timeout plus the longest countdown. */
export const PENDING_RETRY_STUCK_AFTER_MS = (4 * 3600 + 240) * 1000;
const HASHTAG_MAX_CHARS = 100;
/** How long the AI text of a cancelled post waits for the next draft of its clip and network (AI_TEXT_CACHE_TTL). */
const DISCARDED_AI_TEXT_MS = 24 * 3600 * 1000;

function discardedKey(clip: { id: string; start_time: number; end_time: number }, platform: Platform): string {
  return `${clip.id}:${platform}:${clip.start_time}:${clip.end_time}`;
}

/** services/scheduled_posts.is_stuck. */
function isStuck(post: MockPost, now: number): boolean {
  if (post.status !== 'PROCESSING') return false;
  const after = (post.error_log ?? '').startsWith('Retry ') ? PENDING_RETRY_STUCK_AFTER_MS : POST_STUCK_AFTER_MS;
  return now - new Date(post.updated_at).getTime() > after;
}
const TIKTOK_PRIVACY_MISSING = 'Elegí quién puede ver el video.';
const SCHEDULING_DENIED = 'Tu plan no incluye programar publicaciones: publicala ahora o cambiá de plan.';
const CROSSPOSTING_DENIED = 'Tu plan permite publicar cada clip en una sola red. Para publicarlo en varias, cambiá de plan.';
/** apps/integrations/social/accounts.py · check_can_connect. */
const CONNECT_PLAN_DENIED = 'Tu plan no incluye conectar redes. Pasate a un plan pago para conectar las cuentas.';
const CONNECT_EMAIL_NOT_VERIFIED = 'Verificá tu email para conectar redes.';
const SEO_FAILURE = /^(seo_|moderation_)|^(content_blocked|generated_content_blocked|capacity_timeout)$/;
/** services/scheduled_posts.TEXT_MISSING: the 400 of a post that would go out empty after its AI text failed. */
const TEXT_MISSING = 'No pudimos generar el texto con IA y esta publicación no tiene texto: escribilo o regeneralo.';

/**
 * services/scheduled_posts._check_has_text: a post that goes out after its AI text failed (`seo_status:
 * "failed"`) with no caption and no hashtags is a 400 `post_text_missing`, unless the request allows it
 * (`allow_empty_text`). A text still on its way is no failure: the post waits for it.
 */
function checkHasText(post: MockPost, caption: string, hashtags: string[], allowed: boolean | undefined): void {
  if (allowed || caption || hashtags.length > 0 || post.seo_status !== 'failed') return;
  throw badRequest({ error: TEXT_MISSING, code: 'post_text_missing' });
}

/** The 409s of the backend, by what is asked and the status the post is in (`_CONFLICTS`). */
const CONFLICTS: Record<'edit' | 'cancel' | 'retry' | 'seo', Partial<Record<string, [string, string]>>> = {
  edit: {
    QUEUED: ['post_in_flight', 'La publicación ya está en cola: no se puede cambiar.'],
    PROCESSING: ['post_in_flight', 'La publicación se está publicando: no se puede cambiar.'],
    PUBLISHED: ['post_published', 'La publicación ya salió: no se puede cambiar.'],
    FAILED: ['post_failed', 'La publicación falló: reintentala o cancelala.'],
  },
  cancel: {
    PROCESSING: ['post_in_flight', 'La publicación se está publicando: ya no se puede cancelar.'],
    PUBLISHED: ['post_published', 'La publicación ya salió: no se puede cancelar.'],
  },
  retry: {
    DRAFT: ['post_not_failed', 'La publicación no está en error: no hay nada que reintentar.'],
    SCHEDULED: ['post_not_failed', 'La publicación no está en error: no hay nada que reintentar.'],
    QUEUED: ['post_not_failed', 'La publicación no está en error: no hay nada que reintentar.'],
    PROCESSING: ['post_in_flight', 'La publicación se está publicando. Si en unos minutos sigue igual, vas a poder reintentarla.'],
    PUBLISHED: ['post_published', 'La publicación ya salió: no hay nada que reintentar.'],
  },
  seo: {
    QUEUED: ['post_in_flight', 'La publicación ya está saliendo: su texto no cambia.'],
    PROCESSING: ['post_in_flight', 'La publicación ya está saliendo: su texto no cambia.'],
    PUBLISHED: ['post_published', 'La publicación ya salió: su texto no cambia.'],
  },
};

function refuse(kind: keyof typeof CONFLICTS, status: string): void {
  const found = CONFLICTS[kind][status];
  if (found) throw conflict(found[1], found[0]);
}

/** A 400 with the field's sentence; in a batch, also `index`, the position of the post. */
const invalid = (field: string, message: string, index?: number) => badRequest(index === undefined ? { [field]: [message] } : { [field]: [message], index });

/** clean_hashtags: without "#", without repeats (ignoring case); letters, digits or "_". */
function cleanHashtags(values: string[], index?: number): string[] {
  const kept: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const tag = value.trim().replace(/^#+/, '');
    if (!tag) continue;
    if (!/^[\p{L}\p{N}_]+$/u.test(tag)) throw invalid('hashtags', `«${tag.slice(0, 40)}» no es un hashtag válido: usá letras, números o guion bajo, sin espacios.`, index);
    if (tag.length > HASHTAG_MAX_CHARS) throw invalid('hashtags', `Cada hashtag puede tener hasta ${HASHTAG_MAX_CHARS} caracteres.`, index);
    if (seen.has(tag.toLowerCase())) continue;
    seen.add(tag.toLowerCase());
    kept.push(tag);
  }
  return kept;
}

/** Without `allow_crossposting` a clip goes out on one network, counting its live posts (not drafts, not failures). */
function checkCrossposting(db: MockDb, workspace: MockWorkspace, wanted: Map<string, Set<Platform>>, exclude?: string): void {
  const plan = planOf(db, workspace.id);
  if (!plan || plan.allow_crossposting) return;
  for (const [clipId, platforms] of wanted) {
    const networks = new Set(platforms);
    for (const post of db.posts) {
      if (post.workspace === workspace.id && post.video_clip === clipId && post.id !== exclude && LIVE.has(post.status)) networks.add(post.platform);
    }
    if (networks.size > 1) throw new MockHttpError(403, { error: CROSSPOSTING_DENIED, code: 'crossposting' });
  }
}

/** services/scheduled_posts._check_account: each network by the provider's last read (the accounts the screens show). */
function checkAccount(db: MockDb, workspaceId: string, platform: Platform): void {
  const account = db.socialAccounts.find((item) => item.workspace === workspaceId && item.platform === platform);
  if (account?.status === 'expired') throw conflict(`La cuenta de ${NETWORK_NAME[platform]} está vencida. Reconectala para publicar.`, 'account_not_connected');
  if (!account || account.status !== 'connected') {
    throw conflict(`La cuenta de ${NETWORK_NAME[platform]} no está conectada. Conectala para publicar.`, 'account_not_connected');
  }
}

function ownPost(db: MockDb, id: string | undefined, workspaceId: string): MockPost {
  const post = db.posts.find((item) => item.id === id && item.workspace === workspaceId);
  if (!post) throw notFound();
  return post;
}

function checkText(db: MockDb, workspaceId: string, platform: Platform, caption: string, hashtags: string[], index?: number): void {
  const account = db.socialAccounts.find((item) => item.workspace === workspaceId && item.platform === platform);
  const limit = account?.limits.caption_max_chars ?? 2200;
  // compose_caption(): the caption, a blank line and "#a #b".
  const tags = hashtags.filter(Boolean);
  const length = tags.length === 0 ? caption.length : caption.length + 2 + tags.reduce((sum, tag) => sum + tag.length + 1, 0) + (tags.length - 1);
  if (length > limit) throw invalid('caption', `El texto supera el límite de ${NETWORK_NAME[platform]} (${limit} caracteres).`, index);
  if (account && hashtags.length > account.limits.hashtags_max) throw invalid('hashtags', `${NETWORK_NAME[platform]} acepta hasta ${account.limits.hashtags_max} hashtags.`, index);
}

/**
 * Validates a batch and creates the posts, all or nothing, in the backend's order (create_posts): each post's
 * 400s (with `index` in a batch), then the plan (403) and the account (409). A post that goes out is left
 * `SCHEDULED` (for now, or its date) and the simulation queues it on its next tick, as dispatch_scheduled_posts_batch
 * does (right after the request for a post due now; the beat for the others).
 */
function createPosts(db: MockDb, workspace: MockWorkspace, inputs: CreatePostInput[], userId: string, now: number, many = false): MockPost[] {
  const at = (index: number) => (many ? index : undefined);
  // As the backend: every post's own fields first (its serializer), then the repeats, then what needs the data.
  const cleaned = inputs.map((input, index) => cleanHashtags(input.hashtags ?? [], at(index)));
  const pairs = new Set<string>();
  inputs.forEach((input, index) => {
    const pair = `${input.video_clip}:${input.platform}`;
    if (pairs.has(pair)) throw invalid('posts', 'La misma publicación (clip y red) está dos veces.', index);
    pairs.add(pair);
  });

  let later = false;
  const going = new Map<string, Set<Platform>>();
  const drafts = inputs.map((input, index) => {
    // Hybrid: publishing a clip of the real backend works on a stand-in of it.
    const project =
      db.projects.find((item) => item.workspace === workspace.id && item.clips.some((clip) => clip.id === input.video_clip)) ??
      adoptClip(db, workspace.id, input.video_clip)?.project;
    const clip = project?.clips.find((item) => item.id === input.video_clip);
    if (!project || !clip) throw invalid('video_clip', 'El clip no existe.', at(index));
    if (clip.status !== 'completed' && clip.status !== 'published') throw invalid('video_clip', 'El clip todavía no está renderizado.', at(index));
    const caption = input.caption ?? '';
    const hashtags = cleaned[index] ?? [];
    checkText(db, workspace.id, input.platform, caption, hashtags, at(index));
    const draft = Boolean(input.draft);
    let publishAt = input.publish_at ?? new Date(now).toISOString();
    if (!draft) {
      const when = input.publish_at ? new Date(input.publish_at).getTime() : now;
      if (when < now - NOW_TOLERANCE_MS) throw invalid('publish_at', 'La fecha ya pasó.', at(index));
      if (input.platform === 'TIKTOK' && !input.tiktok) throw invalid('tiktok', TIKTOK_PRIVACY_MISSING, at(index));
      if (when > now + NOW_TOLERANCE_MS) later = true;
      else publishAt = input.publish_at ?? new Date(now).toISOString();
      going.set(clip.id, new Set([...(going.get(clip.id) ?? []), input.platform]));
    }
    // seo_requested(project): the project asked for SEO and the plan includes it, checked when the post is created.
    const plan = planOf(db, workspace.id);
    const wantsSeo = Boolean(project.metadata.use_seo) && (!plan || plan.has_seo_optimization) && !(caption && hashtags.length > 0);
    // Like the backend, the AI text of the same clip and network is reused instead of asked for again: another
    // post's, or a cancelled one's of the last 24 h.
    const kept = db.discardedAiText?.[discardedKey(clip, input.platform)];
    const reused = wantsSeo
      ? (db.posts
          .filter((post) => post.workspace === workspace.id && post.video_clip === clip.id && post.platform === input.platform && post.generated_caption && post.generated_hashtags)
          .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0] ??
        (kept && now - kept.at < DISCARDED_AI_TEXT_MS ? { generated_caption: kept.caption, generated_hashtags: kept.hashtags } : undefined))
      : undefined;
    const seo = wantsSeo && !reused;
    return { input, project, clip, seo, reused, caption, hashtags, draft, publishAt };
  });

  const plan = planOf(db, workspace.id);
  if (later && plan && !plan.allow_scheduling) throw new MockHttpError(403, { error: SCHEDULING_DENIED, code: 'scheduling' });
  checkCrossposting(db, workspace, going);
  for (const { input, draft } of drafts) if (!draft) checkAccount(db, workspace.id, input.platform);

  return drafts.map(({ input, project, clip, seo, reused, caption, hashtags, draft, publishAt }) => {
    const immediate = !draft && new Date(publishAt).getTime() <= now + NOW_TOLERANCE_MS;
    const stamp = new Date(now).toISOString();
    const post: MockPost = {
      id: newId(),
      workspace: workspace.id,
      video_clip: clip.id,
      platform: input.platform,
      publish_at: publishAt,
      status: draft ? 'DRAFT' : 'SCHEDULED',
      error_log: null,
      retry_count: 0,
      generated_caption: reused?.generated_caption ?? null,
      generated_hashtags: reused?.generated_hashtags ?? null,
      created_at: stamp,
      updated_at: stamp,
      caption: caption || (reused?.generated_caption ?? ''),
      hashtags: hashtags.length > 0 ? hashtags : (reused?.generated_hashtags ?? []),
      // The backend asks for the AI text when the post is created (unless it reused one or the post brings its own).
      seo_status: seo ? 'pending' : reused ? 'ready' : 'none',
      clip: { id: clip.id, title: clip.title, project_id: project.id, project_title: project.title, duration_seconds: Math.round((clip.end_time - clip.start_time) * 100) / 100, thumbnail_url: null },
      post_url: null,
      error_message: null,
      error_code: null,
      tiktok: input.platform === 'TIKTOK' ? (input.tiktok ?? null) : null,
      // A caption with "falla" makes the publication fail, to see the error state.
      _sim: immediate || seo ? { startedAt: now, fails: /falla/i.test(caption) } : undefined,
    };
    db.posts.push(post);
    if (draft) return post;
    addActivity(db, project, 'post_scheduled', immediate ? `Publicó «${clip.title}» en ${NETWORK_NAME[input.platform]}` : `Programó «${clip.title}» en ${NETWORK_NAME[input.platform]}`, now, { actorId: userId });
    return post;
  });
}

export const socialHandlers = [
  route(contract.listSocialAccounts, ({ db, workspace }) => ({
    data: db.socialAccounts
      .filter((account) => account.workspace === workspace.id)
      .map(({ workspace: _workspace, ...account }) => {
        void _workspace;
        return account;
      }),
  })),

  route(contract.connectSocialAccount, ({ db, workspace, user, body, request, require }) => {
    require('social.manage');
    // The handlers run in the web app's page: its origin is the backend's FRONTEND_URL.
    const app = typeof location === 'undefined' ? null : location.origin;
    // Like the backend, the way back must be the web app's: never an open redirect.
    if (body.return_url && app && new URL(body.return_url).origin !== app) {
      throw badRequest({ return_url: ['La dirección de vuelta tiene que ser de OneCreator.'] });
    }
    // check_can_connect: a paid plan (`allow_social_accounts`; no plan, no limits) and a verified email.
    const plan = planOf(db, workspace.id);
    if (plan && plan.allow_social_accounts === false) throw new MockHttpError(403, { error: CONNECT_PLAN_DENIED, code: 'social_accounts' });
    if (!user.is_email_verified) throw new MockHttpError(403, { error: CONNECT_EMAIL_NOT_VERIFIED, code: 'email_not_verified' });
    // The fake authorization window of the network lives in the app (dev route).
    const origin = app ?? (body.return_url ? new URL(body.return_url).origin : (request.headers.get('Origin') ?? 'http://localhost:3000'));
    return { data: { connect_url: `${origin}/dev/conectar-red?red=${body.platform}` } };
  }),

  route(contract.disconnectSocialAccount, ({ db, workspace, params, now, require }) => {
    require('social.manage');
    const account = db.socialAccounts.find((item) => item.workspace === workspace.id && item.platform === params.platform);
    if (!account) throw notFound();
    account.status = 'disconnected';
    account.username = null;
    account.connected_at = null;
    account.tiktok = null;
    // services/scheduled_posts.fail_network_posts: what was going out on that network fails; drafts stay drafts.
    for (const post of db.posts) {
      if (post.workspace !== workspace.id || post.platform !== account.platform || !GOING_OUT.has(post.status)) continue;
      failPublish(post, 'account_not_connected', 'disconnected');
      delete post._sim;
      post.updated_at = new Date(now).toISOString();
    }
    return { data: undefined };
  }),

  route(contract.listPosts, ({ db, workspace, query }) => {
    const status = query.get('status');
    const platform = query.get('platform');
    const from = query.get('from');
    const to = query.get('to');
    const project = query.get('project');
    const clip = query.get('clip');
    const scoped = db.posts
      .filter((post) => post.workspace === workspace.id)
      .filter((post) => !platform || post.platform === platform)
      .filter((post) => !from || post.publish_at >= from)
      .filter((post) => !to || post.publish_at <= to)
      .filter((post) => !project || post.clip.project_id === project)
      .filter((post) => !clip || post.video_clip === clip);
    const counts: Record<string, number> = { DRAFT: 0, SCHEDULED: 0, QUEUED: 0, PROCESSING: 0, PUBLISHED: 0, FAILED: 0 };
    for (const post of scoped) counts[post.status] = (counts[post.status] ?? 0) + 1;
    const items = scoped.filter((post) => !status || post.status === status).sort((a, b) => a.publish_at.localeCompare(b.publish_at));
    const page = paginate(items, query, contract.listPosts.path);
    return { data: { ...page, results: page.results.map(serializePost), status_counts: counts } };
  }),

  route(contract.createPost, ({ db, workspace, body, user, now, require }) => {
    require('post.publish');
    const [post] = createPosts(db, workspace, [body], user.id, now);
    return { data: serializePost(post as MockPost) };
  }),

  route(contract.createPostsBulk, ({ db, workspace, body, user, now, require }) => {
    require('post.publish');
    return { data: { posts: createPosts(db, workspace, body.posts, user.id, now, true).map(serializePost) } };
  }),

  route(contract.getPost, ({ db, workspace, params }) => ({ data: serializePost(ownPost(db, params.id, workspace.id)) })),

  route(contract.updatePost, ({ db, workspace, params, body, user, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    refuse('edit', post.status);
    const caption = body.caption ?? (post.caption || post.generated_caption || '');
    const hashtags = body.hashtags !== undefined ? cleanHashtags(body.hashtags) : post.hashtags.length > 0 ? post.hashtags : (post.generated_hashtags ?? []);
    checkText(db, workspace.id, post.platform, caption, hashtags);
    const stamp = new Date(now).toISOString();
    if (body.publish_at !== undefined) {
      // `null` (or a date within a minute) publishes now; a later date schedules. Either way the dispatcher queues it.
      const at = body.publish_at === null ? now : new Date(body.publish_at).getTime();
      if (at < now - NOW_TOLERANCE_MS) throw invalid('publish_at', 'La fecha ya pasó.');
      const immediate = at <= now + NOW_TOLERANCE_MS;
      const project = db.projects.find((item) => item.id === post.clip.project_id);
      const clip = project?.clips.find((item) => item.id === post.video_clip);
      if (clip && clip.status !== 'completed' && clip.status !== 'published') throw invalid('video_clip', 'El clip todavía no está renderizado.');
      if (post.platform === 'TIKTOK' && !(body.tiktok ?? post.tiktok)) throw invalid('tiktok', TIKTOK_PRIVACY_MISSING);
      const plan = planOf(db, workspace.id);
      if (!immediate && plan && !plan.allow_scheduling) throw new MockHttpError(403, { error: SCHEDULING_DENIED, code: 'scheduling' });
      checkCrossposting(db, workspace, new Map([[post.video_clip, new Set([post.platform])]]), post.id);
      checkAccount(db, workspace.id, post.platform);
      // A draft that goes out, or a text that changes: moving a scheduled post sends the text it was accepted with.
      if (post.status === 'DRAFT' || body.caption !== undefined || body.hashtags !== undefined) checkHasText(post, caption, hashtags, body.allow_empty_text);
      const wasDraft = post.status === 'DRAFT';
      post.publish_at = body.publish_at ?? stamp;
      post.status = 'SCHEDULED';
      // The failure of the draft's AI text is over once it is sent.
      post.error_log = null;
      if (post.seo_status === 'failed') post.seo_status = 'none';
      if (immediate) post._sim = { ...post._sim, startedAt: now, fails: /falla/i.test(caption) };
      if (wasDraft && project) {
        const verb = immediate ? 'Publicó' : 'Programó';
        addActivity(db, project, 'post_scheduled', `${verb} «${post.clip.title}» en ${NETWORK_NAME[post.platform]}`, now, { actorId: user.id });
      }
    } else if (post.status === 'SCHEDULED' && (body.caption !== undefined || body.hashtags !== undefined)) {
      // Emptying the text of a post that goes out, after its AI text failed.
      checkHasText(post, caption, hashtags, body.allow_empty_text);
    }
    if (body.caption !== undefined) post.caption = body.caption;
    if (body.hashtags !== undefined) post.hashtags = hashtags;
    if ((body.caption !== undefined || body.hashtags !== undefined) && post._sim) {
      // The user's text wins over a "Regenerar" asked for before it (`text_edited_at`): the request stays on
      // (and keeps counting), and the AI text that arrives does not replace this one.
      post._sim.keepText = true;
    }
    if (body.tiktok !== undefined && post.platform === 'TIKTOK') post.tiktok = body.tiktok;
    post.updated_at = stamp;
    return { data: serializePost(post) };
  }),

  route(contract.cancelPost, ({ db, workspace, params, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    // A stuck post may already be public: the web app warns that cancelling does not take it off the network.
    if (!isStuck(post, now)) refuse('cancel', post.status);
    db.posts = db.posts.filter((item) => item.id !== post.id);
    // Its AI text waits 24 h for the next draft of the clip and network (the E1 panel creates them again).
    const clip = db.projects.find((item) => item.id === post.clip.project_id)?.clips.find((item) => item.id === post.video_clip);
    if (clip && post.generated_caption && post.generated_hashtags) {
      db.discardedAiText = { ...db.discardedAiText, [discardedKey(clip, post.platform)]: { caption: post.generated_caption, hashtags: post.generated_hashtags, at: now } };
    }
    return { data: undefined };
  }),

  route(contract.retryPost, ({ db, workspace, params, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    // A post "Publicando" is retried only once it is stuck: before, it may still be going out.
    if (!isStuck(post, now)) refuse('retry', post.status);
    const stamp = new Date(now).toISOString();
    if (post.status === 'FAILED' && SEO_FAILURE.test(post.error_log ?? '')) {
      // Its AI text failed: it never went out. Back to a draft for the user to write the text.
      post.status = 'DRAFT';
      post.seo_status = 'failed';
      post.error_code = null;
      post.error_message = null;
      post.updated_at = stamp;
      return { data: serializePost(post) };
    }
    const project = db.projects.find((item) => item.id === post.clip.project_id);
    const plan = planOf(db, workspace.id);
    const noText = !(post.caption || post.generated_caption) && post.hashtags.length === 0 && !post.generated_hashtags?.length;
    if (post.status === 'FAILED' && noText && project?.metadata.use_seo && (!plan || plan.has_seo_optimization) && post.seo_status !== 'pending') {
      // retry_post: a post of a project with the AI text that would go out with no text (its AI text never came).
      // Back to a draft that says so: the user writes it, regenerates it or confirms (`allow_empty_text`).
      post.status = 'DRAFT';
      post.seo_status = 'failed';
      post.error_log = 'seo_text_missing';
      post.error_code = null;
      post.error_message = null;
      post.updated_at = stamp;
      delete post._sim;
      return { data: serializePost(post) };
    }
    const clip = project?.clips.find((item) => item.id === post.video_clip);
    if (clip && clip.status !== 'completed' && clip.status !== 'published') throw invalid('video_clip', 'El clip todavía no está renderizado.');
    if (post.platform === 'TIKTOK' && !post.tiktok) throw invalid('tiktok', TIKTOK_PRIVACY_MISSING);
    checkCrossposting(db, workspace, new Map([[post.video_clip, new Set([post.platform])]]), post.id);
    checkAccount(db, workspace.id, post.platform);
    // Back to SCHEDULED, for now: the simulation queues it on its next tick, as the backend's dispatcher does after the retry.
    post.status = 'SCHEDULED';
    post.publish_at = stamp;
    post.error_log = null;
    post.error_message = null;
    post.error_code = null;
    post.updated_at = stamp;
    delete post._sim;
    return { data: serializePost(post) };
  }),

  route(contract.regeneratePostSeo, ({ db, workspace, params, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    refuse('seo', post.status);
    const project = db.projects.find((item) => item.id === post.clip.project_id);
    const plan = planOf(db, workspace.id);
    if (!project?.metadata.use_seo || (plan && !plan.has_seo_optimization)) {
      throw new MockHttpError(403, { error: 'Este proyecto no tiene el texto con IA.', code: 'seo' });
    }
    // One on its way already: nothing more is asked for (a double click never pays two generations).
    if (post.seo_status === 'pending' && post._sim) return { status: 202, data: serializePost(post) };
    post.seo_status = 'pending';
    post._sim = { ...post._sim, startedAt: now, regen: (post._sim?.regen ?? 0) + 1 };
    post.updated_at = new Date(now).toISOString();
    return { status: 202, data: serializePost(post) };
  }),
];
