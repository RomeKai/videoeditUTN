import type { Platform } from '../../contracts/common';
import type { CreatePostInput } from '../../contracts/social';
import * as contract from '../../contracts/social';
import { newId, type MockDb, type MockPost, type MockWorkspace } from '../db';
import { badRequest, conflict, MockHttpError, notFound, paginate, route } from '../http';
import { adoptClip } from '../hybrid';
import { planOf, serializePost } from '../serialize';
import { addActivity } from '../sim';

const NETWORK_NAME: Record<Platform, string> = { TIKTOK: 'TikTok', INSTAGRAM_REELS: 'Instagram Reels', YOUTUBE_SHORTS: 'YouTube Shorts' };
const EDITABLE = new Set(['DRAFT', 'SCHEDULED']);

function ownPost(db: MockDb, id: string | undefined, workspaceId: string): MockPost {
  const post = db.posts.find((item) => item.id === id && item.workspace === workspaceId);
  if (!post) throw notFound();
  return post;
}

function checkText(db: MockDb, workspaceId: string, platform: Platform, caption: string, hashtags: string[]): void {
  const account = db.socialAccounts.find((item) => item.workspace === workspaceId && item.platform === platform);
  const limit = account?.limits.caption_max_chars ?? 2200;
  // compose_caption(): the caption, a blank line and "#a #b".
  const tags = hashtags.filter(Boolean);
  const length = tags.length === 0 ? caption.length : caption.length + 2 + tags.reduce((sum, tag) => sum + tag.length + 1, 0) + (tags.length - 1);
  if (length > limit) throw badRequest({ caption: [`El texto supera el límite de ${NETWORK_NAME[platform]} (${limit} caracteres).`] });
  if (account && hashtags.length > account.limits.hashtags_max) throw badRequest({ hashtags: [`${NETWORK_NAME[platform]} acepta hasta ${account.limits.hashtags_max} hashtags.`] });
}

/** Validates a batch against plan, accounts and limits, then creates the posts. All or nothing. */
function createPosts(db: MockDb, workspace: MockWorkspace, inputs: CreatePostInput[], userId: string, now: number): MockPost[] {
  const plan = planOf(db, workspace.id);
  const scheduling = inputs.some((input) => !input.draft && input.publish_at && new Date(input.publish_at).getTime() > now + 60_000);
  if (scheduling && plan && !plan.allow_scheduling) {
    throw new MockHttpError(403, { error: 'Tu plan no incluye programar publicaciones.', code: 'scheduling' });
  }
  const perClip = new Map<string, Set<Platform>>();
  for (const input of inputs) {
    // Drafts publish nothing: they only count when they are sent.
    if (!input.draft) perClip.set(input.video_clip, new Set([...(perClip.get(input.video_clip) ?? []), input.platform]));
  }
  if (plan && !plan.allow_crossposting && [...perClip.values()].some((platforms) => platforms.size > 1)) {
    throw new MockHttpError(403, { error: 'Tu plan permite una sola red por publicación.', code: 'crossposting' });
  }

  const drafts = inputs.map((input) => {
    // Hybrid: publishing a clip of the real backend works on a stand-in of it.
    const project =
      db.projects.find((item) => item.workspace === workspace.id && item.clips.some((clip) => clip.id === input.video_clip)) ??
      adoptClip(db, workspace.id, input.video_clip)?.project;
    const clip = project?.clips.find((item) => item.id === input.video_clip);
    if (!project || !clip) throw badRequest({ video_clip: ['El clip no existe.'] });
    if (clip.status !== 'completed' && clip.status !== 'published') throw badRequest({ video_clip: ['El clip todavía no está renderizado.'] });
    const account = db.socialAccounts.find((item) => item.workspace === workspace.id && item.platform === input.platform);
    if (!account || account.status !== 'connected') {
      throw conflict(`La cuenta de ${NETWORK_NAME[input.platform]} está ${account?.status === 'expired' ? 'vencida' : 'sin conectar'}. Reconectala para publicar.`, 'account_not_connected');
    }
    if (!input.draft && input.platform === 'TIKTOK' && !input.tiktok) throw badRequest({ tiktok: ['Elegí quién puede ver el video.'] });
    if (!input.draft && input.publish_at && new Date(input.publish_at).getTime() < now - 60_000) throw badRequest({ publish_at: ['La fecha ya pasó.'] });
    // seo_requested(project): the project asked for SEO and the plan includes it, checked when the post is created.
    const seo = Boolean(project.metadata.use_seo) && (!plan || plan.has_seo_optimization);
    const caption = input.caption ?? '';
    const hashtags = input.hashtags ?? [];
    checkText(db, workspace.id, input.platform, caption, hashtags);
    return { input, project, clip, seo, caption, hashtags };
  });

  return drafts.map(({ input, project, clip, seo, caption, hashtags }) => {
    const draft = Boolean(input.draft);
    const immediate = !draft && (!input.publish_at || new Date(input.publish_at).getTime() <= now + 60_000);
    const stamp = new Date(now).toISOString();
    const post: MockPost = {
      id: newId(),
      workspace: workspace.id,
      video_clip: clip.id,
      platform: input.platform,
      publish_at: input.publish_at ?? stamp,
      status: draft ? 'DRAFT' : immediate ? 'QUEUED' : 'SCHEDULED',
      error_log: null,
      retry_count: 0,
      generated_caption: null,
      generated_hashtags: null,
      created_at: stamp,
      updated_at: stamp,
      caption,
      hashtags,
      // The existing signal generates the SEO text when the post is created.
      seo_status: seo ? 'pending' : 'none',
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

  route(contract.connectSocialAccount, ({ body, request, require }) => {
    require('social.manage');
    // The fake authorization window of the network lives in the app (dev route).
    const origin = body.return_url ? new URL(body.return_url).origin : (request.headers.get('Origin') ?? 'http://localhost:3000');
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
    for (const post of db.posts) {
      if (post.workspace !== workspace.id || post.platform !== account.platform || !EDITABLE.has(post.status)) continue;
      post.status = 'FAILED';
      post.error_code = 'account_not_connected';
      post.error_message = `La cuenta de ${NETWORK_NAME[post.platform]} se desconectó. Reconectala y reintentá.`;
      post.error_log = 'AccountDisconnected';
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
    return { data: { posts: createPosts(db, workspace, body.posts, user.id, now).map(serializePost) } };
  }),

  route(contract.getPost, ({ db, workspace, params }) => ({ data: serializePost(ownPost(db, params.id, workspace.id)) })),

  route(contract.updatePost, ({ db, workspace, params, body, user, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    if (!EDITABLE.has(post.status)) throw conflict('La publicación ya está en cola, publicando o publicada: no se puede editar.');
    const caption = body.caption ?? post.caption;
    const hashtags = body.hashtags ?? post.hashtags;
    checkText(db, workspace.id, post.platform, caption, hashtags);
    const stamp = new Date(now).toISOString();
    if (body.publish_at !== undefined) {
      // `null` (or a date that is already here) publishes now; a later date schedules.
      const at = body.publish_at === null ? now : new Date(body.publish_at).getTime();
      if (at < now - 60_000) throw badRequest({ publish_at: ['La fecha ya pasó.'] });
      const immediate = at <= now + 60_000;
      const plan = planOf(db, workspace.id);
      if (!immediate && plan && !plan.allow_scheduling) {
        throw new MockHttpError(403, { error: 'Tu plan no incluye programar publicaciones.', code: 'scheduling' });
      }
      const account = db.socialAccounts.find((item) => item.workspace === workspace.id && item.platform === post.platform);
      if (!account || account.status !== 'connected') {
        throw conflict(`La cuenta de ${NETWORK_NAME[post.platform]} está ${account?.status === 'expired' ? 'vencida' : 'sin conectar'}. Reconectala para publicar.`, 'account_not_connected');
      }
      if (post.platform === 'TIKTOK' && !(body.tiktok ?? post.tiktok)) throw badRequest({ tiktok: ['Elegí quién puede ver el video.'] });
      const wasDraft = post.status === 'DRAFT';
      post.publish_at = body.publish_at ?? stamp;
      post.status = immediate ? 'QUEUED' : 'SCHEDULED';
      if (immediate) post._sim = { ...post._sim, startedAt: now, fails: /falla/i.test(caption) };
      if (wasDraft) {
        const project = db.projects.find((item) => item.id === post.clip.project_id);
        if (project) {
          const verb = immediate ? 'Publicó' : 'Programó';
          addActivity(db, project, 'post_scheduled', `${verb} «${post.clip.title}» en ${NETWORK_NAME[post.platform]}`, now, { actorId: user.id });
        }
      }
    }
    post.caption = caption;
    post.hashtags = hashtags;
    if (body.tiktok !== undefined && post.platform === 'TIKTOK') post.tiktok = body.tiktok;
    post.updated_at = stamp;
    return { data: serializePost(post) };
  }),

  route(contract.cancelPost, ({ db, workspace, params, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    if (post.status === 'PROCESSING' || post.status === 'PUBLISHED') throw conflict('Ya se está publicando o ya se publicó.');
    db.posts = db.posts.filter((item) => item.id !== post.id);
    return { data: undefined };
  }),

  route(contract.retryPost, ({ db, workspace, params, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    if (post.status !== 'FAILED' && post.status !== 'PROCESSING') throw conflict('La publicación no está en error.');
    const account = db.socialAccounts.find((item) => item.workspace === workspace.id && item.platform === post.platform);
    if (!account || account.status !== 'connected') {
      throw conflict(`La cuenta de ${NETWORK_NAME[post.platform]} no está conectada. Reconectala para reintentar.`, 'account_not_connected');
    }
    post.status = 'QUEUED';
    post.error_log = null;
    post.error_message = null;
    post.error_code = null;
    post.updated_at = new Date(now).toISOString();
    post._sim = { startedAt: now };
    return { data: serializePost(post) };
  }),

  route(contract.regeneratePostSeo, ({ db, workspace, params, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    if (post.status === 'PROCESSING' || post.status === 'PUBLISHED') throw conflict('La publicación ya salió: su texto no cambia.');
    const project = db.projects.find((item) => item.id === post.clip.project_id);
    const plan = planOf(db, workspace.id);
    if (!project?.metadata.use_seo || (plan && !plan.has_seo_optimization)) {
      throw new MockHttpError(403, { error: 'Este proyecto no tiene el texto con IA.', code: 'seo' });
    }
    post.seo_status = 'pending';
    post._sim = { ...post._sim, startedAt: post._sim?.startedAt ?? now, regen: (post._sim?.regen ?? 0) + 1 };
    // The generation takes its two seconds from this request, without restarting a publication in flight.
    if (post.status !== 'QUEUED') post._sim.startedAt = now;
    post.updated_at = new Date(now).toISOString();
    return { status: 202, data: serializePost(post) };
  }),
];
