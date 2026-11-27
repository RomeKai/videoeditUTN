import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { Id, IsoDateTime, NoContent, PageQuery, Platform, paginated } from './common';
import { PostStatus } from './projects';

/* ── Social accounts (Ayrshare profile of the workspace) ─────────────────── */

export const SocialAccountStatus = z.enum(['connected', 'expired', 'disconnected']);
export type SocialAccountStatus = z.infer<typeof SocialAccountStatus>;

/** Options TikTok audits for direct posting (map E1.4). */
export const TikTokPrivacy = z.enum(['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY']);
export type TikTokPrivacy = z.infer<typeof TikTokPrivacy>;

export const SocialAccount = z.object({
  platform: Platform,
  status: SocialAccountStatus,
  /** "@estudionorte"; null when disconnected. */
  username: z.string().nullable(),
  connected_at: IsoDateTime.nullable(),
  /** Limits the publish panel checks. */
  limits: z.object({
    caption_max_chars: z.number().int().positive(),
    hashtags_max: z.number().int().positive(),
    max_duration_seconds: z.number().int().positive(),
    aspect_ratios: z.array(z.string()),
  }),
  /** Only for TikTok: what the creator's account allows. */
  tiktok: z
    .object({
      privacy_options: z.array(TikTokPrivacy),
      comment_disabled: z.boolean(),
      duet_disabled: z.boolean(),
      stitch_disabled: z.boolean(),
    })
    .nullable(),
});
export type SocialAccount = z.infer<typeof SocialAccount>;

/* ── Scheduled posts ─────────────────────────────────────────────────────── */

export const TikTokOptions = z.object({
  /** No default: the user must choose (the Publish button stays disabled until then). */
  privacy_level: TikTokPrivacy,
  allow_comment: z.boolean(),
  allow_duet: z.boolean(),
  allow_stitch: z.boolean(),
  /** Commercial content disclosure. */
  brand_organic: z.boolean(),
  branded_content: z.boolean(),
});
export type TikTokOptions = z.infer<typeof TikTokOptions>;

/** ScheduledPost (apps/videos/models.py) plus what E1/E2 need to show and edit it. */
export const ScheduledPost = z.object({
  id: Id,
  video_clip: Id,
  platform: Platform,
  /** UTC. */
  publish_at: IsoDateTime,
  status: PostStatus,
  /** Raw exception text. Not shown: the UI uses `error_message`. */
  error_log: z.string().nullable(),
  retry_count: z.number().int().nonnegative(),
  generated_caption: z.string().nullable(),
  generated_hashtags: z.array(z.string()).nullable(),
  created_at: IsoDateTime,
  updated_at: IsoDateTime,
  /* Proposed (gap 8) */
  /** What will be published. Starts as the AI suggestion when the project asked for SEO. */
  caption: z.string(),
  hashtags: z.array(z.string()),
  /**
   * pending: the AI text is still being generated ("Generando texto…"). none: the project has no SEO.
   * failed: the generation failed. Proposed rule: a `DRAFT` stays `DRAFT` with `seo_status: 'failed'`
   * and the user writes the text (today `_fail_seo_post` moves the post to `FAILED`).
   */
  seo_status: z.enum(['none', 'pending', 'ready', 'failed']),
  clip: z.object({
    id: Id,
    title: z.string(),
    project_id: Id,
    project_title: z.string(),
    duration_seconds: z.number(),
    thumbnail_url: z.string().url().nullable(),
  }),
  /** Link to the post once published. */
  post_url: z.string().url().nullable(),
  /** Readable failure, in Spanish. */
  error_message: z.string().nullable(),
  error_code: z.string().nullable(),
  tiktok: TikTokOptions.nullable(),
});
export type ScheduledPost = z.infer<typeof ScheduledPost>;

export const CreatePostInput = z.object({
  video_clip: Id,
  platform: Platform,
  /** UTC. Null or absent: publish now. Scheduling needs `allow_scheduling` in the plan. */
  publish_at: IsoDateTime.nullable().optional(),
  caption: z.string().optional(),
  hashtags: z.array(z.string()).optional(),
  /** Required when `platform` is TIKTOK, unless it is a draft. */
  tiktok: TikTokOptions.optional(),
  /**
   * True: the post is created as `DRAFT` and nothing is published. It is how E1 gets the AI text
   * before publishing: creating the post fires the SEO generation, as the model does today.
   * A draft is published or scheduled with `PATCH` (`publish_at`).
   */
  draft: z.boolean().optional(),
});
export type CreatePostInput = z.infer<typeof CreatePostInput>;

export const PostsQuery = PageQuery.extend({
  status: PostStatus.optional(),
  platform: Platform.optional(),
  /** Range over `publish_at` (calendar week or month), ISO date-times. */
  from: IsoDateTime.optional(),
  to: IsoDateTime.optional(),
  project: Id.optional(),
  clip: Id.optional(),
});
export type PostsQuery = z.infer<typeof PostsQuery>;

export const UpdatePostRequest = z
  .object({
    /** UTC. A date schedules (or reschedules); null publishes now. Leaving it out keeps a draft as a draft. */
    publish_at: IsoDateTime.nullable(),
    caption: z.string(),
    hashtags: z.array(z.string()),
    tiktok: TikTokOptions,
  })
  .partial();
export type UpdatePostRequest = z.infer<typeof UpdatePostRequest>;

const planErrors = [
  { status: 400, when: 'Texto por encima del límite de la red, clip sin renderizar, o falta `tiktok.privacy_level`.' },
  { status: 403, when: 'El plan no permite programar (`code: "scheduling"`) o publicar en varias redes (`code: "crossposting"`), o el rol no publica.' },
  { status: 409, when: 'La cuenta de la red está desconectada o vencida (`code: "account_not_connected"`): la UI pide reconectar.' },
];

export const listSocialAccounts = defineEndpoint({
  id: 'social.accounts',
  method: 'GET',
  path: '/api/v1/social-accounts/',
  summary: 'Una entrada por red (TikTok, Instagram Reels, YouTube Shorts) con su estado y sus límites.',
  response: z.array(SocialAccount),
  errors: [],
  backend: { status: 'proposed', gap: 10, note: 'Hoy solo existe Workspace.ayrshare_profile_key, sin flujo para conectar.' },
  screens: ['H3', 'E1', 'B2'],
});

export const connectSocialAccount = defineEndpoint({
  id: 'social.connect',
  method: 'POST',
  path: '/api/v1/social-accounts/connect/',
  summary: 'Devuelve la URL de la red (vía Ayrshare) para autorizar la cuenta en una ventana aparte.',
  request: z.object({ platform: Platform, return_url: z.string().url().optional() }),
  response: z.object({ connect_url: z.string().url() }),
  errors: [{ status: 403, when: 'El rol no conecta redes (solo Dueño y Admin).' }],
  backend: { status: 'proposed', gap: 10 },
  screens: ['D6', 'H3', 'E1'],
});

export const disconnectSocialAccount = defineEndpoint({
  id: 'social.disconnect',
  method: 'DELETE',
  path: '/api/v1/social-accounts/:platform/',
  summary: 'Desconecta la cuenta de una red. Las publicaciones programadas en esa red quedan en error.',
  response: NoContent,
  successStatus: 204,
  errors: [{ status: 403, when: 'El rol no desconecta redes.' }],
  backend: { status: 'proposed', gap: 10 },
  screens: ['H3'],
});

/** What the read endpoints still miss (DIST-03, PR 1: only GET). */
const PARTIAL_NOTE =
  'Lectura lista: paginada (25, máximo 100), orden por `publish_at`, filtros `status`, `platform`, `from`, `to`, `project` y `clip`, y `status_counts` (cuenta todos los estados con los demás filtros). Solo trae los posts del workspace activo (`X-Workspace-Id`; 404 si el post es de otro). `caption`, `hashtags`, `seo_status`, `error_code` y `error_message` se derivan de lo que ya guarda el modelo. `clip.thumbnail_url` es la miniatura del clip (URL temporal de 1 h; `null` si no tiene). Falta: `post_url` queda `null` hasta que el publicador lo guarde, y crear, editar y cancelar siguen propuestos.';

export const listPosts = defineEndpoint({
  id: 'social.posts',
  method: 'GET',
  path: '/api/v1/scheduled-posts/',
  summary: 'Publicaciones del workspace, ordenadas por `publish_at`, con filtros por estado, red, rango y clip.',
  query: PostsQuery,
  response: paginated(ScheduledPost).extend({
    /** How many per status, for the filter chips. */
    status_counts: z.record(PostStatus, z.number().int().nonnegative()).optional(),
  }),
  errors: [],
  backend: {
    status: 'partial',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.list + ScheduledPostSerializer',
    gap: 8,
    note: PARTIAL_NOTE,
  },
  screens: ['E2', 'D3', 'D4 (diálogo)'],
});

export const createPost = defineEndpoint({
  id: 'social.createPost',
  method: 'POST',
  path: '/api/v1/scheduled-posts/',
  summary: 'Publica ahora o programa un clip en una red. Con `draft: true` crea un borrador, que dispara la generación del texto con IA.',
  request: CreatePostInput,
  response: ScheduledPost,
  successStatus: 201,
  idempotencyKey: true,
  errors: planErrors,
  backend: {
    status: 'proposed',
    gap: [8, 9],
    note:
      'Crear solo encola: la respuesta sale apenas el post queda guardado (`QUEUED` o `SCHEDULED`) y la publicación en la red la hace el worker después, así que el pedido entra en el tiempo de espera normal. Acepta `Idempotency-Key`: el cliente manda una clave por envío y la repite si reintenta (por ejemplo, después de un pedido que venció); el backend guarda la respuesta exitosa (2xx) por clave y workspace al menos 24 h y la devuelve tal cual, sin crear ni publicar de nuevo; una respuesta de error no se guarda, así que reintentar con la misma clave después de corregir el problema (por ejemplo, conectar la cuenta) vuelve a intentarlo. El backend tiene que permitir el header en CORS (`CORS_ALLOW_HEADERS`). Al crear el post, la señal existente genera el SEO y la publicación usa Ayrshare. Reglas propuestas: (1) una publicación en cola sin texto propio espera a que termine la generación con IA antes de salir (hoy saldría con el caption de reserva); (2) si la generación falla en un borrador, sigue en `DRAFT` con `seo_status: "failed"` (hoy `_fail_seo_post` lo pasa a `FAILED`); (3) un borrador creado con `draft: true` que nadie programa ni publica en N horas (sugerido: 24) se borra solo: el panel E1 los descarta al cerrarse, pero una pestaña que se cierra de golpe puede dejar alguno.',
  },
  screens: ['E1'],
});

export const createPostsBulk = defineEndpoint({
  id: 'social.createPostsBulk',
  method: 'POST',
  path: '/api/v1/scheduled-posts/bulk/',
  summary: 'Crea varias publicaciones juntas (varios clips, varias redes). Todo o nada.',
  request: z.object({ posts: z.array(CreatePostInput).min(1).max(60) }),
  response: z.object({ posts: z.array(ScheduledPost) }),
  successStatus: 201,
  errors: planErrors,
  idempotencyKey: true,
  backend: { status: 'proposed', gap: 8, note: 'Hasta 60 publicaciones. Igual que `createPost`: solo encola (ninguna red se llama dentro del pedido) y acepta `Idempotency-Key`, con la misma regla: una clave repetida devuelve las publicaciones que creó el primer pedido.' },
  screens: ['E1c'],
});

export const getPost = defineEndpoint({
  id: 'social.post',
  method: 'GET',
  path: '/api/v1/scheduled-posts/:id/',
  summary: 'Detalle de una publicación. Se consulta cada 5 s mientras está en cola o publicando.',
  response: ScheduledPost,
  errors: [{ status: 404, when: 'No existe o es de otro workspace.' }],
  backend: {
    status: 'partial',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.retrieve + ScheduledPostSerializer',
    gap: 8,
    note: PARTIAL_NOTE,
  },
  screens: ['E2', 'E1'],
});

export const updatePost = defineEndpoint({
  id: 'social.updatePost',
  method: 'PATCH',
  path: '/api/v1/scheduled-posts/:id/',
  summary: 'Edita el texto, reprograma, o publica un borrador (`publish_at`: fecha para programar, `null` para publicar ahora). Solo antes de `QUEUED`.',
  request: UpdatePostRequest,
  response: ScheduledPost,
  errors: [
    { status: 400, when: 'Texto por encima del límite, fecha en el pasado, o falta `tiktok.privacy_level` al publicar un borrador de TikTok.' },
    { status: 403, when: 'El plan no permite programar (`code: "scheduling"`), o el rol no publica.' },
    { status: 409, when: 'La publicación ya está en cola, publicando o publicada: no se puede editar.' },
  ],
  backend: { status: 'proposed', gap: 8 },
  screens: ['E2'],
});

export const cancelPost = defineEndpoint({
  id: 'social.cancelPost',
  method: 'POST',
  path: '/api/v1/scheduled-posts/:id/cancel/',
  summary: 'Cancela una publicación que todavía no salió. La borra.',
  response: NoContent,
  successStatus: 204,
  errors: [{ status: 409, when: 'Ya se está publicando o ya se publicó.' }],
  backend: { status: 'proposed', gap: 8 },
  screens: ['E2'],
});

export const retryPost = defineEndpoint({
  id: 'social.retryPost',
  method: 'POST',
  path: '/api/v1/scheduled-posts/:id/retry/',
  summary: 'Reintenta una publicación que falló (también una trabada en `PROCESSING`).',
  response: ScheduledPost,
  errors: [{ status: 409, when: 'La publicación no está en error.' }],
  backend: { status: 'proposed', gap: 8 },
  screens: ['E2'],
});

export const regeneratePostSeo = defineEndpoint({
  id: 'social.regeneratePostSeo',
  method: 'POST',
  path: '/api/v1/scheduled-posts/:id/regenerate-seo/',
  summary: 'Vuelve a generar el caption y los hashtags con IA. Responde con `seo_status: "pending"`; el texto anterior se conserva hasta que llega el nuevo.',
  response: ScheduledPost,
  successStatus: 202,
  errors: [
    { status: 403, when: 'El proyecto no pidió SEO o el plan no lo incluye (`code: "seo"`), o el rol no publica.' },
    { status: 409, when: 'La publicación ya se está publicando o ya se publicó: su texto no cambia.' },
  ],
  backend: {
    status: 'proposed',
    gap: 8,
    note: 'Hoy solo existe como acción del admin: ScheduledPostAdmin.regenerate_ai_metadata → queue_post_seo(post_id, regenerate=True).',
  },
  screens: ['E1'],
});

export const socialEndpoints = [
  listSocialAccounts,
  connectSocialAccount,
  disconnectSocialAccount,
  listPosts,
  createPost,
  createPostsBulk,
  getPost,
  updatePost,
  cancelPost,
  retryPost,
  regeneratePostSeo,
] as const;
