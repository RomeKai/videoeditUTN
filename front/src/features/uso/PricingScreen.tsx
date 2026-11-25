'use client';

import type { Plan, Pricing } from '@/api/contracts/billing';
import { usePlans, usePricing } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { formatNumber, formatPercent, formatUsd } from '@/lib/format';
import { estimateCost } from '@/lib/pricing';
import { LoadError } from '@/shell/LoadError';
import { useSession } from '@/shell/session';
import { Badge, Button, Card, Icon, Skeleton } from '@/ui';
import { MINUS } from './movements';
import { PlanCard, PlanCardsSkeleton } from './PlanCard';
import styles from './uso.module.css';

const EXAMPLE_SECONDS = 30 * 60;

/** Factors of the formula as they are written: 0.5 → "0,5", 1.5 → "1,5" (formatNumber would pad to "0,50"). */
function factor(value: number): string {
  return String(Math.round(value * 1000) / 1000).replace('.', ',');
}

function multiplier(value: number): string {
  return `×${factor(value)}`;
}

function exampleCost(pricing: Pricing, plan: Pick<Plan, 'base_discount_rate'> | null, seconds = EXAMPLE_SECONDS): number {
  return estimateCost({ durationSeconds: seconds, resolution: '720p' }, pricing, plan).total;
}

function TokensExplained({ pricing, plans }: { pricing: Pricing; plans: ReadonlyArray<Plan> }) {
  const base = exampleCost(pricing, null);
  const discounted = plans.filter((plan) => plan.base_discount_rate > 0);
  const short = plans.filter((plan) => plan.max_video_duration_seconds < EXAMPLE_SECONDS);
  const { resolution_multipliers: multipliers } = pricing;
  return (
    <section className={styles.tokens} id="tokens" aria-label="Tokens">
      <Card className={styles.tokCard}>
        <div className={styles.tokBody}>
          <h2 className="t-h3">Cómo funcionan los tokens</h2>
          <p className="t-small muted">
            Ves el costo antes de confirmar cada video. Si un trabajo falla, te devolvemos los tokens. Los tokens se compran aparte, a{' '}
            <span className="num">USD {formatNumber(pricing.token_price_usd, 3)}</span> cada uno o menos, y no vencen.
          </p>
        </div>
        <dl className={styles.calc}>
          <div>
            <dt>Por segundo de video</dt>
            <dd>{factor(pricing.base_cost_per_second_720p)} tokens</dd>
          </div>
          <div>
            <dt>Resolución</dt>
            <dd>
              720p {multiplier(multipliers['720p'])} · 1080p {multiplier(multipliers['1080p'])} · 4K {multiplier(multipliers['4K'])}
            </dd>
          </div>
          <div>
            <dt>Títulos y descripciones SEO</dt>
            <dd>+{formatNumber(pricing.feature_costs.seo_optimization)} tokens</dd>
          </div>
          {discounted.length > 0 ? (
            <div>
              <dt>Descuento del plan</dt>
              <dd>{discounted.map((plan) => `${MINUS}${formatPercent(plan.base_discount_rate)}`).join(' · ')}</dd>
            </div>
          ) : null}
        </dl>
      </Card>
      <Card className={styles.tokCard}>
        <div className={styles.tokBody}>
          <h2 className="t-h3">Ejemplo de costo</h2>
          <p className="t-small muted">
            Un video de <span className="num">30 min</span> a <span className="num">720p</span>, sin SEO:{' '}
            <span className="num">
              {formatNumber(EXAMPLE_SECONDS, 0)} s × {factor(pricing.base_cost_per_second_720p)} × {factor(multipliers['720p'])}
            </span>
            .
          </p>
          <p className={styles.exampleBig}>
            ≈ {formatNumber(base)} tokens <span>≈ {formatUsd(base * pricing.token_price_usd)}, sin descuento</span>
          </p>
        </div>
        <dl className={styles.calc}>
          {discounted.map((plan) => (
            <div key={plan.id}>
              <dt>
                Con {plan.name}, {MINUS}
                {formatPercent(plan.base_discount_rate)}
              </dt>
              <dd>{formatNumber(exampleCost(pricing, plan))} tokens</dd>
            </div>
          ))}
          {short.map((plan) => (
            <div key={plan.id}>
              <dt>
                En {plan.name}, el máximo: {Math.round(plan.max_video_duration_seconds / 60)} min a 720p
              </dt>
              <dd>≈ {formatNumber(exampleCost(pricing, plan, plan.max_video_duration_seconds))} tokens</dd>
            </div>
          ))}
        </dl>
      </Card>
    </section>
  );
}

/** A6 · Precios: public, the same plan cards as Uso › Planes plus how tokens are priced. */
export function PricingScreen() {
  const { status } = useSession();
  const signedIn = status === 'authenticated';
  const plans = usePlans();
  const pricing = usePricing();
  const base = pricing.data ? exampleCost(pricing.data, null) : null;

  return (
    <div className={styles.pricing}>
      <div className={styles.pricingHead}>
        <h1 className="t-h1">Planes y precios</h1>
        <p className="muted">Elegí según cuánto publicás. Lo que un plan no incluye lo ves igual en la app, con un candado y el plan que lo habilita.</p>
      </div>
      <p className={cx(styles.strip, styles.tokline)}>
        <span className={styles.stripIco}>
          <Icon name="tokens" />
        </span>
        <span className={styles.stripText}>
          El plan define qué podés hacer. Cada video se paga aparte con tokens
          {pricing.data && base !== null ? (
            <>
              : <span className="num">30 min</span> a <span className="num">720p</span> ≈{' '}
              <span className={cx('num', styles.strong)}>{formatNumber(base)} tokens</span>{' '}
              <span className="muted">
                (≈ <span className="num">{formatUsd(base * pricing.data.token_price_usd)}</span>)
              </span>
            </>
          ) : null}
          .
        </span>
        <a className={cx(styles.link, 't-small')} href="#tokens">
          Cómo se calcula
        </a>
      </p>

      {plans.isError ? <LoadError error={plans.error} onRetry={() => void plans.refetch()} fullPageForMissing={false} /> : null}
      {plans.data ? (
        <section className={styles.plans} aria-label="Planes">
          {plans.data.map((plan) => {
            const free = plan.price_usd_monthly === 0;
            return (
              <PlanCard
                key={plan.id}
                plan={plan}
                plans={plans.data}
                recommended={plan.is_recommended}
                badge={plan.is_recommended ? <Badge tone="accent">Recomendado</Badge> : undefined}
                cta={
                  <Button variant={plan.is_recommended ? 'primary' : 'secondary'} block href={signedIn ? '/uso/planes' : '/registro'}>
                    {signedIn ? `Ver ${plan.name} en tu cuenta` : free ? 'Empezar gratis' : `Elegir ${plan.name}`}
                  </Button>
                }
              />
            );
          })}
        </section>
      ) : plans.isError ? null : (
        <PlanCardsSkeleton />
      )}

      {pricing.isError ? <LoadError error={pricing.error} onRetry={() => void pricing.refetch()} fullPageForMissing={false} /> : null}
      {pricing.data && plans.data ? (
        <TokensExplained pricing={pricing.data} plans={plans.data} />
      ) : pricing.isError || plans.isError ? null : (
        <div className={styles.tokens} aria-busy="true">
          <Skeleton height={220} radius={8} />
          <Skeleton height={220} radius={8} />
        </div>
      )}
    </div>
  );
}
