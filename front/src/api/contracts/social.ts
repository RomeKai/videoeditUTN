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
  /* Derived by ScheduledPostSerializer (gap 8) */
  /** What will be published. Starts as the AI suggestion when the project asked for SEO. */
  caption: z.string(),
  hashtags: z.array(z.string()),
  /**
   * pending: the AI text is still being generated ("Generando texto…"), also after "Regenerar", over the
   * previous text. none: the project has no SEO. failed: the generation failed. A `DRAFT` stays `DRAFT`
   * with `seo_status: 'failed'` and the user writes the text; a post that was going out turns `FAILED`.
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
  /**
   * UTC. Null, absent or within a minute of now: publish now, which leaves the post `SCHEDULED` with
   * `publish_at` = now for the beat (it queues it within 5 minutes). Later: scheduling, which needs
   * `allow_scheduling` in the plan.
   */
  publish_at: IsoDateTime.nullable().optional(),
  caption: z.string().optional(),
  /** Without "#"; the backend takes them off and drops repeats. Letters, digits and "_" only. */
  hashtags: z.array(z.string()).optional(),
  /** Required when `platform` is TIKTOK, unless it is a draft. The backend takes the flags left out as false. */
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
  {
    status: 400,
    when: 'Por campo (`{campo: [mensaje]}`): texto por encima del límite de la red (`caption`), demasiados hashtags o uno inválido (`hashtags`), clip de otro espacio o sin renderizar (`video_clip`), fecha pasada (`publish_at`) o falta `tiktok.privacy_level` (`tiktok`). En /bulk/ va además `index`, la posición de la publicación con el error, y `posts` si el lote está vacío, pasa de 60 o repite un clip en la misma red.',
  },
  { status: 403, when: 'El plan no permite programar (`code: "scheduling"`) o publicar el clip en varias redes (`code: "crossposting"`): `{error, code}`. O el rol no publica: `{detail}`.' },
  {
    status: 409,
    when: 'El espacio no tiene la cuenta de Ayrshare (`code: "account_not_connected"`): la UI pide conectar. O la `Idempotency-Key` ya se usó con otro cuerpo, o sus publicaciones ya no existen (`code: "idempotency_key_reused"`).',
  },
];

/** What every write of a post does not do: call the network. */
const QUEUE_ONLY =
  'Ningún endpoint de publicaciones llama a la red: lo que tiene que salir queda `SCHEDULED` y lo encola `dispatch_scheduled_posts_batch` (Celery Beat, cada 5 min). Cada escritura toma el lock de la fila del post y decide con el estado que lee bajo ese lock, igual que el despachador y el worker, así que nunca pisa una publicación que el beat ya tomó.';

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

/** What the read endpoints still miss (DIST-03). */
const PARTIAL_NOTE =
  'Lectura lista: paginada (25, máximo 100), orden por `publish_at`, filtros `status`, `platform`, `from`, `to`, `project` y `clip`, y `status_counts` (cuenta todos los estados con los demás filtros). Solo trae los posts del workspace activo (`X-Workspace-Id`; 404 si el post es de otro). `caption`, `hashtags`, `seo_status`, `error_code` y `error_message` se derivan de lo que ya guarda el modelo. `clip.thumbnail_url` es la miniatura del clip (URL temporal de 1 h; `null` si no tiene). Falta: `post_url` queda `null` hasta que el publicador lo guarde. Una publicación «ahora» queda `SCHEDULED` con `publish_at` en el pasado hasta que el beat la toma (hasta 5 min): la UI la consulta como si estuviera en cola.';

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
    status: 'exists',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.create + services/scheduled_posts.py · create_posts',
    note:
      'Crear solo guarda: un borrador queda `DRAFT` y lo demás `SCHEDULED` (ahora, o la fecha pedida), y la publicación en la red la hace el worker después de que el beat la encola. ' +
      QUEUE_ONLY +
      ' El clip tiene que ser del espacio activo y estar renderizado. Los límites del plan se miran en el servidor: programar pide `allow_scheduling`; sin `allow_crossposting` un clip sale en una sola red, contando sus publicaciones programadas, en cola, publicando o publicadas (no los borradores ni las fallidas). Sin plan no hay límites. La cuenta: sin `Workspace.ayrshare_profile_key` es 409 `account_not_connected` (no se sabe red por red hasta el hueco 10; un borrador no la pide). Acepta `Idempotency-Key` (hasta 255 caracteres): el backend guarda por clave y workspace, durante 24 h, qué publicaciones creó; la misma clave devuelve esas publicaciones (tal como están ahora) sin crear nada, con otro cuerpo es 409, y una respuesta de error no se guarda. Al crear el post, la señal existente genera el SEO (`seo_status: "pending"`). Si la generación falla en un borrador, sigue `DRAFT` con `seo_status: "failed"`. Pendiente: (1) una publicación en cola sin texto propio todavía puede salir antes de que llegue el de la IA, con el texto de reserva, y el publicador todavía no usa `caption`, `hashtags` ni `tiktok` (es del publicador); (2) los borradores que nadie manda no se borran solos.',
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
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.bulk + services/scheduled_posts.py · create_posts',
    note: 'Hasta 60 publicaciones, en una sola transacción: un error en cualquiera (validación, plan, cuenta o de la base) no crea ninguna, y el 400 dice cuál con `index`. Un clip repetido en la misma red es 400. Las mismas reglas que `createPost`, y la misma `Idempotency-Key`: una clave repetida devuelve las publicaciones que creó el primer pedido.',
  },
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
    { status: 400, when: 'Texto por encima del límite, fecha en el pasado, clip que se está renderizando de nuevo, o falta `tiktok.privacy_level` al publicar un borrador de TikTok.' },
    { status: 403, when: 'El plan no permite programar (`code: "scheduling"`) o publicar el clip en otra red (`code: "crossposting"`), o el rol no publica.' },
    { status: 404, when: 'No existe o es de otro workspace.' },
    {
      status: 409,
      when: 'Ya está en cola o publicando (`code: "post_in_flight"`), ya salió (`post_published`) o falló (`post_failed`: se reintenta o se cancela). Al mandarla, sin cuenta: `account_not_connected`.',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.partial_update + services/scheduled_posts.py · update_post',
    note: 'Solo `DRAFT` y `SCHEDULED`: un post `QUEUED` ya tiene su tarea en el broker con la hora vieja. `publish_at` con fecha programa o reprograma; `null` (o dentro de un minuto) lo deja `SCHEDULED` para ahora; sin el campo, un borrador sigue borrador. Al mandarlo se miran el clip, el plan, la cuenta y TikTok como al crear, y se borra la falla del texto del borrador. `caption` y `hashtags` vacíos vuelven a mostrar el texto de la IA. ' + QUEUE_ONLY,
  },
  screens: ['E2'],
});

export const cancelPost = defineEndpoint({
  id: 'social.cancelPost',
  method: 'POST',
  path: '/api/v1/scheduled-posts/:id/cancel/',
  summary: 'Cancela una publicación que todavía no salió. La borra.',
  response: NoContent,
  successStatus: 204,
  errors: [
    { status: 403, when: 'El rol no publica.' },
    { status: 404, when: 'No existe o es de otro workspace.' },
    { status: 409, when: 'Ya se está publicando (`code: "post_in_flight"`) o ya se publicó (`post_published`).' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.cancel + services/scheduled_posts.py · cancel_post',
    note: 'Borra `DRAFT`, `SCHEDULED`, `QUEUED` y `FAILED`. La tarea de un post `QUEUED` después no encuentra nada y termina sin publicar. Si el worker lo toma en el mismo momento, el pedido espera su lock y responde 409. ' + QUEUE_ONLY,
  },
  screens: ['E2'],
});

export const retryPost = defineEndpoint({
  id: 'social.retryPost',
  method: 'POST',
  path: '/api/v1/scheduled-posts/:id/retry/',
  summary: 'Reintenta una publicación que falló (también una trabada en `PROCESSING`).',
  response: ScheduledPost,
  errors: [
    { status: 400, when: 'El clip se está renderizando de nuevo, o es TikTok y le falta `tiktok.privacy_level`.' },
    { status: 403, when: 'El plan no permite el clip en otra red (`code: "crossposting"`), o el rol no publica.' },
    { status: 404, when: 'No existe o es de otro workspace.' },
    {
      status: 409,
      when: 'No está en error (`code: "post_not_failed"`: también el segundo reintento), se está publicando (`post_in_flight`), ya salió (`post_published`), no se pudo leer el lock del worker (`post_lock_unavailable`) o no hay cuenta (`account_not_connected`).',
    },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.retry + services/scheduled_posts.py · retry_post',
    note: 'Un `FAILED`, o un `PROCESSING` trabado, vuelve a `SCHEDULED` para ahora (sin `error_log`) y lo encola el beat. Trabado: su último intento empezó (`updated_at`) hace más de 15 min y el worker no tiene el lock del post; antes es 409, para no publicarlo dos veces mientras sale. Un post trabado o que falló sin saber si salió puede estar publicado: la UI lo avisa antes de reintentar. Si lo que falló fue el texto con IA, vuelve a `DRAFT` con `seo_status: "failed"`: nunca salió y no se programa solo. ' + QUEUE_ONLY,
  },
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
    { status: 404, when: 'No existe o es de otro workspace.' },
    { status: 409, when: 'Ya está en cola o publicando (`code: "post_in_flight"`) o ya se publicó (`post_published`): su texto no cambia.' },
    { status: 429, when: 'Demasiados pedidos del usuario (30 por hora): `detail` dice cuánto esperar.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_social.py · ScheduledPostViewSet.regenerate_seo + services/scheduled_posts.py · regenerate_seo',
    note: 'Encola `process_video_seo` con `regenerate`. Mientras tanto el post responde `seo_status: "pending"` con el texto anterior; cuando llega el nuevo reemplaza también el que escribió el usuario. Si la generación falla, el texto anterior queda y `seo_status` vuelve a `ready`. Un pedido mientras otro está en camino no encola nada (el doble clic no paga dos veces). No gasta tokens de la billetera: el SEO se cobra una vez, al crear el proyecto; el límite por usuario acota lo que se gasta en el proveedor.',
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
