'use client';

import Link from 'next/link';
import { useState } from 'react';
import type { Plan } from '@/api/contracts/billing';
import { useChangePlan, useMembers, usePlans, usePricing, useSubscription } from '@/api/hooks';
import { ConfirmDialog } from '@/dialogs';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { formatDate, formatNumber, formatPercent, formatUsd } from '@/lib/format';
import { estimateCost } from '@/lib/pricing';
import { LoadError } from '@/shell/LoadError';
import { usePermissions, useSession } from '@/shell/session';
import { Badge, Banner, Button, Icon, useToast } from '@/ui';
import { leaveTo } from './leave';
import { PlanCard, PlanCardsSkeleton, planLosses } from './PlanCard';
import styles from './uso.module.css';

/**
 * G3 · Uso › Planes: the cards of Precios with the current plan marked.
 * Going up applies right away; going down waits for the end of the period and
 * shows what is lost before confirming. Only Dueño and Admin change the plan;
 * the rest see the same cards with the buttons disabled.
 */
export function PlansScreen() {
  const toast = useToast();
  const { workspace } = useSession();
  const { can, gate } = usePermissions();
  const admin = can('billing.manage');
  const plans = usePlans();
  const subscription = useSubscription();
  const pricing = usePricing();
  const members = useMembers(workspace?.id, { enabled: can('members.manage') });
  const change = useChangePlan();
  const [target, setTarget] = useState<Plan | null>(null);

  const current = subscription.data?.plan ?? null;
  const renewsAt = subscription.data?.renews_at ?? null;
  const pending = subscription.data?.pending_plan ?? null;
  const base = pricing.data ? estimateCost({ durationSeconds: 1800, resolution: '720p' }, pricing.data, null).total : null;
  const mine = pricing.data && current ? estimateCost({ durationSeconds: 1800, resolution: '720p' }, pricing.data, current).total : null;

  const upgrade = target && current ? target.sort_order > current.sort_order : false;
  const losses = target && current && !upgrade ? planLosses(current, target) : [];
  const seats = members.data?.seats_used ?? 0;
  const overSeats = target && !upgrade && seats > target.max_members;
  const when = renewsAt ? formatDate(renewsAt) : null;

  const close = () => {
    if (change.isPending) return;
    setTarget(null);
    change.reset();
  };
  const confirm = () => {
    if (!target) return;
    change.mutate(target.id, {
      onSuccess: (result) => {
        setTarget(null);
        if (result.checkout_url) {
          leaveTo(result.checkout_url);
          return;
        }
        toast.success(
          result.applied === 'now'
            ? `Listo: ya estás en el plan ${result.subscription.plan.name}.`
            : `Tu plan pasa a ${target.name} el ${formatDate(result.subscription.pending_plan?.effective_at ?? renewsAt ?? new Date())}.`,
        );
      },
    });
  };

  return (
    <>
      <h2 className="sr-only">Planes</h2>
      {base !== null ? (
        <div className={styles.strip} role="note">
          <span className={styles.stripIco}>
            <Icon name="info" />
          </span>
          <p className="t-small">
            Un video de <span className="num">30 min</span> a 720p cuesta <span className="num">≈ {formatNumber(base)} tokens</span>
            {current && mine !== null && current.base_discount_rate > 0 ? (
              <>
                ; con tu plan {current.name}, <span className={cx('num', styles.strong)}>{formatNumber(mine)}</span> por el{' '}
                {formatPercent(current.base_discount_rate)} de descuento
              </>
            ) : null}
            . <span className="muted">Los tokens se compran aparte y no vencen con el plan.</span>
          </p>
          {admin ? (
            <Link className={cx(styles.link, 't-small', styles.nowrap)} href="/uso/comprar">
              Comprar tokens
            </Link>
          ) : null}
        </div>
      ) : null}

      {pending ? (
        <Banner tone="info" className={styles.notice} title={`Tu plan pasa a ${pending.name} el ${formatDate(pending.effective_at)}`}>
          Hasta ese día seguís con {current?.name ?? 'tu plan'} y todo lo que incluye. Si elegís un plan más alto, el cambio se aplica al momento.
        </Banner>
      ) : null}
      {plans.isError ? <LoadError error={plans.error} onRetry={() => void plans.refetch()} className={styles.notice} fullPageForMissing={false} /> : null}
      {subscription.isError ? (
        <LoadError error={subscription.error} onRetry={() => void subscription.refetch()} className={styles.notice} fullPageForMissing={false} />
      ) : null}

      {plans.data && current ? (
        <section className={styles.plans} aria-label="Planes">
          {plans.data.map((plan) => {
            const isCurrent = plan.id === current.id;
            const isUpgrade = plan.sort_order > current.sort_order;
            const isPending = pending?.id === plan.id;
            return (
              <PlanCard
                key={plan.id}
                plan={plan}
                plans={plans.data}
                current={isCurrent}
                recommended={plan.is_recommended}
                badge={
                  isCurrent ? (
                    <Badge tone="outline">Tu plan</Badge>
                  ) : isPending ? (
                    <Badge tone="info" dot>
                      Próximo plan
                    </Badge>
                  ) : plan.is_recommended ? (
                    <Badge tone="accent">Recomendado</Badge>
                  ) : undefined
                }
                cta={
                  isCurrent ? (
                    <>
                      <p className={styles.ctaTxt}>
                        <span className={styles.strong}>Tu plan</span>
                        {when ? (
                          <>
                            {' '}
                            · {pending ? 'termina' : 'se renueva'} el <span className="num">{when}</span>
                          </>
                        ) : (
                          ' · no se renueva ni vence'
                        )}
                      </p>
                      <p className={styles.ctaNote}>
                        {plan.base_discount_rate > 0 ? `Incluye ${formatPercent(plan.base_discount_rate)} de descuento en tokens.` : 'Los tokens se pagan aparte.'}
                      </p>
                    </>
                  ) : isUpgrade ? (
                    <>
                      <Button variant="primary" block onClick={() => setTarget(plan)} {...gate('billing.manage')}>
                        Subir a {plan.name}
                      </Button>
                      <p className={cx(styles.ctaNote, styles.ctaCenter)}>Se aplica al momento.</p>
                    </>
                  ) : isPending ? (
                    <>
                      <p className={styles.ctaTxt}>Ya está programado</p>
                      <p className={styles.ctaNote}>
                        Desde el <span className="num">{formatDate(pending.effective_at)}</span>.
                      </p>
                    </>
                  ) : (
                    <>
                      <Button variant="ghost" className={styles.down} onClick={() => setTarget(plan)} {...gate('billing.manage')}>
                        Bajar a {plan.name}
                      </Button>
                      <p className={styles.ctaNote}>
                        {when ? (
                          <>
                            Desde el <span className="num">{when}</span>, al fin del período.
                          </>
                        ) : (
                          'Al fin del período.'
                        )}
                      </p>
                    </>
                  )
                }
              />
            );
          })}
        </section>
      ) : plans.isError || subscription.isError ? null : (
        <PlanCardsSkeleton />
      )}
      <p className="t-small subtle" style={{ marginTop: 16 }}>
        Subir de plan se aplica al momento. Bajar se aplica al fin del período
        {when ? (
          <>
            , el <span className="num">{when}</span>
          </>
        ) : null}
        ; antes de confirmar te mostramos qué vas a perder. En las filas con candado, a la derecha está el plan que lo habilita.
      </p>

      <ConfirmDialog
        open={Boolean(target)}
        onClose={close}
        onConfirm={confirm}
        loading={change.isPending}
        title={target ? (upgrade ? `¿Subir a ${target.name}?` : `¿Bajar a ${target.name}?`) : ''}
        description={
          target
            ? upgrade
              ? `El cambio se aplica al momento y el plan pasa a costar ${formatUsd(target.price_usd_monthly)} por mes.`
              : `El cambio se aplica ${when ? `el ${when}, ` : ''}al fin del período. Hasta entonces seguís con ${current?.name ?? 'tu plan'}.`
            : undefined
        }
        confirmLabel={target ? (upgrade ? `Subir a ${target.name}` : `Bajar a ${target.name}`) : 'Confirmar'}
        error={change.isError ? errorMessage(change.error, 'No pudimos cambiar el plan.') : undefined}
      >
        {target && !upgrade && losses.length > 0 ? (
          <div>
            <p className="t-label muted" style={{ marginBottom: 8 }}>
              Con {target.name} vas a perder
            </p>
            <ul className={styles.loseList}>
              {losses.map((loss) => (
                <li key={loss}>{loss}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {target && overSeats ? (
          <Banner tone="warn" title={`Hoy ocupás ${seats} lugares y ${target.name} tiene ${target.max_members}`}>
            Antes del cambio quitá miembros o invitaciones en{' '}
            <Link className={styles.link} href="/ajustes/miembros">
              Ajustes › Miembros
            </Link>
            . Si no, los que sobren van a quedar sin acceso.
          </Banner>
        ) : null}
        {target && upgrade ? (
          <p className="t-small muted">
            {target.base_discount_rate > 0 ? `Incluye ${formatPercent(target.base_discount_rate)} de descuento en tokens, ` : 'Incluye '}
            videos de hasta {Math.round(target.max_video_duration_seconds / 60)} min, resolución hasta {target.max_resolution} y{' '}
            {target.max_members === 1 ? '1 miembro' : `${target.max_members} miembros`}.
          </p>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
