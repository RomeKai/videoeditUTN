'use client';

import type { ReactNode } from 'react';
import { formatDuration, formatNumber, formatPercent, formatTokens } from '@/lib/format';
import type { CostEstimate } from '@/lib/pricing';
import { Button } from '@/ui/Button';
import { CostBreakdown, type CostLine } from '@/ui/DataDisplay';
import { Dialog } from '@/ui/Dialog';

export interface ConfirmCostDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  /** "Renderizar 6 clips", "Volver a renderizar". */
  title: ReactNode;
  /** Project or clip name. */
  subtitle?: ReactNode;
  /** Seconds that are charged. */
  durationSeconds: number;
  /** "720p", "1080p" or "4K". */
  resolution: string;
  /** From `estimateCost()` (lib/pricing.ts) or the backend estimate. */
  estimate: CostEstimate;
  /** Paid add-ons with their price: [{ label: 'SEO', tokens: 5 }]. */
  extras?: Array<{ label: string; tokens: number }>;
  /** Name of the plan that gives the discount. */
  planName?: string;
  /** Available balance today. */
  balance: number;
  /** Seconds per token-second, for the formula note. */
  baseRate?: number;
  confirmLabel?: string;
  loading?: boolean;
  /** Error of the action (not 402/403: those open their own dialogs). */
  error?: ReactNode;
}

/**
 * D1 · Confirmar costo: every paid action shows what it costs before it runs
 * (map 1.5): base × resolution + extras − discount, the total and the balance after.
 */
export function ConfirmCostDialog({
  open,
  onClose,
  onConfirm,
  title,
  subtitle,
  durationSeconds,
  resolution,
  estimate,
  extras = [],
  planName,
  balance,
  baseRate = 0.5,
  confirmLabel = 'Confirmar',
  loading = false,
  error,
}: ConfirmCostDialogProps) {
  const seconds = Math.floor(durationSeconds);
  const lines: CostLine[] = [
    {
      key: 'duration',
      label: 'Duración total',
      value: (
        <>
          {formatDuration(seconds)} <span className="subtle">· {formatNumber(seconds, 0)} s</span>
        </>
      ),
    },
    {
      key: 'base',
      label: 'Base por resolución',
      note: `${formatNumber(seconds, 0)} s × ${formatNumber(baseRate)} × ${formatNumber(estimate.multiplier)} (${resolution})`,
      value: formatNumber(estimate.base),
    },
    {
      key: 'extras',
      label: 'Extras',
      note: extras.length > 0 ? extras.map((extra) => extra.label).join(', ') : undefined,
      value: extras.length > 0 ? `+${formatNumber(estimate.addOns)}` : <span className="subtle">Ninguno</span>,
    },
  ];
  if (estimate.discountRate > 0) {
    lines.push({
      key: 'discount',
      label: planName ? `Descuento del plan ${planName}` : 'Descuento del plan',
      note: `−${formatPercent(estimate.discountRate)}`,
      value: `−${formatNumber(estimate.discount)}`,
    });
  }
  lines.push(
    { key: 'total', kind: 'total', label: 'Total', value: formatTokens(estimate.total) },
    { key: 'after', kind: 'after', label: 'Saldo después', value: `${formatNumber(balance)} → ${formatTokens(balance - estimate.total)}` },
  );
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={subtitle}
      dismissable={!loading}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={onConfirm} loading={loading} data-autofocus>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <CostBreakdown lines={lines} />
      <p className="t-small subtle">El costo queda reservado hasta que termine el render. Si algo falla, te lo devolvemos.</p>
      {error ? (
        <p className="t-small" style={{ color: 'var(--err)' }} role="alert">
          {error}
        </p>
      ) : null}
    </Dialog>
  );
}
