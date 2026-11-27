'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { Subscription, Wallet } from '@/api/contracts/billing';
import { usePricing, useSubscription, useTransactions, useUsage, useWallet, useWorkspace } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { formatDate, formatMinutes, formatNumber, formatPercent, formatUsd, plural } from '@/lib/format';
import { ROLE_LABEL } from '@/lib/permissions';
import { secondsForTokens } from '@/lib/pricing';
import { LoadError } from '@/shell/LoadError';
import { usePermissions, useSession } from '@/shell/session';
import { Avatar, Badge, Banner, Button, Card, Icon, Skeleton, SkeletonText } from '@/ui';
import { KindBadge, MovementAmount, movementConcept, movementDate, totalOf } from './movements';
import { monthKey, monthName, monthTitle, shiftMonth, workspaceDay } from './period';
import { UsageChart, usageTotals } from './UsageChart';
import styles from './uso.module.css';

/** "24 nov, 14:21" */
function shortStamp(value: string): string {
  const date = new Date(value);
  const [day = '', month = ''] = formatDate(date).split(' ');
  return `${day} ${month}, ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function KpiNumber({ big, value, unit }: { big?: boolean; value: ReactNode; unit: ReactNode }) {
  return (
    <p className={cx(styles.kpiNum, big && styles.kpiBig)}>
      <span className="num">{value}</span>
      <span className={cx('num', styles.unit)}>{unit}</span>
    </p>
  );
}

function CardSkeleton({ wide = false }: { wide?: boolean }) {
  return (
    <Card padded className={cx(styles.kpi, wide && styles.hero)} aria-busy="true">
      <span className="sr-only">Cargando</span>
      <Skeleton width={120} height={12} />
      <Skeleton width={wide ? 220 : 140} height={wide ? 40 : 28} />
      <SkeletonText lines={2} />
    </Card>
  );
}

/** Reservations grouped by project: a project with a render in flight has more than one. */
function reservationsByProject(wallet: Wallet) {
  const rows = new Map<string, { key: string; projectId: string | null; title: string; amount: number }>();
  for (const item of wallet.reservations) {
    const key = item.project_id ?? item.transaction_id;
    const row = rows.get(key) ?? { key, projectId: item.project_id, title: item.project_title ?? 'Trabajo en curso', amount: 0 };
    row.amount += item.amount;
    rows.set(key, row);
  }
  return [...rows.values()].sort((a, b) => b.amount - a.amount);
}

function planLine(subscription: Subscription): ReactNode {
  const { plan, renews_at: renewsAt, pending_plan: pending } = subscription;
  const discount = plan.base_discount_rate > 0 ? ` Incluye ${formatPercent(plan.base_discount_rate)} de descuento en tokens.` : '';
  if (pending) {
    return (
      <>
        Pasa a {pending.name} el <span className="num">{formatDate(pending.effective_at)}</span>. Hasta entonces seguís con {plan.name}.
      </>
    );
  }
  if (!renewsAt) return <>No se renueva ni vence.{discount || ' Los tokens se pagan aparte.'}</>;
  return (
    <>
      Vence el <span className="num">{formatDate(renewsAt)}</span>.{discount}
    </>
  );
}

/**
 * G1 · Uso › Resumen (and R1, the same screen for Editor and Lector: only the
 * balance and what is reserved, with who to ask for more).
 */
export function UsageOverview({ notice }: { notice?: ReactNode }) {
  const { workspace } = useSession();
  const { can, gate, role } = usePermissions();
  const admin = can('billing.manage');
  // The month depends on the clock (read in the workspace's time zone): decided after mount so the server markup matches.
  const [today, setToday] = useState<Date | null>(null);
  useEffect(() => setToday(new Date()), []);
  const month = today ? monthKey(today) : undefined;
  const previousMonth = month ? shiftMonth(month, -1) : undefined;

  const wallet = useWallet();
  const subscription = useSubscription();
  const pricing = usePricing();
  const usage = useUsage(month, { enabled: admin && Boolean(month) });
  const previousUsage = useUsage(previousMonth, { enabled: admin && Boolean(previousMonth) });
  const latest = useTransactions({ page_size: 7 }, { enabled: admin });
  const monthMovements = useTransactions({ month, page_size: 1 }, { enabled: admin && Boolean(month) });
  const detail = useWorkspace(admin ? null : workspace?.id);

  const plan = subscription.data?.plan ?? null;
  // Only who can open Planes (R1: Editor and Lector see the balance and nothing about the plan).
  const paying = admin ? (subscription.data?.pending_payment ?? null) : null;
  const reservations = useMemo(() => (wallet.data ? reservationsByProject(wallet.data) : []), [wallet.data]);
  const totals = usage.data ? usageTotals(usage.data) : null;
  const previousTotal = previousUsage.data ? usageTotals(previousUsage.data).total : null;
  const charged = totalOf(monthMovements.data?.totals, 'charged');
  const refunded = totalOf(monthMovements.data?.totals, 'refund');

  const available = wallet.data?.available_balance ?? 0;
  const seconds = wallet.data && pricing.data ? secondsForTokens(available, pricing.data, plan) : null;
  const low = wallet.data ? available < wallet.data.low_balance_threshold : false;
  const discountText = plan && plan.base_discount_rate > 0 ? formatPercent(plan.base_discount_rate) : null;

  return (
    <>
      {notice}
      {low && wallet.data ? (
        <Banner
          tone="warn"
          className={styles.notice}
          title="Te queda poco saldo"
          action={
            <Button size="sm" variant="primary" href="/uso/comprar" {...gate('billing.manage')}>
              Comprar tokens
            </Button>
          }
        >
          Tenés <span className="num">{formatNumber(available)}</span> tokens y un video promedio cuesta{' '}
          <span className="num">≈ {formatNumber(wallet.data.low_balance_threshold, 0)}</span>.
          {admin ? '' : ' Pedile a un admin que compre más.'}
        </Banner>
      ) : null}
      {paying ? (
        <div className={cx(styles.strip, styles.notice)} role="note">
          <span className={styles.stripIco}>
            <Icon name="info" />
          </span>
          <p className="t-small">Tenés un pago pendiente para pasar al plan {paying.plan_name}.</p>
          <Link className={cx(styles.link, 't-small', styles.nowrap)} href="/uso/planes">
            Ver en Planes
          </Link>
        </div>
      ) : null}
      {wallet.isError ? <LoadError error={wallet.error} onRetry={() => void wallet.refetch()} className={styles.notice} fullPageForMissing={false} /> : null}

      <section className={styles.kpis} aria-label={admin ? 'Resumen de tokens y plan' : 'Saldo de tokens'}>
        {wallet.data ? (
          <Card padded className={cx(styles.kpi, styles.hero)}>
            <div className={styles.heroMain}>
              <span className="t-label muted">Saldo disponible</span>
              <KpiNumber big value={formatNumber(available)} unit={available === 1 ? 'token' : 'tokens'} />
              <p className="t-small muted">
                {seconds !== null && seconds >= 60 ? (
                  <>
                    {admin ? 'Te alcanza' : 'Alcanza'} para <span className={cx('num', styles.fg)}>≈ {formatMinutes(seconds)}</span> de video a 720p
                    {discountText ? `, con el ${discountText} de descuento ${admin ? 'de tu plan' : `del plan ${plan?.name ?? ''}`}` : ''}.
                  </>
                ) : seconds !== null ? (
                  'No alcanza para un minuto de video a 720p.'
                ) : (
                  'Los usa todo el workspace y no vencen.'
                )}
              </p>
              {/* The wrapper keeps the button at the bottom also when a tooltip wraps it (disabled by role). */}
              <div className={styles.bottom}>
                <Button variant="primary" iconLeft="plus" href="/uso/comprar" {...gate('billing.manage')}>
                  Comprar tokens
                </Button>
              </div>
            </div>
            <div className={styles.heroSide}>
              <span className="t-label muted">Reservado</span>
              <KpiNumber value={formatNumber(wallet.data.reserved_balance)} unit="tokens" />
              {!admin ? (
                <p className="t-small muted">
                  Apartado para los trabajos en curso del workspace. Se cobra cuando termine cada uno; si alguno falla, vuelve al saldo.
                </p>
              ) : reservations.length > 0 ? (
                <>
                  <ul className={styles.holds} aria-label="Reservas por proyecto">
                    {reservations.map((item) => (
                      <li key={item.key}>
                        {item.projectId ? (
                          <Link className={styles.link} href={`/proyectos/${item.projectId}`}>
                            {item.title}
                          </Link>
                        ) : (
                          <span className={styles.what}>{item.title}</span>
                        )}
                        <span className={cx('num', styles.n)}>{formatNumber(item.amount)}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="t-small subtle">Se cobra cuando termine cada trabajo. Si alguno falla, vuelve a tu saldo.</p>
                </>
              ) : (
                <p className="t-small subtle">No hay trabajos en curso. Lo que confirmes queda reservado acá hasta que termine.</p>
              )}
            </div>
          </Card>
        ) : wallet.isError ? null : (
          <CardSkeleton wide />
        )}

        {admin ? (
          <>
            {wallet.data ? (
              <Card padded className={styles.kpi} as="section" aria-label="Gastado este mes">
                <span className="t-label muted">Gastado este mes</span>
                <KpiNumber value={formatNumber(wallet.data.spent_this_month)} unit="tokens cobrados" />
                <p className="t-small muted">
                  {today && month ? (
                    charged.count > 0 ? (
                      <>
                        Del 1 al {Number(workspaceDay(today).slice(8))} de {monthName(month)}, en {plural(charged.count, 'cobro', 'cobros')}.
                        {refunded.amount > 0 ? (
                          <>
                            {' '}
                            Te devolvimos <span className="num">{formatNumber(refunded.amount)}</span>: neto{' '}
                            <span className={cx('num', styles.fg)}>{formatNumber(wallet.data.spent_this_month - refunded.amount)}</span>.
                          </>
                        ) : null}
                      </>
                    ) : (
                      `Todavía no hubo cobros en ${monthName(month)}.`
                    )
                  ) : null}
                </p>
                {totals ? (
                  <ul className={styles.types} aria-label="Total del mes por tipo">
                    <li>
                      Proyectos <span className={cx('num', styles.n)}>{formatNumber(totals.projects)}</span>
                    </li>
                    <li>
                      Renders <span className={cx('num', styles.n)}>{formatNumber(totals.renders)}</span>
                    </li>
                    <li>
                      SEO <span className={cx('num', styles.n)}>{formatNumber(totals.seo)}</span>
                    </li>
                  </ul>
                ) : null}
                {previousMonth && previousTotal !== null ? (
                  <p className="t-small subtle" style={{ marginTop: 'auto' }}>
                    {monthName(previousMonth).replace(/^./, (letter) => letter.toUpperCase())} completo:{' '}
                    <span className="num">{formatNumber(previousTotal)}</span>
                  </p>
                ) : null}
              </Card>
            ) : wallet.isError ? null : (
              <CardSkeleton />
            )}

            {subscription.data ? (
              <Card padded className={styles.kpi} as="section" aria-label="Plan actual">
                <span className="t-label muted">Plan actual</span>
                <p className={styles.kpiNum}>
                  <span>{subscription.data.plan.name}</span>
                  <span className={cx('num', styles.unit)}>{formatUsd(subscription.data.plan.price_usd_monthly)}/mes</span>
                </p>
                <p className="t-small muted">{planLine(subscription.data)}</p>
                <Button variant="secondary" size="sm" href="/uso/planes" className={styles.bottom}>
                  Cambiar plan
                </Button>
              </Card>
            ) : subscription.isError ? (
              <Card padded className={styles.kpi}>
                <LoadError error={subscription.error} onRetry={() => void subscription.refetch()} fullPageForMissing={false} />
              </Card>
            ) : (
              <CardSkeleton />
            )}
          </>
        ) : (
          <Card padded as="aside" className={cx(styles.kpi, styles.roleNote)} aria-labelledby="uso-rol-nota">
            <span className={styles.roleIco}>
              <Icon name="lock" />
            </span>
            <p id="uso-rol-nota" className={styles.strong}>
              Los movimientos y los planes los ven los dueños y admins del workspace.
            </p>
            <p className="t-small muted">
              Como {role ? ROLE_LABEL[role] : 'miembro'} ves el saldo y lo reservado. Si necesitás más tokens o el detalle de un cobro, pedíselo a:
            </p>
            {detail.data?.admins && detail.data.admins.length > 0 ? (
              <ul className={styles.admins}>
                {detail.data.admins.map((person) => (
                  <li key={person.id}>
                    <Avatar name={person.name} seed={person.id} size="sm" />
                    <span>{person.name}</span>
                    <Badge tone="outline" size="sm">
                      {ROLE_LABEL[person.role]}
                    </Badge>
                  </li>
                ))}
              </ul>
            ) : detail.isLoading ? (
              <div className={styles.admins} aria-busy="true">
                <Skeleton width="70%" />
                <Skeleton width="55%" />
              </div>
            ) : (
              <p className={cx('t-small muted', styles.admins)}>Un dueño o admin del workspace.</p>
            )}
          </Card>
        )}
      </section>

      {admin ? (
        <div className={styles.row2}>
          <Card as="section" className={styles.chartCard} aria-labelledby="uso-consumo">
            <div className={styles.cardHead}>
              <div>
                <h2 className="t-h3" id="uso-consumo">
                  Consumo por día
                </h2>
                <p className="t-small muted">{month ? `${monthTitle(month)} · tokens cobrados` : 'Tokens cobrados'}</p>
              </div>
            </div>
            <div className={styles.cardBody}>
              {usage.data && month && today ? (
                <>
                  <UsageChart month={month} days={usage.data} today={workspaceDay(today)} />
                  <p className="t-small subtle">
                    {totals && totals.total > 0
                      ? 'Lo reservado no se cuenta acá y las devoluciones no se restan.'
                      : 'Todavía no hubo consumo este mes. Acá vas a ver lo que se cobra cada día por proyectos, renders y SEO.'}
                  </p>
                </>
              ) : usage.isError ? (
                <LoadError error={usage.error} onRetry={() => void usage.refetch()} fullPageForMissing={false} />
              ) : (
                <div aria-busy="true">
                  <span className="sr-only">Cargando el consumo</span>
                  <Skeleton height={220} radius={6} />
                </div>
              )}
            </div>
          </Card>

          <Card as="section" className={styles.movCard} aria-labelledby="uso-ultimos">
            <div className={styles.cardHead}>
              <h2 className="t-h3" id="uso-ultimos">
                Últimos movimientos
              </h2>
              <Button variant="ghost" size="sm" href="/uso/movimientos" iconRight="chevron-right">
                Ver todos
              </Button>
            </div>
            {latest.data ? (
              latest.data.results.length > 0 ? (
                <div className={styles.mrows}>
                  {latest.data.results.map((item) => (
                    <div key={item.id} className={styles.mrow}>
                      <span className={cx('num subtle t-small', styles.mdate)}>{shortStamp(movementDate(item))}</span>
                      <span className={styles.mwhat}>
                        {item.project ? (
                          <Link className={styles.link} href={`/proyectos/${item.project.id}`}>
                            {item.project.title}
                          </Link>
                        ) : (
                          movementConcept(item)
                        )}
                      </span>
                      <span className={styles.mkind}>
                        <KindBadge kind={item.kind} size="sm" />
                      </span>
                      <MovementAmount transaction={item} className={styles.mamt} />
                    </div>
                  ))}
                </div>
              ) : (
                <p className={cx('t-small muted', styles.emptyLine)}>Todavía no hay movimientos. Aparecen cuando creás un proyecto o comprás tokens.</p>
              )
            ) : latest.isError ? (
              <div className={styles.cardBody}>
                <LoadError error={latest.error} onRetry={() => void latest.refetch()} fullPageForMissing={false} />
              </div>
            ) : (
              <div className={styles.cardBody} aria-busy="true">
                <span className="sr-only">Cargando los movimientos</span>
                <SkeletonText lines={4} />
              </div>
            )}
          </Card>
        </div>
      ) : null}
    </>
  );
}
