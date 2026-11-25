import type { Plan, Pricing } from '@/api/contracts/billing';
import type { RenderResolution } from '@/api/contracts/common';

export interface CostInput {
  /** Seconds. Ignored for links (flat price). */
  durationSeconds: number;
  resolution: RenderResolution;
  useSeo?: boolean;
  source?: 'file' | 'link';
}

export interface CostEstimate {
  /** duration × base × multiplier (or the flat link price). */
  base: number;
  multiplier: number;
  addOns: number;
  discountRate: number;
  discount: number;
  total: number;
}

function round2(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

/**
 * Same formula as PricingEngine.calculate_render_cost, with the values read
 * from `GET /pricing/` (map 1.5): total = (duration × base × multiplier + add-ons) × (1 − discount),
 * floored at `min_cost` and rounded to 2 decimals. The real charge is decided by the backend.
 *
 * Links: flat `link_flat_cost`, plus the SEO add-on priced by the engine with duration 0
 * (so the plan discount applies to the add-on only), as views.py does.
 */
export function estimateCost(input: CostInput, pricing: Pricing, plan: Pick<Plan, 'base_discount_rate'> | null): CostEstimate {
  const discountRate = plan?.base_discount_rate ?? 0;
  const seo = input.useSeo ? pricing.feature_costs.seo_optimization : 0;
  if (input.source === 'link') {
    const addOns = input.useSeo ? Math.max(pricing.min_cost, round2(seo * (1 - discountRate))) : 0;
    return {
      base: pricing.link_flat_cost,
      multiplier: 1,
      addOns,
      discountRate,
      discount: round2(seo - addOns > 0 && input.useSeo ? seo - addOns : 0),
      total: round2(pricing.link_flat_cost + addOns),
    };
  }
  const multiplier = pricing.resolution_multipliers[input.resolution] ?? 1;
  const base = Math.max(0, Math.floor(input.durationSeconds)) * pricing.base_cost_per_second_720p * multiplier;
  const gross = base + seo;
  const total = Math.max(pricing.min_cost, round2(gross * (1 - discountRate)));
  return { base: round2(base), multiplier, addOns: seo, discountRate, discount: round2(gross - total), total };
}

const RESOLUTION_RANK: Record<RenderResolution, number> = { '720p': 1, '1080p': 2, '4K': 3 };

/** Whether the plan allows a resolution (options above the plan show a lock). */
export function planAllowsResolution(plan: Pick<Plan, 'max_resolution'> | null, resolution: RenderResolution): boolean {
  if (!plan) return true;
  return RESOLUTION_RANK[resolution] <= RESOLUTION_RANK[plan.max_resolution];
}

/** The cheapest plan (by sort order) that satisfies a predicate: "Disponible en el plan X". */
export function firstPlanWith<P extends Pick<Plan, 'sort_order' | 'name'>>(plans: ReadonlyArray<P>, predicate: (plan: P) => boolean): P | null {
  return [...plans].sort((a, b) => a.sort_order - b.sort_order).find(predicate) ?? null;
}

/** How many seconds of 720p video a number of tokens buys with a plan's discount ("≈ 37 min de video"). */
export function secondsForTokens(tokens: number, pricing: Pricing, plan: Pick<Plan, 'base_discount_rate'> | null): number {
  const perSecond = pricing.base_cost_per_second_720p * (1 - (plan?.base_discount_rate ?? 0));
  return perSecond > 0 ? Math.floor(tokens / perSecond) : 0;
}
