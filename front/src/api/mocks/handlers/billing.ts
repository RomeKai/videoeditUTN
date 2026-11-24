import * as contract from '../../contracts/billing';
import { newId } from '../db';
import { badRequest, dec, forbidden, MockHttpError, notFound, paginate, route } from '../http';
import { priceOf, PRICING, TOKEN_PACKAGES } from '../seed';
import { planOf, serializeSubscription, serializeTransaction, serializeWallet } from '../serialize';
import { SIM, walletOf } from '../sim';

const RANK = { '720p': 1, '1080p': 2, '4K': 3 } as const;

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
    const duration = body.duration_seconds ?? 0;
    if (plan) {
      if (body.source === 'file' && duration > plan.max_video_duration_seconds) {
        throw new MockHttpError(403, { error: `Video duration (${Math.floor(duration)}s) exceeds your plan limit (${plan.max_video_duration_seconds}s).`, code: 'duration', limit: plan.max_video_duration_seconds });
      }
      if (RANK[resolution] > RANK[plan.max_resolution]) {
        throw new MockHttpError(403, { error: `Your plan '${plan.name}' does not support ${resolution} rendering.`, code: 'resolution', limit: plan.max_resolution });
      }
      if (body.use_seo && !plan.has_seo_optimization) {
        throw new MockHttpError(403, { error: 'SEO Optimization is not allowed in your current plan.', code: 'seo' });
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

  route(contract.changePlan, ({ db, workspace, body, now, require }) => {
    require('billing.manage');
    const target = db.plans.find((plan) => plan.id === body.plan_id && plan.is_active);
    if (!target) throw notFound('El plan no existe.');
    const current = planOf(db, workspace.id);
    if (current?.id === target.id) throw badRequest({ plan_id: ['Ya es tu plan actual.'] });
    const upgrade = !current || target.sort_order > current.sort_order;
    if (upgrade) {
      // Upgrades apply right away.
      workspace.subscription_plan = target.id;
      workspace.pending_plan = null;
      workspace.renews_at = new Date(now + 30 * 24 * 3600 * 1000).toISOString();
    } else {
      // Downgrades wait for the end of the period.
      workspace.pending_plan = { id: target.id, effective_at: workspace.renews_at ?? new Date(now + 30 * 24 * 3600 * 1000).toISOString() };
    }
    return { data: { applied: upgrade ? 'now' : 'period_end', checkout_url: null, subscription: serializeSubscription(db, workspace) } };
  }),

  route(contract.getWallet, ({ db, workspace, now }) => ({ data: serializeWallet(db, workspace.id, now) })),

  route(contract.getUsage, ({ db, workspace, query, now, require }) => {
    require('billing.manage');
    const month = query.get('month') ?? new Date(now).toISOString().slice(0, 7);
    const [year, monthNumber] = month.split('-').map(Number) as [number, number];
    const days = new Date(year, monthNumber, 0).getDate();
    const byDay = new Map<string, { projects: number; renders: number; seo: number }>();
    for (const transaction of db.transactions) {
      // Only what was charged: reservations are not consumption yet and released ones never were.
      if (transaction.workspace !== workspace.id || transaction.transaction_type !== 'SPEND' || transaction.status !== 'COMPLETED') continue;
      const day = transaction.created_at.slice(0, 10);
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

  route(contract.listTransactions, ({ db, workspace, query, role }) => {
    const project = query.get('project');
    // Movements are for Dueño and Admin; the project's own activity tab reads them filtered by project.
    if (!project && role !== 'owner' && role !== 'admin') throw forbidden();
    const kind = query.get('kind');
    const month = query.get('month');
    const scoped = db.transactions
      .filter((item) => item.workspace === workspace.id)
      .filter((item) => !month || item.created_at.startsWith(month))
      .filter((item) => !project || item.project?.id === project);
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
    const month = query.get('month');
    const rows = db.transactions
      .filter((item) => item.workspace === workspace.id)
      .filter((item) => !kind || item.kind === kind)
      .filter((item) => !month || item.created_at.startsWith(month))
      .map((item) => [item.created_at, `"${item.description.replace(/"/g, '""')}"`, item.kind, (item.transaction_type === 'SPEND' ? -item.amount : item.amount).toFixed(2)].join(','));
    return { data: ['fecha,concepto,tipo,monto', ...rows].join('\n'), raw: true };
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
    const id = newId();
    const origin = new URL(body.return_url).origin;
    const purchase = {
      id,
      workspace: workspace.id,
      status: 'pending' as const,
      tokens,
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
    const { workspace: _workspace, _settleAt, _reject, ...data } = purchase;
    void _workspace;
    void _settleAt;
    void _reject;
    return { data };
  }),

  route(contract.getTokenPurchase, ({ db, workspace, params, now }) => {
    const purchase = db.purchases.find((item) => item.id === params.id && item.workspace === workspace.id);
    if (!purchase) throw notFound();
    // Coming back from the checkout without an answer yet: the "webhook" lands a few seconds later.
    if (purchase.status === 'pending' && purchase._settleAt === null) purchase._settleAt = now + SIM.purchaseMs;
    const { workspace: _workspace, _settleAt, _reject, ...data } = purchase;
    void _workspace;
    void _settleAt;
    void _reject;
    return { data };
  }),
];
