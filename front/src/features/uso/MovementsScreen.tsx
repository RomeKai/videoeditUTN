'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import type { TransactionKind } from '@/api/contracts/billing';
import { DEFAULT_PAGE_SIZE } from '@/api/contracts/common';
import { useExportTransactions, useTransactions } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { downloadText } from '@/lib/download';
import { errorMessage } from '@/lib/error-messages';
import { formatDateTime, formatNumber, plural } from '@/lib/format';
import { LoadError } from '@/shell/LoadError';
import { RequireRole } from '@/shell/RequireSession';
import { Button, Card, EmptyState, FilterChips, Pagination, Select, Skeleton, Table, useToast, type FilterOption } from '@/ui';
import { KIND_META, KIND_ORDER, KindBadge, MINUS, MovementAmount, movementConcept, totalOf, type KindTotals } from './movements';
import { monthKey, monthLong, monthTitle, recentMonths } from './period';
import styles from './uso.module.css';

type KindFilter = TransactionKind | 'all';
const ALL_MONTHS = 'todos';

function countAll(totals: KindTotals | undefined): number {
  return KIND_ORDER.reduce((sum, kind) => sum + totalOf(totals, kind).count, 0);
}

function Totals({ totals, scope }: { totals: KindTotals | undefined; scope: string }) {
  const charged = totalOf(totals, 'charged');
  const reserved = totalOf(totals, 'reserved');
  const refund = totalOf(totals, 'refund');
  const released = totalOf(totals, 'released');
  const purchase = totalOf(totals, 'purchase');
  const pending = totalOf(totals, 'pending');
  const returned = refund.amount + released.amount;
  const returnedParts = [
    refund.count > 0 ? plural(refund.count, 'devolución', 'devoluciones') : null,
    released.count > 0 ? plural(released.count, 'reserva liberada', 'reservas liberadas') : null,
  ].filter(Boolean);
  return (
    <Card as="section" className={styles.totals} aria-label={`Totales de ${scope}`}>
      <div>
        <span className="t-label muted">Cobrado</span>
        <span className={cx('num', styles.tot)}>{charged.amount > 0 ? `${MINUS}${formatNumber(charged.amount)}` : '0'}</span>
        <span className="t-small subtle">{plural(charged.count, 'movimiento', 'movimientos')}</span>
      </div>
      <div>
        <span className="t-label muted">Reservado</span>
        <span className={cx('num', styles.tot, styles.muted)}>{reserved.amount > 0 ? `${MINUS}${formatNumber(reserved.amount)}` : '0'}</span>
        <span className="t-small subtle">
          {reserved.count > 0 ? `${plural(reserved.count, 'trabajo', 'trabajos')} en curso · todavía no se cobró` : 'Nada en curso'}
        </span>
      </div>
      <div>
        <span className="t-label muted">Devuelto</span>
        <span className={cx('num', styles.tot, returned > 0 && styles.pos)}>{returned > 0 ? `+${formatNumber(returned)}` : '0'}</span>
        <span className="t-small subtle">{returnedParts.length > 0 ? returnedParts.join(' y ') : 'Sin devoluciones'}</span>
      </div>
      <div>
        <span className="t-label muted">Comprado</span>
        <span className={cx('num', styles.tot, purchase.amount > 0 && styles.pos)}>{purchase.amount > 0 ? `+${formatNumber(purchase.amount)}` : '0'}</span>
        <span className="t-small subtle">
          {plural(purchase.count, 'compra', 'compras')}
          {pending.count > 0 ? (
            <>
              {' '}
              · {pending.count === 1 ? 'otra' : `otras ${pending.count}`} de <span className="num">{formatNumber(pending.amount)}</span>{' '}
              {pending.count === 1 ? 'pendiente' : 'pendientes'}
            </>
          ) : null}
        </span>
      </div>
    </Card>
  );
}

function Movements() {
  const toast = useToast();
  const [kind, setKind] = useState<KindFilter>('all');
  const [page, setPage] = useState(1);
  // The current month depends on the browser's clock: chosen after mount.
  const [month, setMonth] = useState<string | null>(null);
  useEffect(() => setMonth(monthKey()), []);
  const months = useMemo(() => (month ? recentMonths(6) : []), [month]);
  const monthFilter = month && month !== ALL_MONTHS ? month : undefined;

  const query = { month: monthFilter, kind: kind === 'all' ? undefined : kind, page, page_size: DEFAULT_PAGE_SIZE };
  const movements = useTransactions(query, { enabled: month !== null });
  const exporter = useExportTransactions();

  const totals = movements.data?.totals;
  // While the next month loads the previous rows stay on screen: the labels keep naming the month of
  // what is shown and change together with the data.
  const [shownMonth, setShownMonth] = useState<string | undefined>(undefined);
  const fresh = Boolean(movements.data) && !movements.isPlaceholderData;
  useEffect(() => {
    if (fresh) setShownMonth(monthFilter);
  }, [fresh, monthFilter]);
  const scopeMonth = fresh ? monthFilter : shownMonth;
  const scope = scopeMonth ? monthLong(scopeMonth) : 'todos los meses';
  const options: Array<FilterOption<KindFilter>> = [
    { value: 'all', label: 'Todos', count: totals ? countAll(totals) : undefined },
    ...KIND_ORDER.map((value) => ({ value, label: KIND_META[value].plural, count: totals ? totalOf(totals, value).count : undefined })),
  ];

  const exportCsv = () =>
    exporter.mutate(
      { month: monthFilter, kind: kind === 'all' ? undefined : kind },
      {
        onSuccess: (csv) => {
          downloadText(csv, `movimientos-${monthFilter ?? 'todos'}.csv`, 'text/csv;charset=utf-8');
          toast.success('Movimientos exportados');
        },
        onError: (error) => toast.error(errorMessage(error, 'No pudimos exportar los movimientos')),
      },
    );

  const rows = movements.data?.results ?? [];
  const loading = month === null || movements.isLoading;

  return (
    <>
      <h2 className="sr-only">Movimientos</h2>
      {movements.isError ? <LoadError error={movements.error} onRetry={() => void movements.refetch()} className={styles.notice} /> : null}
      {loading ? (
        <Card className={styles.totals} aria-busy="true">
          {[0, 1, 2, 3].map((item) => (
            <div key={item}>
              <Skeleton width={80} height={12} />
              <Skeleton width={120} height={28} />
              <Skeleton width={150} height={12} />
            </div>
          ))}
        </Card>
      ) : movements.data ? (
        <Totals totals={totals} scope={scope} />
      ) : null}

      <div className={styles.toolbar}>
        <FilterChips<KindFilter>
          ariaLabel="Filtrar por tipo"
          options={options}
          value={kind}
          onChange={(value) => {
            setKind(value);
            setPage(1);
          }}
        />
        <span className={styles.spacer} />
        <label>
          <span className="sr-only">Filtrar por mes</span>
          <Select
            className={styles.monthSel}
            value={month ?? ''}
            onChange={(event) => {
              setMonth(event.target.value);
              setPage(1);
            }}
          >
            {months.map((key) => (
              <option key={key} value={key}>
                {monthTitle(key)}
              </option>
            ))}
            <option value={ALL_MONTHS}>Todos los meses</option>
          </Select>
        </label>
        <Button variant="secondary" iconLeft="download" onClick={exportCsv} loading={exporter.isPending} disabled={!movements.data || countAll(totals) === 0}>
          Exportar CSV
        </Button>
      </div>

      {loading ? (
        <Card aria-busy="true" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
          <span className="sr-only">Cargando los movimientos</span>
          {[0, 1, 2, 3, 4, 5].map((item) => (
            <Skeleton key={item} height={20} />
          ))}
        </Card>
      ) : movements.data && rows.length === 0 ? (
        kind !== 'all' && countAll(totals) > 0 ? (
          <EmptyState
            icon="filter"
            title={`No hay ${KIND_META[kind].plural.toLowerCase()} en ${scope}`}
            action={
              <Button variant="secondary" onClick={() => setKind('all')}>
                Ver todos
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon="tokens"
            title={`No hay movimientos en ${scope}`}
            description="Acá aparece cada reserva, cobro, devolución y compra de tokens del workspace."
            action={
              monthFilter ? (
                <Button variant="secondary" onClick={() => setMonth(ALL_MONTHS)}>
                  Ver todos los meses
                </Button>
              ) : (
                <Button variant="primary" href="/proyectos/nuevo">
                  Nuevo proyecto
                </Button>
              )
            }
          />
        )
      ) : movements.data ? (
        <Card as="section" aria-label={`Movimientos de ${scope}`} style={{ overflow: 'hidden' }}>
          <Table className={styles.mtable} caption={`Movimientos de ${scope}`}>
            <thead>
              <tr>
                <th style={{ width: 176 }}>Fecha</th>
                <th>Concepto</th>
                <th style={{ width: 220 }}>Tipo</th>
                <th style={{ width: 150 }} className={styles.right}>
                  Monto (tokens)
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => {
                const meta = KIND_META[item.kind];
                return (
                  <tr key={item.id}>
                    <td>
                      <span className={cx('num t-small muted', styles.nowrap)}>{formatDateTime(item.created_at)}</span>
                    </td>
                    <td>
                      <div className={styles.cell2}>
                        <span>{movementConcept(item)}</span>
                        <span className="t-small muted">
                          {item.project ? (
                            <Link className={styles.link} href={`/proyectos/${item.project.id}`}>
                              {item.project.title}
                            </Link>
                          ) : item.kind === 'pending' ? (
                            'Esperando la confirmación del pago'
                          ) : item.created_by_detail ? (
                            `La hizo ${item.created_by_detail.name}`
                          ) : null}
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className={cx(styles.cell2, styles.kindCell)}>
                        <KindBadge kind={item.kind} />
                        {meta.note ? <span className={styles.kindNote}>{meta.note}</span> : null}
                      </div>
                    </td>
                    <td className={styles.right}>
                      <MovementAmount transaction={item} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <div className={styles.foot}>
            <span className="t-small muted">
              <span className="num">{formatNumber(movements.data.count, 0)}</span> {movements.data.count === 1 ? 'movimiento' : 'movimientos'} en {scope}
            </span>
            <span className="t-small subtle">Pasá el mouse por un tipo para ver qué significa.</span>
          </div>
        </Card>
      ) : null}
      {movements.data && movements.data.count > DEFAULT_PAGE_SIZE ? (
        <Pagination page={page} pageSize={DEFAULT_PAGE_SIZE} count={movements.data.count} onPageChange={setPage} noun="movimientos" />
      ) : null}
    </>
  );
}

/** G2 · Uso › Movimientos. Only Dueño and Admin (map 1.7); the rest get K2. */
export function MovementsScreen() {
  return (
    <RequireRole action="billing.manage">
      <Movements />
    </RequireRole>
  );
}
