import * as contract from '../../contracts/billing';
import { newId, workspaceDateTime, workspaceDay, type MockDb, type MockPurchase, type MockTransaction } from '../db';
import { badRequest, dec, MockHttpError, notFound, paginate, route } from '../http';
import { priceOf, PRICING, TOKEN_PACKAGES } from '../seed';
import { chargedAt, planOf, serializeSubscription, serializeTransaction, serializeWallet } from '../serialize';
import { SIM, tick, walletOf } from '../sim';

const RANK = { '720p': 1, '1080p': 2, '4K': 3 } as const;

/** Like the backend's _format_duration: "45 s", "10 min", "1 h 30 min". */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min` : `${minutes} min`;
}

/** How the CSV names each kind (the screen's labels, KIND_META). */
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

function movementsOf(transactions: MockTransaction[], workspace: string, query: URLSearchParams): MockTransaction[] {
  const month = query.get('month');
  const project = query.get('project');
  return transactions
    .filter((item) => item.workspace === workspace)
    .filter((item) => !month || workspaceDay(item.created_at).startsWith(month))
    .filter((item) => !project || item.project?.id === project);
}

/** Like the backend (services/token_packages.py): no purchase above this many tokens. */
const CUSTOM_MAX_TOKENS = 1_000_000;
const REPLACED = 'Elegiste otro plan antes de pagar este.';
const KEPT = 'Elegiste seguir con tu plan antes de pagar este.';

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
  const { workspace: _workspace, _settleAt, _reject, ...data } = purchase;
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
    if (body.source === 'link' && resolution !== '720p') {
      throw badRequest({ render_resolution: ['Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo.'] });
    }
    if (body.source === 'file' && body.duration_seconds === undefined) {
      throw badRequest({ duration_seconds: ['Mandá la duración del archivo, en segundos.'] });
    }
    const duration = body.duration_seconds ?? 0;
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

  route(contract.changePlan, ({ db, workspace, body, now, require, request }) => {
    require('billing.manage');
    const target = db.plans.find((plan) => plan.id === body.plan_id && plan.is_active);
    if (!target) throw notFound('El plan no existe.');
    const current = planOf(db, workspace.id);
    if (current?.id === target.id) {
      // Choosing the current plan again cancels a scheduled change and the upgrade waiting for its payment.
      const waiting = db.purchases.some((item) => item.workspace === workspace.id && item.status === 'pending' && item.plan_id);
      if (!workspace.pending_plan && !waiting) throw badRequest({ plan_id: ['Ya es tu plan actual.'] });
      replacePendingUpgrade(db, workspace.id, null, KEPT);
      workspace.pending_plan = null;
      return { data: { applied: 'now', checkout_url: null, subscription: serializeSubscription(db, workspace) } };
    }
    // Like the backend, the price decides: only a pricier plan is paid for; a cheaper one, or one of the same price, waits.
    const currentPrice = current?.price_usd_monthly ?? 0;
    const upgrade = target.price_usd_monthly > currentPrice;
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
    if (upgrade && db.flags.planCheckout) {
      // The upgrade needs a payment: nothing changes until the provider confirms it. The same upgrade again reuses its checkout.
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
      });
      return { data: { applied: 'now', checkout_url: checkoutUrl, subscription: serializeSubscription(db, workspace) } };
    }
    replacePendingUpgrade(db, workspace.id, null);
    // Downgrades wait for the end of the paid period; without one (or once it ended) there is nothing to wait for.
    const periodEnd = workspace.renews_at && new Date(workspace.renews_at).getTime() > now ? workspace.renews_at : null;
    if (upgrade) {
      // Upgrades apply right away.
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
    return { data: { applied: upgrade || !periodEnd ? 'now' : 'period_end', checkout_url: null, subscription: serializeSubscription(db, workspace) } };
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
      const day = workspaceDay(chargedAt(transaction));
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
    const totals: Record<string, { count: number; amount: number }> = {};
    for (const item of scoped) {
      const entry = (totals[item.kind] ??= { count: 0, amount: 0 });
      entry.count += 1;
      entry.amount = Math.round((entry.amount + item.amount) * 100) / 100;
    }
    const items = scoped.filter((item) => !kind || item.kind === kind).map(serializeTransaction);
    return { data: { ...paginate(items, query, contract.listTransactions.path), totals } };
  }),

  route(contract.exportTransactions, ({ db, workspace, query, require }) => {
    require('billing.manage');
    const kind = query.get('kind');
    const rows = movementsOf(db.transactions, workspace.id, query)
      .filter((item) => !kind || item.kind === kind)
      // Same sign as the screen: what left the balance is negative, what came (back) to it is positive.
      .map((item) =>
        [
          workspaceDateTime(item.created_at),
          csvText(item.description),
          csvText(item.project?.title ?? ''),
          KIND_LABEL[item.kind],
          csvAmount(item.kind === 'reserved' || item.kind === 'charged' ? -item.amount : item.amount),
        ].join(','),
      );
    return { data: [['fecha', 'concepto', 'proyecto', 'tipo', 'monto'].join(','), ...rows].join('\n'), raw: true };
  }),

  route(contract.listTokenPackages, () => ({ data: TOKEN_PACKAGES })),

  route(contract.createTokenPurchase, ({ db, workspace, body, now, require }) => {
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
