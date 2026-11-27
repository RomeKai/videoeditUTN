import type { ReactNode } from 'react';
import type { Plan } from '@/api/contracts/billing';
import { cx } from '@/lib/cx';
import { formatPercent, formatUsd } from '@/lib/format';
import { firstPlanWith } from '@/lib/pricing';
import { Card, Icon } from '@/ui';
import styles from './uso.module.css';

export interface PlanFeature {
  key: string;
  label: string;
  /** False: the plan does not include it (lock, with the plan that does at the right). */
  included: boolean;
  /** "60 min", "1080p", "5 %". */
  value?: string;
  /** Name of the cheapest plan that includes it. */
  availableIn?: string;
}

const RANK = { '720p': 1, '1080p': 2, '4K': 3 } as const;

/** The rows of a plan card, from the fields of `SubscriptionPlan` (map 1.6). Same order in A6 and G3. */
export function planFeatures(plan: Plan, plans: ReadonlyArray<Plan>): PlanFeature[] {
  const from = (predicate: (item: Plan) => boolean) => firstPlanWith(plans, predicate)?.name;
  return [
    { key: 'duration', label: 'Videos de hasta', included: true, value: `${Math.round(plan.max_video_duration_seconds / 60)} min` },
    { key: 'resolution', label: 'Resolución hasta', included: true, value: plan.max_resolution },
    { key: 'watermark', label: 'Sin marca de agua', included: !plan.has_watermark, availableIn: from((item) => !item.has_watermark) },
    { key: 'scheduling', label: 'Programar publicaciones', included: plan.allow_scheduling, availableIn: from((item) => item.allow_scheduling) },
    { key: 'crossposting', label: 'Publicar en varias redes', included: plan.allow_crossposting, availableIn: from((item) => item.allow_crossposting) },
    // A backend that does not send the flag yet (rolling deploy): the row is left out rather than guessed.
    ...(plan.allow_social_accounts === undefined
      ? []
      : [{ key: 'social', label: 'Conectar redes sociales', included: plan.allow_social_accounts, availableIn: from((item) => item.allow_social_accounts === true) }]),
    { key: 'seo', label: 'Títulos y descripciones SEO', included: plan.has_seo_optimization, availableIn: from((item) => item.has_seo_optimization) },
    { key: 'members', label: 'Miembros', included: true, value: String(plan.max_members) },
    {
      key: 'discount',
      label: 'Descuento en tokens',
      included: plan.base_discount_rate > 0,
      value: plan.base_discount_rate > 0 ? formatPercent(plan.base_discount_rate) : undefined,
      availableIn: from((item) => item.base_discount_rate > 0),
    },
  ];
}

/** What a workspace stops having when it goes from `current` to `target` (shown before confirming a downgrade). */
export function planLosses(current: Plan, target: Plan): string[] {
  const losses: string[] = [];
  const minutes = (plan: Plan) => `${Math.round(plan.max_video_duration_seconds / 60)} min`;
  if (target.max_video_duration_seconds < current.max_video_duration_seconds) losses.push(`Videos de hasta ${minutes(target)} (hoy ${minutes(current)})`);
  if (RANK[target.max_resolution] < RANK[current.max_resolution]) losses.push(`Resolución hasta ${target.max_resolution} (hoy ${current.max_resolution})`);
  if (target.has_watermark && !current.has_watermark) losses.push('Los clips vuelven a salir con marca de agua');
  if (!target.allow_scheduling && current.allow_scheduling) losses.push('Programar publicaciones');
  if (!target.allow_crossposting && current.allow_crossposting) losses.push('Publicar en varias redes a la vez');
  // What is connected stays and still publishes; connecting or reconnecting a network is what goes.
  if (target.allow_social_accounts === false && current.allow_social_accounts === true) losses.push('Conectar o reconectar redes (las conectadas siguen publicando)');
  if (!target.has_seo_optimization && current.has_seo_optimization) losses.push('Títulos y descripciones SEO');
  if (target.max_members < current.max_members) losses.push(`Miembros: ${target.max_members} (hoy ${current.max_members})`);
  if (target.base_discount_rate < current.base_discount_rate) {
    losses.push(
      target.base_discount_rate > 0
        ? `Descuento en tokens: ${formatPercent(target.base_discount_rate)} (hoy ${formatPercent(current.base_discount_rate)})`
        : `El ${formatPercent(current.base_discount_rate)} de descuento en tokens`,
    );
  }
  return losses;
}

export interface PlanCardProps {
  plan: Plan;
  plans: ReadonlyArray<Plan>;
  /** The accent border of the recommended plan. */
  recommended?: boolean;
  /** G3: strong border and the "Tu plan" label. */
  current?: boolean;
  /** Label at the right of the name ("Recomendado", "Tu plan"). */
  badge?: ReactNode;
  /** Button and the line under it. */
  cta: ReactNode;
}

/** One plan: same card and same texts in Precios (A6) and Uso › Planes (G3) (map 9.1.11). */
export function PlanCard({ plan, plans, recommended = false, current = false, badge, cta }: PlanCardProps) {
  return (
    <Card
      as="article"
      className={cx(styles.plan, recommended && !current && styles.planRec, current && styles.planCurrent)}
      aria-label={`Plan ${plan.name}${current ? ', tu plan' : recommended ? ', recomendado' : ''}`}
      aria-current={current ? 'true' : undefined}
    >
      <div className={styles.planTop}>
        <h2 className="t-h3">{plan.name}</h2>
        {badge}
      </div>
      <p className={styles.price}>
        <span className="num">{formatUsd(plan.price_usd_monthly)}</span>
        <span className={cx('num', styles.per)}>/mes</span>
      </p>
      <p className={cx('t-small muted', styles.planDesc)}>{plan.description}</p>
      <div className={styles.cta}>{cta}</div>
      <ul className={styles.feats}>
        {planFeatures(plan, plans).map((feature) => (
          <li key={feature.key} className={feature.included ? undefined : styles.off}>
            <Icon name={feature.included ? 'check' : 'lock'} size={14} />
            <span className={styles.fl}>{feature.label}</span>
            {feature.included ? (
              feature.value ? (
                <span className={cx('num', styles.fv, styles.fvOn)}>{feature.value}</span>
              ) : (
                <span className="sr-only">Incluido</span>
              )
            ) : feature.availableIn ? (
              <span className={styles.fv}>
                <span className="sr-only">Disponible desde el </span>Plan {feature.availableIn}
              </span>
            ) : (
              <span className="sr-only">No incluido</span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function PlanCardsSkeleton() {
  return (
    <div className={styles.plans} aria-busy="true">
      <span className="sr-only">Cargando los planes</span>
      {[0, 1, 2, 3].map((item) => (
        <Card key={item} className={styles.plan} style={{ minHeight: 420 }} />
      ))}
    </div>
  );
}
