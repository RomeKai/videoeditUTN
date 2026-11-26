import { z } from 'zod';
import { SLOW_REQUEST_TIMEOUT_MS } from '../config';
import { defineEndpoint } from '../endpoint';
import { Decimal, Id, IsoDateTime, PageQuery, RenderResolution, UserRef, paginated } from './common';

/** SubscriptionPlan (apps/payments/models.py) plus what the pricing page needs. */
export const Plan = z.object({
  id: Id,
  name: z.string(),
  max_video_duration_seconds: z.number().int().positive(),
  max_resolution: RenderResolution,
  has_watermark: z.boolean(),
  allow_scheduling: z.boolean(),
  allow_crossposting: z.boolean(),
  has_seo_optimization: z.boolean(),
  has_thumbnail_engine: z.boolean(),
  /** 0.10 = 10 % off tokens. */
  base_discount_rate: Decimal,
  max_members: z.number().int().positive(),
  is_active: z.boolean(),
  /** Monthly price, tagline, order and the recommended mark of A6/G3 (PAY-03; at most one plan is recommended). */
  price_usd_monthly: Decimal,
  description: z.string(),
  sort_order: z.number().int(),
  is_recommended: z.boolean(),
});
export type Plan = z.infer<typeof Plan>;

/** The numbers of PricingEngine, so the browser estimates with the same formula (map 1.5). The backend sends every Decimal as text. */
export const Pricing = z.object({
  /** PricingEngine.BASE_COST_PER_SECOND_720P */
  base_cost_per_second_720p: Decimal,
  /** PricingEngine.RESOLUTION_MULTIPLIERS */
  resolution_multipliers: z.object({ '720p': Decimal, '1080p': Decimal, '4K': Decimal }),
  /** PricingEngine.FEATURE_COSTS */
  feature_costs: z.object({ seo_optimization: Decimal, ai_thumbnail: Decimal }),
  /** Flat price of a project created from a link (PricingEngine.LINK_FLAT_COST). Links are always 720p. */
  link_flat_cost: Decimal,
  /** Floor of any charge (PricingEngine.MIN_COST, 0.01). */
  min_cost: Decimal,
  /** Reference price of one token, for "≈ USD 9" (setting TOKEN_PRICE_USD). */
  token_price_usd: Decimal,
  /** AI_SINGLE_EDIT_MAX_DURATION_SECONDS (600). */
  single_edit_max_duration_seconds: z.number().int().positive(),
  /** AI_UPLOAD_MAX_DURATION_SECONDS (3 h): the system ceiling, above any plan. */
  upload_max_duration_seconds: z.number().int().positive(),
  /** Upload size ceiling in bytes (UPLOAD_MAX_SIZE_BYTES in apps/videos/serializers.py: 500 MB). */
  upload_max_size_bytes: z.number().int().positive(),
  /** Accepted extensions (UPLOAD_EXTENSIONS: .mp4, .mov, .avi). */
  upload_extensions: z.array(z.string()),
  /** MAX_SELECTION_CRITERIA_CHARS */
  selection_criteria_max_chars: z.number().int().positive(),
  /** prompt_max_chars() */
  prompt_max_chars: z.number().int().positive(),
});
export type Pricing = z.infer<typeof Pricing>;

export const EstimateRequest = z.object({
  /** 'file': priced by duration. 'link': flat price, duration unknown. */
  source: z.enum(['file', 'link']),
  /** Needed with 'file' (400 without it); priced by its whole seconds, like the creation. At most 86400. */
  duration_seconds: z.number().nonnegative().optional(),
  /** Default 720p. A 'link' takes only 720p (400 otherwise). */
  render_resolution: RenderResolution.optional(),
  use_seo: z.boolean().optional(),
  /** For the ETA: how many clips at most. Accepted; the time to the review does not depend on it today. */
  max_clips: z.number().int().positive().optional(),
});
export type EstimateRequest = z.infer<typeof EstimateRequest>;

export const Estimate = z.object({
  base_cost: Decimal,
  add_ons_cost: Decimal,
  discount_rate: Decimal,
  discount_amount: Decimal,
  total: Decimal,
  available_balance: Decimal,
  balance_after: Decimal,
  sufficient: z.boolean(),
  has_watermark: z.boolean(),
  /**
   * "Listo para revisar en ≈ X min" (gap 18): the median time the latest projects took per second of video
   * up to the review, times this duration (only the stages, not the queue). Null for a link, for 0 s and
   * while no project has reached the review.
   */
  eta_seconds: z.number().int().nonnegative().nullable(),
});
export type Estimate = z.infer<typeof Estimate>;

export const Subscription = z.object({
  plan: Plan,
  status: z.enum(['active', 'past_due', 'canceled']),
  /** Null on the free plan. */
  renews_at: IsoDateTime.nullable(),
  /** Downgrade waiting for the end of the period. */
  pending_plan: z.object({ id: Id, name: z.string(), effective_at: IsoDateTime }).nullable(),
});
export type Subscription = z.infer<typeof Subscription>;

export const Wallet = z.object({
  id: Id,
  workspace: Id,
  available_balance: Decimal,
  reserved_balance: Decimal,
  updated_at: IsoDateTime,
  /** SPEND + COMPLETED charged this month (by when it was charged), in the workspace's time zone. */
  spent_this_month: Decimal,
  /** Below this the UI warns "saldo bajo": an average video, 20 min at 720p with the plan's discount (map 1.11). */
  low_balance_threshold: Decimal,
  /** Gap 19: each open reservation (SPEND + RESERVED) with its project, newest first. */
  reservations: z.array(
    z.object({
      transaction_id: Id,
      project_id: Id.nullable(),
      project_title: z.string().nullable(),
      amount: Decimal,
      created_at: IsoDateTime,
    }),
  ),
});
export type Wallet = z.infer<typeof Wallet>;

/** Transaction.Type */
export const TransactionType = z.enum(['DEPOSIT', 'SPEND', 'REFUND']);
/** Transaction.Status */
export const TransactionStatus = z.enum(['PENDING', 'RESERVED', 'COMPLETED', 'FAILED']);

/**
 * What the movements table shows (map G2). The backend derives it from type + status
 * (apps/payments/services/movements.py · KINDS), so "devuelto" does not depend on reading FAILED:
 * DEPOSIT+COMPLETED purchase, SPEND+RESERVED reserved, SPEND+COMPLETED charged, REFUND+COMPLETED refund,
 * SPEND+FAILED released, DEPOSIT or REFUND + PENDING pending.
 */
export const TransactionKind = z.enum(['purchase', 'reserved', 'charged', 'refund', 'released', 'pending']);
export type TransactionKind = z.infer<typeof TransactionKind>;

export const Transaction = z.object({
  id: Id,
  wallet: Id,
  created_by: Id.nullable(),
  amount: Decimal,
  transaction_type: TransactionType,
  status: TransactionStatus,
  description: z.string(),
  created_at: IsoDateTime,
  /** Gap 4. */
  kind: TransactionKind,
  /** The name, never the email. */
  created_by_detail: UserRef.nullable(),
  /** Gap 4: direct link to the project. Null on a purchase and once the project is deleted. */
  project: z.object({ id: Id, title: z.string() }).nullable(),
  /** What the charge was for, for the usage chart. The creation is `project` (SEO included); any later render, `render`. */
  category: z.enum(['project', 'render', 'seo', 'purchase', 'other']),
});
export type Transaction = z.infer<typeof Transaction>;

export const TransactionsQuery = PageQuery.extend({
  kind: TransactionKind.optional(),
  /** YYYY-MM */
  month: z.string().regex(/^\d{4}-\d{2}$/).optional(),
  project: Id.optional(),
});
export type TransactionsQuery = z.infer<typeof TransactionsQuery>;

/** Count and sum of the movements of one kind, for the filter chips and the totals of G2 and G1. */
export const TransactionKindTotal = z.object({ count: z.number().int().nonnegative(), amount: Decimal });
export type TransactionKindTotal = z.infer<typeof TransactionKindTotal>;

export const UsageDay = z.object({
  /** YYYY-MM-DD */
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  projects: Decimal,
  renders: Decimal,
  seo: Decimal,
});
export type UsageDay = z.infer<typeof UsageDay>;

export const TokenPackage = z.object({
  id: z.string().min(1),
  tokens: z.number().int().positive(),
  price_usd: Decimal,
  /** 0.10 = "10 % menos". */
  discount_rate: Decimal,
});
export type TokenPackage = z.infer<typeof TokenPackage>;

export const TokenPackages = z.object({
  packages: z.array(TokenPackage),
  /** Price per token of a free amount (no volume discount). */
  custom_price_per_token_usd: Decimal,
  custom_min_tokens: z.number().int().positive(),
  /** Reference conversion to the local currency: "≈ ARS 14.500". */
  local_currency: z.string().length(3),
  usd_to_local_rate: Decimal,
});
export type TokenPackages = z.infer<typeof TokenPackages>;

export const PurchaseStatus = z.enum(['pending', 'approved', 'rejected']);

export const TokenPurchase = z.object({
  id: Id,
  status: PurchaseStatus,
  /** 0 when the payment is for a plan upgrade (`plan_id` set). */
  tokens: z.number().int().nonnegative(),
  amount_usd: Decimal,
  /** Proposed: set when this payment is the checkout of a plan upgrade (`billing.changePlan`). */
  plan_id: Id.nullable().optional(),
  /** Where the browser goes to pay. Null once it is settled. */
  checkout_url: z.string().url().nullable(),
  /** Why the provider rejected it, as a readable sentence. */
  failure_reason: z.string().nullable(),
  created_at: IsoDateTime,
  /** Balance after the purchase was credited. */
  balance_after: Decimal.nullable(),
});
export type TokenPurchase = z.infer<typeof TokenPurchase>;

/** What `billing.purchase` answers: a token purchase always has tokens; the payment of a plan upgrade has `plan_id` and none. */
export const Payment = TokenPurchase.superRefine((value, context) => {
  if (value.plan_id) {
    if (value.tokens !== 0) context.addIssue({ code: z.ZodIssueCode.custom, path: ['tokens'], message: 'El pago de un plan no acredita tokens.' });
  } else if (value.tokens <= 0) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ['tokens'], message: 'Una compra de tokens tiene al menos uno.' });
  }
});

export const listPlans = defineEndpoint({
  id: 'billing.plans',
  method: 'GET',
  path: '/api/v1/plans/',
  summary: 'Planes activos y públicos, en orden, para Precios y Uso › Planes.',
  response: z.array(Plan),
  auth: false,
  workspaceScoped: false,
  errors: [],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views.py · PublicPlanListView + PublicPlanSerializer (apps/payments/serializers.py)',
    note: 'Público, sin token. Lista los planes con `is_active` e `is_public` (los cuatro sembrados por las migraciones `payments` 0005, 0007 y 0009), ordenados por `sort_order`; un plan creado en el admin no sale hasta marcarlo público. `is_public` no viaja en la respuesta. Cache de 5 min que se borra cuando un cambio de plan hace commit. Un workspace nuevo empieza en Gratis (`DEFAULT_PLAN_NAME`).',
  },
  screens: ['A6', 'G3', 'D3 (diálogo)', 'C1'],
});

export const getPricing = defineEndpoint({
  id: 'billing.pricing',
  method: 'GET',
  path: '/api/v1/pricing/',
  summary: 'Valores de la fórmula de precios y los topes de subida, para estimar en el navegador.',
  response: Pricing,
  auth: false,
  workspaceScoped: false,
  errors: [],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views.py · PricingView + services/estimate.py · pricing_values',
    note: 'Público, sin token. Los valores de PricingEngine (apps/payments/services/pricing_engine.py), los topes de subida de apps/videos/serializers.py y los settings `AI_SINGLE_EDIT_MAX_DURATION_SECONDS`, `AI_UPLOAD_MAX_DURATION_SECONDS`, `AI_PROMPT_MAX_CHARS` y `TOKEN_PRICE_USD`. Los decimales llegan como texto (`"0.5"`).',
  },
  screens: ['C1', 'A6', 'G3', 'G4', 'D2', 'D1 (diálogo)'],
});

export const estimateCost = defineEndpoint({
  id: 'billing.estimate',
  method: 'POST',
  path: '/api/v1/pricing/estimate/',
  summary: 'Estimado del backend para una configuración: costo, descuento, saldo después y tiempo estimado.',
  request: EstimateRequest,
  response: Estimate,
  errors: [
    { status: 400, when: 'Falta `duration_seconds` con `source: "file"`, o un link pide otra resolución que 720p (`render_resolution`). Errores por campo.' },
    { status: 403, when: 'El plan no permite la duración, la resolución o el SEO (`code`: `duration` | `resolution` | `seo`, con `limit` en los dos primeros). La UI abre D3.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views.py · EstimateView + services/estimate.py · estimate',
    note: 'Las mismas reglas que la creación: un archivo se cobra por sus segundos enteros con PricingEngine.calculate_render_cost y el plan del workspace; un link, precio fijo más el SEO con descuento. `discount_amount` es base + adicionales − total (incluye el redondeo). No escribe nada: lo pide cualquier rol (`wallet.view`). `eta_seconds` sale de los tiempos por etapa de los últimos proyectos que llegaron a la revisión (`metadata.stage_durations`): null para un link o mientras no hay ninguno. 404 si el usuario no tiene workspace.',
  },
  screens: ['C1', 'D2', 'D4', 'D5'],
});

export const getSubscription = defineEndpoint({
  id: 'billing.subscription',
  method: 'GET',
  path: '/api/v1/subscription/',
  summary: 'Plan actual del workspace, su renovación y el cambio pendiente si lo hay.',
  response: Subscription,
  errors: [{ status: 404, when: 'El workspace no tiene plan y tampoco existe el plan por defecto (`DEFAULT_PLAN_NAME`).' }],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views_subscription.py · SubscriptionView + SubscriptionSerializer (apps/payments/serializers_billing.py)',
    note: 'Todos los roles. `status` es siempre `active`: todavía nada cobra la renovación (PAY-02), así que nada vence. `renews_at` es el fin del período pagado con el checkout (un mes desde que se confirmó el pago de la subida); es null en un plan gratis, en uno asignado en el admin, en uno al que se llegó bajando y cuando el período ya terminó. Un workspace sin plan muestra el plan por defecto.',
  },
  screens: ['Barra', 'G1', 'G3', 'C1', 'E1', 'H2'],
});

export const changePlan = defineEndpoint({
  id: 'billing.changePlan',
  method: 'POST',
  path: '/api/v1/subscription/change/',
  summary:
    'Cambia de plan. Subir se aplica al momento, o al volver del checkout si hay que pagar; bajar, al fin del período. Mandar el plan actual cancela un cambio programado.',
  request: z.object({
    plan_id: Id,
    /**
     * Where the provider sends the browser back when the upgrade needs a payment (`/uso/planes`).
     * The backend appends `compra=<id>`; the screen follows that payment with `billing.purchase`.
     */
    return_url: z.string().url().optional(),
  }),
  response: z.object({
    applied: z.enum(['now', 'period_end']),
    /** Present when the upgrade needs a payment. */
    checkout_url: z.string().url().nullable(),
    subscription: Subscription,
  }),
  errors: [
    { status: 400, when: 'Ya es el plan actual y no hay ningún cambio programado para cancelar (`{ plan_id: ["Ya es tu plan actual."] }`), o `return_url` no es una página de la app.' },
    { status: 403, when: 'El rol no puede cambiar el plan (solo Dueño y Admin).' },
    { status: 404, when: 'El plan no existe, está inactivo o no es público (`{ detail: "El plan no existe." }`).' },
    {
      status: 409,
      when: 'Cambiar a un plan con menos lugares que los que el workspace ocupa hoy, contando invitaciones pendientes (`code: "seats_exceeded"`, con `seats_used` y `max_members`). No se programa nada: primero hay que quitar miembros. Es un 409 porque lo impide el estado del workspace; el mismo código en `workspaces.invite` es un 403 de límite de plan, que se resuelve subiendo de plan.',
    },
    { status: 501, when: 'Subir a un plan con precio sin proveedor de pagos configurado (`code: "payments_unavailable"`). No cambia nada. No es 503: la app lee cualquier 503 como mantenimiento.' },
    { status: 502, when: 'El proveedor no abrió el checkout (`code: "checkout_failed"`). No se cobró nada.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views_subscription.py · ChangePlanView (apps/payments/services/subscriptions.py)',
    note: 'Subir es ir a un plan posterior en `sort_order`, como en Planes. Subir a un plan con precio siempre pide un pago: `checkout_url` viene con valor, el plan no cambia todavía y el proveedor vuelve a `return_url` (por defecto `FRONTEND_URL/uso/planes`) con `compra=<id>`; al confirmarse el pago cambia el plan una sola vez y empieza un período pagado de un mes. Pedir otra vez la misma subida devuelve el mismo checkout; elegir otro plan rechaza el pago que esperaba. Subir a un plan sin precio se aplica al momento. Bajar se programa para el fin del período pagado (`applied: "period_end"`); sin período pagado (plan asignado en el admin, o el período ya terminó) se aplica al momento (`applied: "now"`). La tarea `apply_due_plan_changes` (Celery Beat, cada 15 min) aplica las bajadas que vencieron. Con `plan_id` igual al plan actual y un cambio programado, lo cancela (`pending_plan` vuelve a null, `applied: "now"`). El mock aplica la subida al momento salvo en el escenario «Plan con checkout».',
  },
  screens: ['G3'],
});

export const getWallet = defineEndpoint({
  id: 'billing.wallet',
  method: 'GET',
  path: '/api/v1/wallet/',
  summary: 'Saldo disponible y reservado del workspace, con el detalle de lo reservado por proyecto.',
  response: Wallet,
  errors: [],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views.py · WalletView',
    note: 'Los saldos con sus diez decimales, como texto. `reservations` tiene una fila por reserva abierta (no una suma por proyecto), con el proyecto que paga (`Transaction.project`, migración 0015 de payments; null en una reserva vieja que la 0016 no pudo vincular). `spent_this_month` y el mes van en `WORKSPACE_TIME_ZONE` (America/Argentina/Buenos_Aires) y cuentan por cuándo se cobró (`settled_at`). Lo ven todos los roles (`wallet.view`). 404 si el usuario no tiene workspace.',
  },
  screens: ['Barra', 'G1', 'C1', 'D2', 'G4', 'D1 (diálogo)', 'D2 (diálogo)'],
});

export const getUsage = defineEndpoint({
  id: 'billing.usage',
  method: 'GET',
  path: '/api/v1/wallet/usage/',
  summary: 'Consumo por día de un mes (solo lo cobrado), separado en proyectos, renders y SEO. El mes y los días son los de la zona horaria del workspace (America/Argentina/Buenos_Aires).',
  query: z.object({ month: z.string().regex(/^\d{4}-\d{2}$/).optional() }),
  response: z.array(UsageDay),
  errors: [
    { status: 400, when: '`month` no es `AAAA-MM` (`{ month: ["Mandá el mes como AAAA-MM."] }`).' },
    { status: 403, when: 'Solo Dueño y Admin.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views.py · WalletUsageView + services/movements.py · usage',
    note: 'Todos los días del mes (sin `month`, el actual), en orden. Suma los SPEND + COMPLETED por el día en que se cobraron (`settled_at`, o `created_at` en filas viejas): un cobro parcial cuenta lo cobrado. Columnas por `Transaction.category`: `render` en `renders`, `seo` en `seo` y el resto en `projects`. Hoy nada escribe `seo`: el SEO se cobra dentro de la creación, así que cae en `projects` y la columna llega en 0.',
  },
  screens: ['G1'],
});

export const listTransactions = defineEndpoint({
  id: 'billing.transactions',
  method: 'GET',
  path: '/api/v1/transactions/',
  summary: 'Movimientos de la billetera, paginados, con filtro por tipo, mes y proyecto. `month=YYYY-MM` es el mes en la zona horaria del workspace (America/Argentina/Buenos_Aires).',
  query: TransactionsQuery,
  response: paginated(Transaction).extend({
    /**
     * Gap 4, always present: how many movements and how many tokens per kind, for the
     * same month and project but ignoring `kind` and the page, so the chips can show every count
     * at once. Kinds without movements are absent.
     */
    totals: z.record(TransactionKind, TransactionKindTotal),
  }),
  errors: [
    { status: 400, when: '`kind`, `month` o `project` no son válidos (errores por campo).' },
    { status: 403, when: 'Solo Dueño y Admin, también con `project` (Editor y Lector ven únicamente el saldo).' },
    { status: 404, when: 'La página no existe (`detail: "Página inválida."`).' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views.py · TransactionListView + TransactionSerializer (apps/payments/serializers.py) + services/movements.py',
    note: 'De la más nueva a la más vieja; `page_size` hasta 100 (25 por defecto). `kind` sale de tipo + estado; una fila sin `kind` no movió el saldo y no sale (una compra que falló). Cada reserva queda vinculada a su proyecto con `category` desde que se reserva (`wallet_service.link_reservation`); las viejas, por la migración 0016 de payments, con lo que guardaba la metadata del proyecto. Un proyecto borrado deja `project` en null. Hoy el backend solo escribe SPEND: un cobro parcial (`commit_partial_reservation`) baja el monto del SPEND a lo cobrado y lo deja COMPLETED, y devuelve el resto al saldo sin escribir otra fila; una reserva que se cae queda SPEND + FAILED (`kind: "released"`, «Devuelto», con su proyecto). DEPOSIT llega con las compras (PAY-02) y REFUND no lo escribe nada todavía: `kind: "refund"` es para devoluciones de algo ya cobrado. Tres consultas por página, sea cual sea su tamaño.',
  },
  screens: ['G2', 'D1f'],
});

export const exportTransactions = defineEndpoint({
  id: 'billing.exportTransactions',
  method: 'GET',
  path: '/api/v1/transactions/export/',
  // The whole history is written out while the request waits.
  timeoutMs: SLOW_REQUEST_TIMEOUT_MS,
  summary: 'Los mismos movimientos en CSV. El monto lleva el signo de lo que le hizo al saldo: negativo lo reservado y lo cobrado, positivo lo comprado y lo devuelto.',
  query: TransactionsQuery.omit({ page: true, page_size: true }),
  response: z.string(),
  responseType: 'text',
  errors: [
    { status: 400, when: '`kind`, `month` o `project` no son válidos (errores por campo).' },
    { status: 403, when: 'Solo Dueño y Admin.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views.py · TransactionExportView + services/movements.py · csv_text',
    note: 'Columnas `fecha,concepto,proyecto,tipo,monto`: la fecha en ISO con la zona del workspace (`2026-11-04T12:00:00-03:00`), la descripción, el título del proyecto, el tipo como lo nombra la pantalla («Devuelto», «Cobrado»…) y el monto exacto (dos decimales si alcanzan, si no todos). Un texto que empieza con `=`, `+`, `-` o `@` lleva un apóstrofo adelante, para que una planilla no lo ejecute. Toda la historia, sin páginas.',
  },
  screens: ['G2'],
});

export const listTokenPackages = defineEndpoint({
  id: 'billing.tokenPackages',
  method: 'GET',
  path: '/api/v1/token-packages/',
  summary: 'Los 3 paquetes fijos, el precio de la cantidad libre y la conversión de referencia.',
  response: TokenPackages,
  errors: [],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views_purchases.py · TokenPackagesView (apps/payments/services/token_packages.py)',
    note: 'Todos los roles. El precio de un paquete sale de sus tokens y su descuento sobre 0,01 USD por token. La conversión es de referencia (`PAYMENTS_LOCAL_CURRENCY`, `PAYMENTS_USD_TO_LOCAL_RATE`): todo pago es en USD. La cantidad libre va de 100 a 1.000.000 tokens.',
  },
  screens: ['G4', 'D2 (diálogo)'],
});

export const createTokenPurchase = defineEndpoint({
  id: 'billing.createPurchase',
  method: 'POST',
  path: '/api/v1/token-purchases/',
  summary: 'Inicia una compra de tokens y devuelve la URL del checkout del proveedor de pagos.',
  request: z
    .object({
      package_id: z.string().min(1).optional(),
      custom_tokens: z.number().int().positive().optional(),
      /** Where the provider sends the browser back: `/uso/comprar/resultado`. The backend appends `compra=<id>` to it. */
      return_url: z.string().url(),
    })
    .refine((value) => Boolean(value.package_id) !== Boolean(value.custom_tokens), {
      message: 'Mandá `package_id` o `custom_tokens`, uno solo.',
    }),
  response: TokenPurchase,
  successStatus: 201,
  idempotencyKey: true,
  errors: [
    { status: 400, when: 'Cantidad por debajo del mínimo o por encima del máximo (1.000.000), paquete inexistente, o `return_url` que no es una página de la app.' },
    { status: 403, when: 'El rol no puede comprar (solo Dueño y Admin).' },
    { status: 409, when: 'La `Idempotency-Key` ya empezó otra compra (`code: "idempotency_key_reused"`).' },
    { status: 501, when: 'No hay proveedor de pagos configurado (`code: "payments_unavailable"`). No se crea nada. No es 503: la app lee cualquier 503 como mantenimiento.' },
    { status: 502, when: 'El proveedor no abrió el checkout (`code: "checkout_failed"`). No se cobró nada.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views_purchases.py · TokenPurchaseCreateView + PaymentSerializer (apps/payments/serializers_billing.py)',
    note: 'El proveedor es `PAYMENTS_PROVIDER`. Con `simulated` (desarrollo, y en una demo con `PAYMENTS_ALLOW_SIMULATED`) `checkout_url` es `/dev/checkout?compra=<id>&volver=<return_url>` en el origen de `return_url`, y esa página decide con `billing.simulatePurchase`. Sin proveedor, 501. Una `Idempotency-Key` repetida responde la compra que ya empezó, con el mismo 201.',
  },
  screens: ['G4', 'D2 (diálogo)'],
});

export const getTokenPurchase = defineEndpoint({
  id: 'billing.purchase',
  method: 'GET',
  path: '/api/v1/token-purchases/:id/',
  summary: 'Estado de un pago: pendiente, acreditado o rechazado. Es una compra de tokens (`tokens` > 0) o el pago de una subida de plan (`plan_id`, `tokens` 0).',
  response: Payment,
  errors: [
    { status: 403, when: 'El rol no puede ver pagos (solo Dueño y Admin).' },
    { status: 404, when: 'La compra no existe en este workspace.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views_purchases.py · TokenPurchaseDetailView + PaymentSerializer (apps/payments/serializers_billing.py)',
    note: 'La confirmación del proveedor (o `billing.simulatePurchase`) acredita una sola vez: un DEPOSIT COMPLETED de los tokens en la billetera, «Compra de 1.000 tokens». La pantalla consulta cada 5 s mientras está pendiente. `plan_id` viene siempre (null en una compra de tokens). `balance_after` llega con 10 decimales, como el saldo.',
  },
  screens: ['G5'],
});

export const simulatePurchase = defineEndpoint({
  id: 'billing.simulatePurchase',
  method: 'POST',
  path: '/api/v1/token-purchases/:id/simulate/',
  summary: 'Solo desarrollo y demo: el checkout de prueba aprueba o rechaza un pago del proveedor simulado.',
  request: z.object({ approved: z.boolean() }),
  response: Payment,
  errors: [
    { status: 403, when: 'El rol no puede pagar (solo Dueño y Admin).' },
    { status: 404, when: 'El pago no existe en este workspace, no es del proveedor simulado, o el proveedor simulado está apagado (sin `PAYMENTS_PROVIDER=simulated`, o sin `DEBUG` ni `PAYMENTS_ALLOW_SIMULATED`): fuera de desarrollo y demos el endpoint no existe.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/payments/views_purchases.py · SimulatePaymentView',
    note: 'Liquida el pago en el momento, como lo haría el webhook de un proveedor real, y responde el pago ya liquidado. Uno ya liquidado se responde como está: aprobar dos veces acredita una sola. Lo llama `/dev/checkout` cuando `billing` va al backend; en el modo mock la página decide en la base simulada y el pago se liquida unos segundos después de volver, como un webhook.',
  },
  screens: ['Checkout de prueba (/dev/checkout)'],
});

export const billingEndpoints = [
  listPlans,
  getPricing,
  estimateCost,
  getSubscription,
  changePlan,
  getWallet,
  getUsage,
  listTransactions,
  exportTransactions,
  listTokenPackages,
  createTokenPurchase,
  getTokenPurchase,
  simulatePurchase,
] as const;
