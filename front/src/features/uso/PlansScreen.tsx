'use client';

import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import type { Plan } from '@/api/contracts/billing';
import { useChangePlan, useMembers, usePlans, usePricing, useSubscription, useTokenPurchase } from '@/api/hooks';
import { keys } from '@/api/query';
import { ConfirmDialog } from '@/dialogs';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { formatDate, formatNumber, formatPercent, formatUsd } from '@/lib/format';
import { estimateCost } from '@/lib/pricing';
import { LoadError } from '@/shell/LoadError';
import { RequireRole } from '@/shell/RequireSession';
import { usePermissions, useSession } from '@/shell/session';
import { Badge, Banner, Button, Dialog, Icon, IconButton, Spinner, useToast } from '@/ui';
import { leaveTo } from './leave';
import { PlanCard, PlanCardsSkeleton, planLosses } from './PlanCard';
import styles from './uso.module.css';

/**
 * The way back from the checkout of a plan upgrade (`?compra=<id>`): a line while the provider has
 * not answered, then whether the plan changed or the payment did not go through.
 */
function UpgradeNotice({ currentName, plans }: { currentName: string | null; plans: ReadonlyArray<Plan> }) {
  const search = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const id = search.get('compra');
  const payment = useTokenPurchase(id);
  const refreshed = useRef<string | null>(null);
  const status = payment.data?.status;
  useEffect(() => {
    if (!id || status !== 'approved' || refreshed.current === id) return;
    refreshed.current = id;
    void queryClient.invalidateQueries({ queryKey: keys.billing.all });
    void queryClient.invalidateQueries({ queryKey: keys.me });
  }, [id, queryClient, status]);
  if (!id) return null;
  const close = <IconButton icon="close" label="Cerrar aviso" size="sm" onClick={() => router.replace('/uso/planes')} />;
  if (payment.isError) return <LoadError error={payment.error} onRetry={() => void payment.refetch()} className={styles.notice} fullPageForMissing={false} />;
  if (!payment.data || payment.data.status === 'pending') {
    return (
      <div className={styles.strip} role="status" aria-busy="true">
        <Spinner />
        <p className="t-small">
          <span className={styles.strong}>Estamos confirmando el pago del plan.</span>{' '}
          <span className="muted">Suele tardar unos segundos. El plan cambia solo cuando se confirma.</span>
        </p>
      </div>
    );
  }
  if (payment.data.status === 'approved') {
    // Named from the payment itself: the subscription on screen may still be the one from before paying.
    const paidName = plans.find((plan) => plan.id === payment.data.plan_id)?.name ?? null;
    return (
      <Banner tone="ok" className={styles.notice} title={`Listo: ya estás en el plan ${paidName ?? 'nuevo'}`} action={close}>
        El cambio ya está aplicado para todo el workspace.
      </Banner>
    );
  }
  return (
    <Banner tone="err" className={styles.notice} title="El pago no se completó. No se te cobró nada." action={close}>
      {payment.data.failure_reason ? `${payment.data.failure_reason} ` : ''}
      {currentName ? `Seguís con el plan ${currentName}.` : 'Tu plan no cambió.'} Podés intentarlo de nuevo cuando quieras.
    </Banner>
  );
}

/**
 * G3 · Uso › Planes: the cards of Precios with the current plan marked.
 * Going up applies right away (or after the checkout when it needs a payment);
 * going down waits for the end of the period and shows what is lost before
 * confirming, and a scheduled change can be cancelled. Only Dueño and Admin
 * open it, like the locked tab says (R1); the rest get K2.
 */
export function PlansScreen() {
  return (
    <RequireRole action="billing.manage">
      <Plans />
    </RequireRole>
  );
}

function Plans() {
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
  // The backend rejects (409) a downgrade while the seats in use exceed the target plan.
  const overSeats = Boolean(target && !upgrade && seats > target.max_members);
  const when = renewsAt ? formatDate(renewsAt) : null;

  const close = () => {
    if (change.isPending) return;
    setTarget(null);
    change.reset();
  };
  const confirm = () => {
    if (!target) return;
    change.mutate(
      { planId: target.id, returnUrl: `${window.location.origin}/uso/planes` },
      {
        onSuccess: (result) => {
          setTarget(null);
          if (result.checkout_url) {
            // The upgrade needs a payment: the plan changes when the provider confirms it (UpgradeNotice).
            leaveTo(result.checkout_url);
            return;
          }
          toast.success(
            result.applied === 'now'
              ? `Listo: ya estás en el plan ${result.subscription.plan.name}.`
              : `Tu plan pasa a ${target.name} el ${formatDate(result.subscription.pending_plan?.effective_at ?? renewsAt ?? new Date())}.`,
          );
        },
      },
    );
  };
  /** Choosing the current plan again cancels the scheduled change. */
  const keepCurrent = () => {
    if (!current) return;
    change.mutate(
      { planId: current.id },
      {
        onSuccess: () => toast.success(`Listo: seguís con el plan ${current.name}.`),
        onError: (error) => toast.error(errorMessage(error, 'No pudimos cancelar el cambio')),
      },
    );
  };

  return (
    <>
      <h2 className="sr-only">Planes</h2>
      <UpgradeNotice currentName={current?.name ?? null} plans={plans.data ?? []} />
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
        <Banner
          tone="info"
          className={styles.notice}
          title={`Tu plan pasa a ${pending.name} el ${formatDate(pending.effective_at)}`}
          action={
            <Button size="sm" variant="secondary" onClick={keepCurrent} loading={change.isPending && !target} {...gate('billing.manage')}>
              Seguir con {current?.name ?? 'mi plan'}
            </Button>
          }
        >
          Hasta ese día seguís con {current?.name ?? 'tu plan'} y todo lo que incluye. Podés cancelar el cambio hasta entonces.
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

      <Dialog
        open={Boolean(target) && Boolean(overSeats)}
        onClose={close}
        role="alertdialog"
        icon="warning"
        iconTone="warn"
        title={target ? `Todavía no podés bajar a ${target.name}` : ''}
        description={
          target
            ? `Hoy ocupás ${seats} lugares, contando las invitaciones pendientes, y ${target.name} tiene ${target.max_members}. Quitá miembros o invitaciones y volvé a intentarlo: no se programa ningún cambio mientras sobren.`
            : undefined
        }
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Cancelar
            </Button>
            <Button variant="primary" href="/ajustes/miembros" data-autofocus>
              Ir a Miembros
            </Button>
          </>
        }
      />
      <ConfirmDialog
        open={Boolean(target) && !overSeats}
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
