'use client';

import { useEffect, useState } from 'react';
import { clearRouteOverrides, setRouteOverride, type Route } from '@/api/routing';
import { domainRouting, type DomainRouting } from '@/api/routingTable';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import styles from './MockPanel.module.css';

const reload = () => window.location.reload();

const ORIGIN: Record<DomainRouting['route'], { label: string; tone: 'ok' | 'neutral' | 'info' }> = {
  real: { label: 'Real', tone: 'ok' },
  mock: { label: 'Mock', tone: 'neutral' },
  mixed: { label: 'Mixto', tone: 'info' },
};

function detail(row: DomainRouting): string {
  if (row.locked) return row.locked;
  const share = `${row.real} de ${row.total} endpoints van al backend`;
  if (row.override) return `${share}. Cambiado a mano en este navegador (el env dice ${ORIGIN[row.envRoute].label.toLowerCase()}).`;
  if (row.route === 'mixed') return `${share}: los que el contrato marca como existentes.`;
  return `${share}.`;
}

/**
 * Hybrid mode: who answers each domain of the contract, the real backend or the mocks, with a dev-only
 * switch per domain. The defaults come from the env (`NEXT_PUBLIC_API_REAL`, or the contract's
 * `backend.status`); a switch is kept in this browser's localStorage and applied after a reload.
 */
export function ApiRoutingPanel({ onChange = reload }: { onChange?: () => void }) {
  // Read after mount: the overrides live in localStorage, which the server render does not have.
  const [rows, setRows] = useState<DomainRouting[] | null>(null);
  useEffect(() => setRows(domainRouting()), []);
  if (!rows) return null;

  const choose = (row: DomainRouting, route: Route) => {
    // Choosing what the env already says removes the override instead of pinning it.
    setRouteOverride(row.domain, row.envRoute === route ? null : route);
    setRows(domainRouting());
    onChange();
  };
  const overridden = rows.some((row) => row.override !== null);

  return (
    <div className={styles.routing} role="group" aria-label="Origen de cada dominio de la API">
      <p className={`t-over ${styles.group}`}>Origen por dominio</p>
      <ul className={styles.domains}>
        {rows.map((row) => (
          <li key={row.domain} className={styles.domain} data-domain={row.domain} data-route={row.route}>
            <div className={styles.domainHead}>
              <span className={styles.itemName}>{row.title}</span>
              <Badge tone={ORIGIN[row.route].tone} size="sm">
                {ORIGIN[row.route].label}
              </Badge>
            </div>
            <div className={styles.domainFoot}>
              <span className={styles.itemDesc}>{detail(row)}</span>
              <span className={styles.switch} role="group" aria-label={`Origen de ${row.title}`}>
                {(['real', 'mock'] as const).map((route) => (
                  <button
                    key={route}
                    type="button"
                    className={styles.switchOption}
                    aria-pressed={row.route === route}
                    disabled={row.locked !== null}
                    title={row.locked ?? undefined}
                    onClick={() => choose(row, route)}
                  >
                    {ORIGIN[route].label}
                  </button>
                ))}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {overridden ? (
        <div className={styles.routingFoot}>
          <Button
            size="sm"
            variant="ghost"
            iconLeft="retry"
            onClick={() => {
              clearRouteOverrides();
              setRows(domainRouting());
              onChange();
            }}
          >
            Volver a lo que dice el env
          </Button>
        </div>
      ) : null}
    </div>
  );
}
