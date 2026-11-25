import { z } from 'zod';
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
  /** Proposed (PAY-01): monthly price, tagline, order and the recommended mark of A6/G3. */
  price_usd_monthly: Decimal,
  description: z.string(),
  sort_order: z.number().int(),
  is_recommended: z.boolean(),
});
export type Plan = z.infer<typeof Plan>;

/** The numbers of PricingEngine, so the browser estimates with the same formula (map 1.5). */
export const Pricing = z.object({
  /** PricingEngine.BASE_COST_PER_SECOND_720P */
  base_cost_per_second_720p: Decimal,
  /** PricingEngine.RESOLUTION_MULTIPLIERS */
  resolution_multipliers: z.object({ '720p': Decimal, '1080p': Decimal, '4K': Decimal }),
  /** PricingEngine.FEATURE_COSTS */
  feature_costs: z.object({ seo_optimization: Decimal, ai_thumbnail: Decimal }),
  /** Flat price of a project created from a link (views.py: Decimal('10.0')). Links are always 720p. */
  link_flat_cost: Decimal,
  /** Floor of any charge (0.01). */
  min_cost: Decimal,
  /** Reference price of one token, for "≈ USD 9". */
  token_price_usd: Decimal,
  /** AI_SINGLE_EDIT_MAX_DURATION_SECONDS (600). */
  single_edit_max_duration_seconds: z.number().int().positive(),
  /** AI_UPLOAD_MAX_DURATION_SECONDS (3 h): the system ceiling, above any plan. */
  upload_max_duration_seconds: z.number().int().positive(),
  /** Upload size ceiling in bytes (serializer: 500 MB). */
  upload_max_size_bytes: z.number().int().positive(),
  /** Accepted extensions (serializer: .mp4, .mov, .avi). */
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
  duration_seconds: z.number().nonnegative().optional(),
  render_resolution: RenderResolution.optional(),
  use_seo: z.boolean().optional(),
  /** For the ETA: how many clips at most. */
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
  /** "Listo para revisar en ≈ X min" (gap 18). Null when it cannot be estimated. */
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
  /** Proposed: SPEND + COMPLETED of the current month. */
  spent_this_month: Decimal,
  /** Proposed: below this the UI warns "saldo bajo" (cost of an average video, map 1.11). */
  low_balance_threshold: Decimal,
  /** Proposed (gap 19): what is reserved, per project. */
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
 * What the movements table shows (map G2). Derivable from type + status today;
 * proposed as an explicit field so "devuelto" does not depend on reading FAILED.
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
  /** Proposed (gap 4). */
  kind: TransactionKind,
  created_by_detail: UserRef.nullable(),
  /** Proposed (gap 4): direct link to the project. */
  project: z.object({ id: Id, title: z.string() }).nullable(),
  /** Proposed: what the charge was for, for the usage chart. */
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

export const listPlans = defineEndpoint({
  id: 'billing.plans',
  method: 'GET',
  path: '/api/v1/plans/',
  summary: 'Planes activos, en orden, para Precios y Uso › Planes.',
  response: z.array(Plan),
  auth: false,
  workspaceScoped: false,
  errors: [],
  backend: { status: 'proposed', gap: [6, 12], note: 'Modelo: SubscriptionPlan (apps/payments/models.py); faltan precio, descripción, orden y recomendado.' },
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
  backend: { status: 'proposed', gap: 6, note: 'Valores de PricingEngine (apps/payments/services/pricing_engine.py) y de settings.' },
  screens: ['C1', 'A6', 'G3', 'G4', 'D2', 'D1 (diálogo)'],
});

export const estimateCost = defineEndpoint({
  id: 'billing.estimate',
  method: 'POST',
  path: '/api/v1/pricing/estimate/',
  summary: 'Estimado del backend para una configuración: costo, descuento, saldo después y tiempo estimado.',
  request: EstimateRequest,
  response: Estimate,
  errors: [{ status: 403, when: 'El plan no permite la duración, la resolución o el SEO (`code`: `duration` | `resolution` | `seo`). La UI abre D3.' }],
  backend: { status: 'proposed', gap: [6, 18], note: 'Envuelve PricingEngine.calculate_render_cost con el plan del workspace.' },
  screens: ['C1', 'D2', 'D4', 'D5'],
});

export const getSubscription = defineEndpoint({
  id: 'billing.subscription',
  method: 'GET',
  path: '/api/v1/subscription/',
  summary: 'Plan actual del workspace, su renovación y el cambio pendiente si lo hay.',
  response: Subscription,
  errors: [],
  backend: { status: 'proposed', gap: 12, note: 'Hoy solo existe Workspace.subscription_plan.' },
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
    { status: 400, when: 'Ya es el plan actual y no hay ningún cambio programado para cancelar.' },
    { status: 403, when: 'El rol no puede cambiar el plan (solo Dueño y Admin).' },
    {
      status: 409,
      when: 'Bajar a un plan con menos lugares que los que el workspace ocupa hoy, contando invitaciones pendientes (`code: "seats_exceeded"`). No se programa nada: primero hay que quitar miembros.',
    },
  ],
  backend: {
    status: 'proposed',
    gap: 12,
    note: 'Con `plan_id` igual al plan actual y un cambio programado, lo cancela (`pending_plan` vuelve a null, `applied: "now"`). Si subir necesita un pago, `checkout_url` viene con valor, el plan no cambia todavía y el proveedor vuelve a `return_url` con `compra=<id>`.',
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
  backend: { status: 'proposed', gap: [4, 19], note: 'Modelo: Wallet (available_balance, reserved_balance).' },
  screens: ['Barra', 'G1', 'C1', 'D2', 'G4', 'D1 (diálogo)', 'D2 (diálogo)'],
});

export const getUsage = defineEndpoint({
  id: 'billing.usage',
  method: 'GET',
  path: '/api/v1/wallet/usage/',
  summary: 'Consumo por día de un mes (solo lo cobrado), separado en proyectos, renders y SEO. El mes y los días son los de la zona horaria del workspace (America/Argentina/Buenos_Aires).',
  query: z.object({ month: z.string().regex(/^\d{4}-\d{2}$/).optional() }),
  response: z.array(UsageDay),
  errors: [{ status: 403, when: 'Solo Dueño y Admin.' }],
  backend: { status: 'proposed', gap: 4 },
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
     * Proposed (gap 4), always present: how many movements and how many tokens per kind, for the
     * same month and project but ignoring `kind` and the page, so the chips can show every count
     * at once. Kinds without movements may be absent.
     */
    totals: z.record(TransactionKind, TransactionKindTotal),
  }),
  errors: [{ status: 403, when: 'Solo Dueño y Admin (Editor y Lector ven únicamente el saldo).' }],
  backend: { status: 'proposed', gap: 4, note: 'Modelo: Transaction. Faltan el vínculo al proyecto y `kind`. Hoy el backend solo escribe DEPOSIT y SPEND: un cobro parcial (`commit_partial_reservation`) baja el monto del SPEND a lo cobrado y lo deja COMPLETED, y devuelve el resto al saldo sin escribir otra fila; una reserva que se cae queda SPEND + FAILED (`kind: "released"`, «Devuelto»). El tipo REFUND existe en el modelo pero nada lo escribe: `kind: "refund"` queda propuesto para devoluciones de algo ya cobrado.' },
  screens: ['G2', 'D1f'],
});

export const exportTransactions = defineEndpoint({
  id: 'billing.exportTransactions',
  method: 'GET',
  path: '/api/v1/transactions/export/',
  summary: 'Los mismos movimientos en CSV. El monto lleva el signo de lo que le hizo al saldo: negativo lo reservado y lo cobrado, positivo lo comprado y lo devuelto.',
  query: TransactionsQuery.omit({ page: true, page_size: true }),
  response: z.string(),
  responseType: 'text',
  errors: [{ status: 403, when: 'Solo Dueño y Admin.' }],
  backend: { status: 'proposed', gap: 4 },
  screens: ['G2'],
});

export const listTokenPackages = defineEndpoint({
  id: 'billing.tokenPackages',
  method: 'GET',
  path: '/api/v1/token-packages/',
  summary: 'Los 3 paquetes fijos, el precio de la cantidad libre y la conversión de referencia.',
  response: TokenPackages,
  errors: [],
  backend: { status: 'proposed', gap: 12 },
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
  errors: [
    { status: 400, when: 'Cantidad por debajo del mínimo o paquete inexistente.' },
    { status: 403, when: 'El rol no puede comprar (solo Dueño y Admin).' },
  ],
  backend: { status: 'proposed', gap: 12 },
  screens: ['G4', 'D2 (diálogo)'],
});

export const getTokenPurchase = defineEndpoint({
  id: 'billing.purchase',
  method: 'GET',
  path: '/api/v1/token-purchases/:id/',
  summary: 'Estado de una compra: pendiente, acreditada o rechazada.',
  response: TokenPurchase,
  errors: [{ status: 404, when: 'La compra no existe en este workspace.' }],
  backend: { status: 'proposed', gap: 12, note: 'El webhook del proveedor acredita; la pantalla consulta cada 5 s mientras está pendiente.' },
  screens: ['G5'],
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
] as const;
