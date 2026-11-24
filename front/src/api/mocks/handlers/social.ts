import type { Platform } from '../../contracts/common';
import type { CreatePostInput } from '../../contracts/social';
import * as contract from '../../contracts/social';
import { newId, type MockDb, type MockPost, type MockWorkspace } from '../db';
import { badRequest, conflict, MockHttpError, notFound, paginate, route } from '../http';
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
  const length = caption.length + hashtags.reduce((sum, tag) => sum + tag.length + 2, 0);
  if (length > limit) throw badRequest({ caption: [`El texto supera el límite de ${NETWORK_NAME[platform]} (${limit} caracteres).`] });
  if (account && hashtags.length > account.limits.hashtags_max) throw badRequest({ hashtags: [`${NETWORK_NAME[platform]} acepta hasta ${account.limits.hashtags_max} hashtags.`] });
}

/** Validates a batch against plan, accounts and limits, then creates the posts. All or nothing. */
function createPosts(db: MockDb, workspace: MockWorkspace, inputs: CreatePostInput[], userId: string, now: number): MockPost[] {
  const plan = planOf(db, workspace.id);
  const scheduling = inputs.some((input) => input.publish_at && new Date(input.publish_at).getTime() > now + 60_000);
  if (scheduling && plan && !plan.allow_scheduling) {
    throw new MockHttpError(403, { error: 'Tu plan no incluye programar publicaciones.', code: 'scheduling' });
  }
  const perClip = new Map<string, Set<Platform>>();
  for (const input of inputs) perClip.set(input.video_clip, new Set([...(perClip.get(input.video_clip) ?? []), input.platform]));
  if (plan && !plan.allow_crossposting && [...perClip.values()].some((platforms) => platforms.size > 1)) {
    throw new MockHttpError(403, { error: 'Tu plan permite una sola red por publicación.', code: 'crossposting' });
  }

  const drafts = inputs.map((input) => {
    const project = db.projects.find((item) => item.workspace === workspace.id && item.clips.some((clip) => clip.id === input.video_clip));
    const clip = project?.clips.find((item) => item.id === input.video_clip);
    if (!project || !clip) throw badRequest({ video_clip: ['El clip no existe.'] });
    if (clip.status !== 'completed' && clip.status !== 'published') throw badRequest({ video_clip: ['El clip todavía no está renderizado.'] });
    const account = db.socialAccounts.find((item) => item.workspace === workspace.id && item.platform === input.platform);
    if (!account || account.status !== 'connected') {
      throw conflict(`La cuenta de ${NETWORK_NAME[input.platform]} está ${account?.status === 'expired' ? 'vencida' : 'sin conectar'}. Reconectala para publicar.`, 'account_not_connected');
    }
    if (input.platform === 'TIKTOK' && !input.tiktok) throw badRequest({ tiktok: ['Elegí quién puede ver el video.'] });
    if (input.publish_at && new Date(input.publish_at).getTime() < now - 60_000) throw badRequest({ publish_at: ['La fecha ya pasó.'] });
    const seo = Boolean(project.metadata.use_seo);
    const caption = input.caption ?? '';
    const hashtags = input.hashtags ?? [];
    checkText(db, workspace.id, input.platform, caption, hashtags);
    return { input, project, clip, seo, caption, hashtags };
  });

  return drafts.map(({ input, project, clip, seo, caption, hashtags }) => {
    const immediate = !input.publish_at || new Date(input.publish_at).getTime() <= now + 60_000;
    const stamp = new Date(now).toISOString();
    const post: MockPost = {
      id: newId(),
      workspace: workspace.id,
      video_clip: clip.id,
      platform: input.platform,
      publish_at: input.publish_at ?? stamp,
      status: immediate ? 'QUEUED' : 'SCHEDULED',
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

  route(contract.updatePost, ({ db, workspace, params, body, now, require }) => {
    require('post.publish');
    const post = ownPost(db, params.id, workspace.id);
    if (!EDITABLE.has(post.status)) throw conflict('La publicación ya está en cola, publicando o publicada: no se puede editar.');
    if (body.publish_at !== undefined) {
      if (new Date(body.publish_at).getTime() < now - 60_000) throw badRequest({ publish_at: ['La fecha ya pasó.'] });
      post.publish_at = body.publish_at;
      if (post.status === 'DRAFT') post.status = 'SCHEDULED';
    }
    const caption = body.caption ?? post.caption;
    const hashtags = body.hashtags ?? post.hashtags;
    checkText(db, workspace.id, post.platform, caption, hashtags);
    post.caption = caption;
    post.hashtags = hashtags;
    if (body.tiktok !== undefined && post.platform === 'TIKTOK') post.tiktok = body.tiktok;
    post.updated_at = new Date(now).toISOString();
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
];
