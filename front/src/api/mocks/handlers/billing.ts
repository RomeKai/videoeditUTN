import * as contract from '../../contracts/billing';
import { newId, workspaceDateTime, workspaceDay, type MockDb, type MockPurchase, type MockTransaction } from '../db';
import { badRequest, conflict, dec, MockHttpError, notFound, paginate, route } from '../http';
import { priceOf, PRICING, TOKEN_PACKAGES } from '../seed';
import { movedAt, planOf, serializeSubscription, serializeTransaction, serializeWallet } from '../serialize';
import { SIM, tick, walletOf } from '../sim';

const RANK = { '720p': 1, '1080p': 2, '4K': 3 } as const;

/** Like the backend's _format_duration: "45 s", "10 min", "1 h 30 min". */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min` : `${minutes} min`;
}

/** How the CSV names the state of each transaction (the screen's labels, KIND_META). */
const KIND_LABEL: Record<contract.TransactionKind, string> = {
  purchase: 'Compra', reserved: 'Reservado', charged: 'Cobrado', refund: 'Devolución', released: 'Devuelto', pending: 'Pendiente',
};

/** A text cell of the CSV: quoted, and with an apostrophe in front of what a spreadsheet would run. */
function csvText(value: string): string {
  const safe = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** Two decimals when that is exact, else every decimal it has (the backend keeps ten). */
function csvAmount(value: number): string {
  return Math.round(value * 100) / 100 === value ? value.toFixed(2) : String(value);
}

const cents = (value: number) => Math.round(value * 100) / 100;

/** The workspace's movements with `month` (by when each one last moved the balance) and `project`, newest first. */
function movementsOf(transactions: MockTransaction[], workspace: string, query: URLSearchParams): MockTransaction[] {
  const month = query.get('month');
  const project = query.get('project');
  return transactions
    .filter((item) => item.workspace === workspace)
    .filter((item) => !month || workspaceDay(movedAt(item)).startsWith(month))
    .filter((item) => !project || item.project?.id === project)
    .sort((a, b) => movedAt(b).localeCompare(movedAt(a)) || b.id.localeCompare(a.id));
}

/** What a partial charge gave back: reserved_amount − amount (the backend writes no row for it). */
function returnedPart(item: MockTransaction): number {
  return item.kind === 'charged' && item.reserved_amount !== null && item.reserved_amount > item.amount ? cents(item.reserved_amount - item.amount) : 0;
}

interface BalanceChange {
  at: string;
  /** A reservation before its return when both happen at the same instant. */
  order: number;
  item: MockTransaction;
  what: 'Reserva' | 'Devuelto' | 'Compra' | 'Devolución';
  amount: number;
}

/** Like the backend's balance_changes: each time a transaction moved the available balance. */
function balanceChanges(item: MockTransaction): BalanceChange[] {
  const settled = item.settled_at ?? item.created_at;
  if (item.transaction_type === 'SPEND') {
    const reserved = item.reserved_amount ?? item.amount;
    const changes: BalanceChange[] = [{ at: item.created_at, order: 0, item, what: 'Reserva', amount: -reserved }];
    if (item.status === 'FAILED') changes.push({ at: settled, order: 1, item, what: 'Devuelto', amount: reserved });
    else if (returnedPart(item) > 0) changes.push({ at: settled, order: 1, item, what: 'Devuelto', amount: returnedPart(item) });
    return changes;
  }
  if (item.status !== 'COMPLETED') return []; // a pending purchase has not moved the balance yet
  return [{ at: settled, order: 0, item, what: item.transaction_type === 'DEPOSIT' ? 'Compra' : 'Devolución', amount: item.amount }];
}

/** Like the backend (services/token_packages.py): no purchase above this many tokens. */
const CUSTOM_MAX_TOKENS = 1_000_000;
const REPLACED = 'Elegiste otro plan antes de pagar este.';
const KEPT = 'Elegiste seguir con tu plan antes de pagar este.';
/** The 409s of `billing.cancelPending`, with the backend's texts (views_subscription.py). */
const PENDING_CHANGED = 'Mientras tanto cambió lo que estaba pendiente. No cancelamos nada: revisá cómo quedó y, si querés, probá de nuevo.';
const NOTHING_LEFT_TO_CANCEL = 'Ya no hay nada pendiente para cancelar.';

/** Like the backend: choosing a plan rejects the upgrade payment that was waiting, unless it is for that same plan. */
function replacePendingUpgrade(db: MockDb, workspaceId: string, keepPlanId: string | null, reason = REPLACED) {
  let kept: MockPurchase | null = null;
  for (const purchase of db.purchases) {
    if (purchase.workspace !== workspaceId || purchase.status !== 'pending' || !purchase.plan_id) continue;
    if (purchase.plan_id === keepPlanId && purchase.checkout_url) {
      kept = purchase;
      continue;
    }
    purchase.status = 'rejected';
    purchase.failure_reason = reason;
    purchase.checkout_url = null;
    purchase._settleAt = null;
  }
  return kept;
}

function publicPurchase(purchase: MockPurchase) {
  const { workspace: _workspace, _settleAt, _reject, _by, ...data } = purchase;
  void _by;
  void _workspace;
  void _settleAt;
  void _reject;
  return data;
}

export const billingHandlers = [
  route(contract.listPlans, ({ db }) => ({ data: [...db.plans].sort((a, b) => a.sort_order - b.sort_order) })),

  route(contract.getPricing, () => ({
    data: {
      ...PRICING,
      base_cost_per_second_720p: dec(PRICING.base_cost_per_second_720p, 1),
      link_flat_cost: dec(PRICING.link_flat_cost, 1),
      upload_extensions: [...PRICING.upload_extensions],
    },
  })),

  route(contract.estimateCost, ({ db, workspace, body }) => {
    const plan = planOf(db, workspace.id);
    const resolution = body.render_resolution ?? '720p';
    // The creation's own 400s (price_new_project in the backend).
    if (body.source === 'link' && resolution !== '720p') {
      throw badRequest({ error: 'Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo.' });
    }
    if (body.source === 'file' && body.duration_seconds === undefined) {
      throw badRequest({ duration_seconds: ['Mandá la duración del archivo, en segundos.'] });
    }
    const duration = body.duration_seconds ?? 0;
    if (body.source === 'file' && duration > PRICING.upload_max_duration_seconds) {
      throw badRequest({ source_file: [`El video supera la duración máxima que podemos procesar (${formatDuration(PRICING.upload_max_duration_seconds)}).`] });
    }
    if (plan) {
      if (body.source === 'file' && Math.floor(duration) > plan.max_video_duration_seconds) {
        throw new MockHttpError(403, { error: `Tu plan ${plan.name} permite videos de hasta ${formatDuration(plan.max_video_duration_seconds)}.`, code: 'duration', limit: plan.max_video_duration_seconds });
      }
      if (RANK[resolution] > RANK[plan.max_resolution]) {
        throw new MockHttpError(403, { error: `Tu plan ${plan.name} renderiza hasta ${plan.max_resolution}.`, code: 'resolution', limit: plan.max_resolution });
      }
      if (body.use_seo && !plan.has_seo_optimization) {
        throw new MockHttpError(403, { error: `Tu plan ${plan.name} no incluye el SEO.`, code: 'seo' });
      }
    }
    const rate = plan?.base_discount_rate ?? 0;
    const seo = body.use_seo ? PRICING.feature_costs.seo_optimization : 0;
    const base = body.source === 'link' ? PRICING.link_flat_cost : Math.floor(duration) * PRICING.base_cost_per_second_720p * PRICING.resolution_multipliers[resolution];
    const total = body.source === 'link' ? PRICING.link_flat_cost + (body.use_seo ? priceOf(0, '720p', true, rate) : 0) : priceOf(duration, resolution, Boolean(body.use_seo), rate);
    const wallet = walletOf(db, workspace.id);
    return {
      data: {
        base_cost: dec(base),
        add_ons_cost: dec(seo),
        discount_rate: dec(rate),
        discount_amount: dec(Math.max(0, base + seo - total)),
        total: dec(total),
        available_balance: dec(wallet.available_balance),
        balance_after: dec(wallet.available_balance - total),
        sufficient: wallet.available_balance >= total,
        has_watermark: plan?.has_watermark ?? true,
        eta_seconds: body.source === 'link' ? null : Math.max(60, Math.round(duration / 6)),
      },
    };
  }),

  route(contract.getSubscription, ({ db, workspace }) => ({ data: serializeSubscription(db, workspace) })),

  route(contract.changePlan, ({ db, user, workspace, body, now, require, request }) => {
    require('billing.manage');
    const target = db.plans.find((plan) => plan.id === body.plan_id && plan.is_active);
    if (!target) throw notFound('El plan no existe.');
    const current = planOf(db, workspace.id);
    if (current?.id === target.id) {
      // Choosing the current plan again cancels a scheduled change and the upgrade waiting for its payment.
      const waiting = db.purchases.some((item) => item.workspace === workspace.id && item.status === 'pending' && item.plan_id);
      if (!workspace.pending_plan && !waiting) throw badRequest({ error: 'Ya es tu plan actual.', code: 'nothing_to_cancel' });
      replacePendingUpgrade(db, workspace.id, null, KEPT);
      workspace.pending_plan = null;
      return { data: { applied: 'now', checkout_url: null, subscription: serializeSubscription(db, workspace) } };
    }
    // Like the backend: every paid plan other than the current one is a purchase that applies at once
    // (pricier, cheaper or the same price); only a free plan waits for the end of the period.
    const currentPrice = current?.price_usd_monthly ?? 0;
    const purchase = target.price_usd_monthly > 0;
    // No change leaves more seats in use (members plus pending invitations) than the new plan has.
    const seatsUsed = db.members.filter((member) => member.workspace === workspace.id).length;
    if (seatsUsed > target.max_members) {
      throw new MockHttpError(409, {
        error: `Ocupás ${seatsUsed} lugares y el plan ${target.name} tiene ${target.max_members}. Quitá miembros o invitaciones antes de ${target.price_usd_monthly < currentPrice ? 'bajar' : 'cambiar de plan'}.`,
        code: 'seats_exceeded',
        seats_used: seatsUsed,
        max_members: target.max_members,
      });
    }
    if (purchase && db.flags.planCheckout) {
      // The plan needs a payment: nothing changes until the provider confirms it. The same plan again reuses its checkout.
      const waiting = replacePendingUpgrade(db, workspace.id, target.id);
      if (waiting) return { data: { applied: 'now', checkout_url: waiting.checkout_url, subscription: serializeSubscription(db, workspace) } };
      const id = newId();
      const returnUrl = body.return_url ?? `${new URL(request.url).origin}/uso/planes`;
      const checkoutUrl = `${new URL(returnUrl).origin}/dev/checkout?compra=${id}&volver=${encodeURIComponent(returnUrl)}`;
      db.purchases.push({
        id,
        workspace: workspace.id,
        status: 'pending',
        tokens: 0,
        amount_usd: target.price_usd_monthly,
        plan_id: target.id,
        checkout_url: checkoutUrl,
        failure_reason: null,
        created_at: new Date(now).toISOString(),
        balance_after: null,
        _settleAt: null,
        _reject: db.flags.rejectPayments,
        _by: user.id,
      });
      return { data: { applied: 'now', checkout_url: checkoutUrl, subscription: serializeSubscription(db, workspace) } };
    }
    replacePendingUpgrade(db, workspace.id, null);
    // The free plan waits for the end of the paid period; without one (or once it ended) there is nothing to wait for.
    const periodEnd = workspace.renews_at && new Date(workspace.renews_at).getTime() > now ? workspace.renews_at : null;
    if (purchase) {
      // Without the checkout scenario a paid plan applies right away, with a month of its own.
      workspace.subscription_plan = target.id;
      workspace.pending_plan = null;
      workspace.renews_at = new Date(now + 30 * 24 * 3600 * 1000).toISOString();
    } else if (periodEnd) {
      workspace.pending_plan = { id: target.id, effective_at: periodEnd };
    } else {
      workspace.subscription_plan = target.id;
      workspace.pending_plan = null;
      workspace.renews_at = null;
    }
    return { data: { applied: purchase || !periodEnd ? 'now' : 'period_end', checkout_url: null, subscription: serializeSubscription(db, workspace) } };
  }),

  route(contract.cancelPending, ({ db, workspace, body, require }) => {
    require('billing.manage');
    if (!body.payment_id && !body.pending_plan_id) {
      throw badRequest({ non_field_errors: ['Decí qué cancelar: el pago pendiente o el cambio programado.'] });
    }
    // Like the backend (cancel_pending_change): only what the screen showed, and never a change of plan.
    const current = planOf(db, workspace.id);
    if (!current || current.id !== body.current_plan_id) {
      const nowOn = current ? `: ahora estás en el plan ${current.name}` : '';
      throw conflict(`Tu plan cambió mientras tanto${nowOn}. No cancelamos nada.`, 'plan_changed');
    }
    const waiting = db.purchases.find((item) => item.workspace === workspace.id && item.status === 'pending' && item.plan_id);
    const cancelPayment = Boolean(waiting && waiting.id === body.payment_id);
    const cancelDowngrade = Boolean(workspace.pending_plan && workspace.pending_plan.id === body.pending_plan_id);
    // Something waits that the screen did not show (another payment, another downgrade): nothing is canceled.
    if ((waiting && !cancelPayment) || (workspace.pending_plan && !cancelDowngrade)) throw conflict(PENDING_CHANGED, 'pending_changed');
    // Settled elsewhere (rejected, expired, kept in another tab): nothing is left of what it showed.
    if (!cancelPayment && !cancelDowngrade) throw conflict(NOTHING_LEFT_TO_CANCEL, 'nothing_to_cancel');
    if (cancelPayment) replacePendingUpgrade(db, workspace.id, null, KEPT);
    if (cancelDowngrade) workspace.pending_plan = null;
    return { data: { subscription: serializeSubscription(db, workspace) } };
  }),

  route(contract.getWallet, ({ db, workspace, now }) => ({ data: serializeWallet(db, workspace.id, now) })),

  route(contract.getUsage, ({ db, workspace, query, now, require }) => {
    require('billing.manage');
    const month = query.get('month') ?? workspaceDay(now).slice(0, 7);
    const [year, monthNumber] = month.split('-').map(Number) as [number, number];
    const days = new Date(year, monthNumber, 0).getDate();
    const byDay = new Map<string, { projects: number; renders: number; seo: number }>();
    for (const transaction of db.transactions) {
      // Only what was charged: reservations are not consumption yet and released ones never were.
      if (transaction.workspace !== workspace.id || transaction.transaction_type !== 'SPEND' || transaction.status !== 'COMPLETED') continue;
      // On the day it was charged, like the backend (`settled_at`).
      const day = workspaceDay(movedAt(transaction));
      if (!day.startsWith(month)) continue;
      const entry = byDay.get(day) ?? { projects: 0, renders: 0, seo: 0 };
      if (transaction.category === 'render') entry.renders += transaction.amount;
      else if (transaction.category === 'seo') entry.seo += transaction.amount;
      else entry.projects += transaction.amount;
      byDay.set(day, entry);
    }
    return {
      data: Array.from({ length: days }, (_, index) => {
        const date = `${month}-${String(index + 1).padStart(2, '0')}`;
        const entry = byDay.get(date) ?? { projects: 0, renders: 0, seo: 0 };
        return { date, projects: dec(entry.projects), renders: dec(entry.renders), seo: dec(entry.seo) };
      }),
    };
  }),

  route(contract.listTransactions, ({ db, workspace, query, require }) => {
    // Movements are for Dueño and Admin, also those of one project (the backend's billing.manage).
    require('billing.manage');
    const kind = query.get('kind');
    const scoped = movementsOf(db.transactions, workspace.id, query);
    const totals: Record<string, { count: number; amount: number; partial_count?: number }> = {};
    let partialCount = 0;
    let partialAmount = 0;
    for (const item of scoped) {
      const entry = (totals[item.kind] ??= { count: 0, amount: 0 });
      entry.count += 1;
      entry.amount = cents(entry.amount + item.amount);
      if (returnedPart(item) > 0) {
        partialCount += 1;
        partialAmount = cents(partialAmount + returnedPart(item));
      }
    }
    // "Devuelto" is also what partial charges gave back; its count stays the rows its filter lists.
    if (partialCount > 0 || totals.released) {
      const released = totals.released ?? { count: 0, amount: 0 };
      totals.released = { ...released, amount: cents(released.amount + partialAmount), partial_count: partialCount };
    }
    const items = scoped.filter((item) => !kind || item.kind === kind).map(serializeTransaction);
    return { data: { ...paginate(items, query, contract.listTransactions.path), totals } };
  }),

  route(contract.exportTransactions, ({ db, workspace, query, require }) => {
    require('billing.manage');
    const kind = query.get('kind');
    const month = query.get('month');
    const project = query.get('project');
    // The ledger: one row per change of the balance, dated when it happened, so a month adds up to what the balance did.
    const changes = db.transactions
      .filter((item) => item.workspace === workspace.id && (!kind || item.kind === kind) && (!project || item.project?.id === project))
      .flatMap(balanceChanges)
      .filter((change) => !month || workspaceDay(change.at).startsWith(month))
      .sort((a, b) => b.at.localeCompare(a.at) || b.order - a.order || b.item.id.localeCompare(a.item.id));
    const rows = changes.map((change) =>
      [
        workspaceDateTime(change.at),
        csvText(change.item.description),
        csvText(change.item.project?.title ?? ''),
        change.what,
        csvAmount(change.amount),
        KIND_LABEL[change.item.kind],
        change.item.id,
      ].join(','),
    );
    return { data: [['fecha', 'concepto', 'proyecto', 'tipo', 'monto', 'estado', 'transaccion'].join(','), ...rows].join('\n'), raw: true };
  }),

  route(contract.listTokenPackages, () => ({ data: TOKEN_PACKAGES })),

  route(contract.createTokenPurchase, ({ db, user, workspace, body, now, require }) => {
    require('billing.manage');
    const pack = body.package_id ? TOKEN_PACKAGES.packages.find((item) => item.id === body.package_id) : null;
    if (body.package_id && !pack) throw badRequest({ package_id: ['El paquete no existe.'] });
    const tokens = pack ? pack.tokens : (body.custom_tokens ?? 0);
    if (!pack && tokens < TOKEN_PACKAGES.custom_min_tokens) {
      throw badRequest({ custom_tokens: [`El mínimo es ${TOKEN_PACKAGES.custom_min_tokens} tokens.`] });
    }
    if (!pack && tokens > CUSTOM_MAX_TOKENS) {
      throw badRequest({ custom_tokens: ['El máximo es 1.000.000 tokens por compra.'] });
    }
    const id = newId();
    const origin = new URL(body.return_url).origin;
    const purchase: MockPurchase = {
      id,
      workspace: workspace.id,
      status: 'pending' as const,
      tokens,
      plan_id: null,
      amount_usd: pack ? pack.price_usd : Math.round(tokens * TOKEN_PACKAGES.custom_price_per_token_usd * 100) / 100,
      // The fake provider checkout lives in the app (dev route) and comes back to return_url.
      checkout_url: `${origin}/dev/checkout?compra=${id}&volver=${encodeURIComponent(body.return_url)}`,
      failure_reason: null,
      created_at: new Date(now).toISOString(),
      balance_after: null,
      _settleAt: null,
      _reject: db.flags.rejectPayments,
      _by: user.id,
    };
    db.purchases.push(purchase);
    return { data: publicPurchase(purchase) };
  }),

  route(contract.getTokenPurchase, ({ db, workspace, params, now, require }) => {
    require('billing.manage');
    const purchase = db.purchases.find((item) => item.id === params.id && item.workspace === workspace.id);
    if (!purchase) throw notFound('No encontramos ese pago.');
    // Coming back from the checkout without an answer yet: the "webhook" lands a few seconds later.
    if (purchase.status === 'pending' && purchase._settleAt === null) purchase._settleAt = now + SIM.purchaseMs;
    return { data: publicPurchase(purchase) };
  }),

  // The backend's simulated provider settles at once. The mock checkout page (/dev/checkout) does not call
  // this in mock mode: it leaves the decision in the mock database and the payment settles a few seconds
  // after the app comes back (mocks/control.ts · resolveMockPurchase).
  route(contract.simulatePurchase, ({ db, workspace, params, body, now, require }) => {
    require('billing.manage');
    const purchase = db.purchases.find((item) => item.id === params.id && item.workspace === workspace.id);
    if (!purchase) throw notFound('No encontramos ese pago.');
    if (purchase.status === 'pending') {
      purchase._reject = !body.approved || db.flags.rejectPayments;
      purchase._settleAt = now;
      tick(db, now);
    }
    return { data: publicPurchase(purchase) };
  }),
];
