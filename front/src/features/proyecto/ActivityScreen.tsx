'use client';

import Link from 'next/link';
import { useEffect, useRef } from 'react';
import { useProjectActivity, usePricing } from '@/api/hooks';
import { formatTime, plural } from '@/lib/format';
import { LoadError } from '@/shell/LoadError';
import { usePermissions, usePlan } from '@/shell/session';
import { Avatar, Badge, Button, Card, CardFoot, CardHead, DateTime, EmptyState, Icon, NetworkChip, Skeleton, Table, Tokens } from '@/ui';
import styles from './ActivityScreen.module.css';
import { MOVEMENT_KIND, activityLink, explainCost, groupActivityByDay, tokenMovements } from './model';
import { useCurrentProject } from './project-context';

function ActivitySkeleton() {
  return (
    <div className={styles.cols} aria-busy="true" aria-label="Cargando la actividad">
      <div className={styles.main}>
        <Card>
          <CardHead title="Actividad" />
          <div className={styles.skeleton}>
            {Array.from({ length: 5 }, (_, index) => (
              <div key={index} className={styles.skelRow}>
                <Skeleton width={36} height={14} />
                <Skeleton width={24} height={24} pill />
                <Skeleton width={`${70 - index * 8}%`} height={14} />
              </div>
            ))}
          </div>
        </Card>
      </div>
      <div className={styles.side}>
        <Skeleton width="100%" height={220} radius={12} />
      </div>
    </div>
  );
}

/** D1f · Actividad: who did what, the project's token movements and how its cost was calculated. */
export function ActivityScreen({ projectId }: { projectId: string }) {
  const project = useCurrentProject();
  const activity = useProjectActivity(projectId);
  const pricing = usePricing();
  const { plan } = usePlan();
  const { gate } = usePermissions();
  // What happens to the project (a stage ends, a render finishes, a clip is retried) adds activity:
  // read it again when the project's state changes, instead of waiting for the cache to expire.
  const stamp = project ? `${project.status}:${project.pipeline_stage}:${project.clips.map((clip) => clip.status).join(',')}` : '';
  const seenStamp = useRef(stamp);
  const { refetch } = activity;
  useEffect(() => {
    if (seenStamp.current === stamp) return;
    const first = seenStamp.current === '';
    seenStamp.current = stamp;
    if (!first) void refetch();
  }, [stamp, refetch]);
  if (!project) return null;
  if (activity.isLoading) return <ActivitySkeleton />;
  if (activity.isError && !activity.data) return <LoadError error={activity.error} onRetry={() => void activity.refetch()} fullPageForMissing={false} />;

  const items = activity.data ?? [];
  const days = groupActivityByDay(items);
  const movements = tokenMovements(items);
  const cost = explainCost(project, pricing.data, plan);
  const refunds = movements.filter((item) => item.type === 'tokens_refunded').length;

  return (
    <div className={styles.cols}>
      <div className={styles.main}>
        {activity.isError ? <LoadError error={activity.error} onRetry={() => void activity.refetch()} fullPageForMissing={false} /> : null}
        {items.length === 0 ? (
          <EmptyState icon="activity" title="Todavía no hay actividad" description="Acá vas a ver quién hizo qué en este proyecto y sus movimientos de tokens." />
        ) : (
          <Card as="section" aria-labelledby="actividad-h">
            <CardHead>
              <h2 className="t-h3" id="actividad-h">
                Actividad
              </h2>
              <span className="t-small muted">Quién hizo qué, de lo más nuevo a lo más viejo</span>
            </CardHead>
            {days.map((day) => (
              <div key={day.key} className={styles.dayBlock}>
                <h3 className={styles.day}>
                  <span className="num">{day.label}</span>
                  <span className="num subtle">{day.items.length}</span>
                </h3>
                <ol className={styles.events}>
                  {day.items.map((item) => {
                    const link = activityLink(item, project);
                    const movement = MOVEMENT_KIND[item.type];
                    return (
                      <li key={item.id} className={styles.event}>
                        <span className={`${styles.time} num`}>{formatTime(item.created_at)}</span>
                        {item.actor ? (
                          <Avatar name={item.actor.name} seed={item.actor.id} size="sm" />
                        ) : (
                          <span className={styles.system} aria-hidden="true">
                            <Icon name="sparkles" size={12} />
                          </span>
                        )}
                        <div className={styles.eventBody}>
                          <p>
                            <b>{item.actor?.name ?? 'OneCreator'}</b> · {item.description}
                            {item.platform ? (
                              <>
                                {' '}
                                en <NetworkChip platform={item.platform} size="sm" short className={styles.net} />
                              </>
                            ) : null}
                          </p>
                          {movement && item.amount !== null ? (
                            <p className={styles.sub}>
                              <Badge tone={movement.tone} size="sm" dot>
                                {movement.label}
                              </Badge>{' '}
                              <Tokens value={item.amount} signed />
                            </p>
                          ) : null}
                        </div>
                        {link ? (
                          <Link className={`${styles.arrowLink} t-small`} href={link.href}>
                            {link.label}
                            <Icon name="chevron-right" size={14} />
                          </Link>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              </div>
            ))}
          </Card>
        )}

        <Card as="section" aria-labelledby="movimientos-h">
          <CardHead>
            <h2 className="t-h3" id="movimientos-h">
              Movimientos de tokens del proyecto
            </h2>
            <span className="t-small muted num">{plural(movements.length, 'movimiento', 'movimientos')}</span>
          </CardHead>
          {movements.length === 0 ? (
            <p className={`${styles.none} t-small muted`}>Este proyecto todavía no movió tokens.</p>
          ) : (
            <Table caption="Movimientos de tokens del proyecto" className={styles.table}>
              <thead>
                <tr>
                  <th style={{ width: 176 }}>Fecha</th>
                  <th>Concepto</th>
                  <th style={{ width: 150 }}>Tipo</th>
                  <th style={{ width: 150 }} className="right">
                    Monto (tokens)
                  </th>
                </tr>
              </thead>
              <tbody>
                {movements.map((item) => {
                  const kind = MOVEMENT_KIND[item.type];
                  return (
                    <tr key={item.id}>
                      <td>
                        <DateTime value={item.created_at} className="t-small muted" />
                      </td>
                      <td>
                        <div className={styles.cell2}>
                          <span>{item.description}</span>
                          {item.actor ? <span className="t-small muted">{item.actor.name}</span> : null}
                        </div>
                      </td>
                      <td>
                        {kind ? (
                          <Badge tone={kind.tone} dot title={kind.hint}>
                            {kind.label}
                          </Badge>
                        ) : null}
                      </td>
                      <td className="right">
                        <Tokens value={item.amount ?? 0} unit={false} signed className={styles.amount} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
          <CardFoot>
            <span className="t-small muted">{refunds > 0 ? `${plural(refunds, 'devolución', 'devoluciones')} en este proyecto.` : 'Sin devoluciones.'}</span>
            <Button size="sm" variant="ghost" iconRight="chevron-right" href="/uso/movimientos" {...gate('billing.manage')}>
              Ver todos los movimientos del workspace
            </Button>
          </CardFoot>
        </Card>
      </div>

      <div className={styles.side}>
        <Card as="section" aria-labelledby="calculo-h">
          <CardHead>
            <h2 className="t-h3" id="calculo-h">
              Cómo se calculó
            </h2>
          </CardHead>
          {cost ? (
            <dl className={styles.calc}>
              {cost.lines.map((line) => (
                <div key={line.key}>
                  <dt>
                    <span className="num">{line.label}</span>
                    {line.note ? <small className="num">{line.note}</small> : null}
                  </dt>
                  <dd className="num">{line.value}</dd>
                </div>
              ))}
              <div className={styles.sum}>
                <dt>{cost.totalLabel}</dt>
                <dd className="num">{cost.total}</dd>
              </div>
            </dl>
          ) : (
            <p className={`${styles.none} t-small muted`}>Este proyecto no tiene un costo asociado.</p>
          )}
          {cost && pricing.isLoading ? (
            <p className={`${styles.none} t-small muted`} aria-busy="true">
              Cargando el detalle…
            </p>
          ) : null}
          {cost && cost.estimate && Math.abs(cost.estimate.total - (project.cost?.amount ?? 0)) > 0.01 ? (
            <p className={`${styles.calcNote} t-small muted`}>El total es lo que se reservó al crear el proyecto, con los precios y el plan de ese momento.</p>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
