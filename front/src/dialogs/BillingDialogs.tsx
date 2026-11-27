'use client';

import Link from 'next/link';
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { Plan } from '@/api/contracts/billing';
import { ApiError } from '@/api/errors';
import { usePlans, useSubscription, useTokenPackages, useWallet } from '@/api/hooks';
import { planLimitKind, type PlanLimitKind } from '@/lib/error-messages';
import { formatMinutes, formatNumber, formatTokens, formatUsd } from '@/lib/format';
import { firstPlanWith } from '@/lib/pricing';
import { usePermissions, useSession } from '@/shell/session';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { CostBreakdown } from '@/ui/DataDisplay';
import { Dialog } from '@/ui/Dialog';
import styles from './Dialogs.module.css';

export interface InsufficientBalanceInput {
  /** Tokens the action needs. */
  required: number;
  /** What is being paid, for the first row: "Costo del proyecto", "Costo del render". */
  concept?: string;
}

export interface PlanLimitInput {
  kind: PlanLimitKind;
  /** Sentence under the title; a default is built from `kind` when absent. */
  detail?: ReactNode;
  /** "Cambiar opciones": goes back to the form with the field marked. */
  onChangeOptions?: () => void;
}

export interface BillingDialogs {
  /** D2 · Saldo insuficiente. */
  showInsufficientBalance: (input: InsufficientBalanceInput) => void;
  /** D3 · Límite del plan. */
  showPlanLimit: (input: PlanLimitInput) => void;
  /**
   * Opens D2 for a 402 and D3 for a plan 403. Returns true when it handled the
   * error; false means the caller shows it (field error, toast…).
   */
  handleError: (error: unknown, options?: { concept?: string; onChangeOptions?: () => void }) => boolean;
}

const BillingDialogsContext = createContext<BillingDialogs | null>(null);

const LIMIT_TITLE: Record<PlanLimitKind, string> = {
  duration: 'Este video supera el límite de tu plan',
  resolution: 'Tu plan no incluye esta resolución',
  seo: 'Tu plan no incluye SEO',
  scheduling: 'Tu plan no incluye programar publicaciones',
  crossposting: 'Tu plan permite una sola red por publicación',
  social: 'Tu plan no incluye conectar redes',
  seats: 'No quedan lugares en tu plan',
  unknown: 'Tu plan no permite esto',
};

function allowsMore(kind: PlanLimitKind, current: Plan | null): (plan: Plan) => boolean {
  const rank = { '720p': 1, '1080p': 2, '4K': 3 } as const;
  return (plan) => {
    if (current && plan.sort_order <= current.sort_order) return false;
    switch (kind) {
      case 'duration':
        return plan.max_video_duration_seconds > (current?.max_video_duration_seconds ?? 0);
      case 'resolution':
        return rank[plan.max_resolution] > rank[current?.max_resolution ?? '720p'];
      case 'seo':
        return plan.has_seo_optimization;
      case 'scheduling':
        return plan.allow_scheduling;
      case 'crossposting':
        return plan.allow_crossposting;
      case 'social':
        return plan.allow_social_accounts === true;
      case 'seats':
        return plan.max_members > (current?.max_members ?? 1);
      default:
        return true;
    }
  };
}

function planFeature(kind: PlanLimitKind, plan: Plan): string {
  switch (kind) {
    case 'duration':
      return `Videos de hasta ${formatMinutes(plan.max_video_duration_seconds)}`;
    case 'resolution':
      return `Resolución hasta ${plan.max_resolution}`;
    case 'seo':
      return plan.has_seo_optimization ? 'Títulos y descripciones SEO' : 'Sin SEO';
    case 'scheduling':
      return plan.allow_scheduling ? 'Programar publicaciones' : 'Solo publicar ahora';
    case 'crossposting':
      return plan.allow_crossposting ? 'Publicar en varias redes' : 'Una red por publicación';
    case 'social':
      return plan.allow_social_accounts ? 'Conectar redes sociales' : 'Sin redes conectadas';
    case 'seats':
      return `${plan.max_members} ${plan.max_members === 1 ? 'miembro' : 'miembros'}`;
    default:
      return plan.description;
  }
}

function InsufficientBalanceDialog({ input, onClose }: { input: InsufficientBalanceInput | null; onClose: () => void }) {
  const { status } = useSession();
  const wallet = useWallet({ enabled: status === 'authenticated' && input !== null });
  const packages = useTokenPackages({ enabled: status === 'authenticated' && input !== null });
  const { gate } = usePermissions();
  const available = wallet.data?.available_balance ?? 0;
  const required = input?.required ?? 0;
  const missing = Math.max(0, required - available);
  // The smallest fixed package that covers what is missing.
  const pack = [...(packages.data?.packages ?? [])].sort((a, b) => a.tokens - b.tokens).find((item) => item.tokens >= missing);
  return (
    <Dialog
      open={input !== null}
      onClose={onClose}
      role="alertdialog"
      icon="warning"
      iconTone="warn"
      title="No te alcanza el saldo"
      description={
        <>
          Necesitás <span className="num fg">{formatTokens(required)}</span> y tenés <span className="num fg">{formatNumber(available)}</span>.
        </>
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            href={pack ? `/uso/comprar?paquete=${pack.id}` : '/uso/comprar'}
            onClick={onClose}
            data-autofocus
            {...gate('billing.manage')}
          >
            {pack ? `Comprar ${formatNumber(pack.tokens, 0)} tokens · ${formatUsd(pack.price_usd)}` : 'Comprar tokens'}
          </Button>
        </>
      }
    >
      <CostBreakdown
        lines={[
          { key: 'cost', label: input?.concept ?? 'Costo', value: formatNumber(required) },
          { key: 'balance', label: 'Saldo disponible', value: formatNumber(available) },
          { key: 'missing', kind: 'total', label: 'Te faltan', value: formatTokens(missing) },
        ]}
      />
      <p className="t-small subtle">
        {pack ? (
          <>
            El paquete de <span className="num">{formatNumber(pack.tokens, 0)} tokens</span> es el más chico que cubre lo que falta.{' '}
          </>
        ) : null}
        Tu configuración queda guardada: cuando compres, seguís desde acá.
      </p>
    </Dialog>
  );
}

function PlanLimitDialog({ input, onClose }: { input: PlanLimitInput | null; onClose: () => void }) {
  const { status } = useSession();
  const subscription = useSubscription({ enabled: status === 'authenticated' && input !== null });
  const plans = usePlans({ enabled: input !== null });
  const kind = input?.kind ?? 'unknown';
  const current = subscription.data?.plan ?? null;
  const better = firstPlanWith(plans.data ?? [], allowsMore(kind, current));
  return (
    <Dialog
      open={input !== null}
      onClose={onClose}
      role="alertdialog"
      icon="lock"
      title={LIMIT_TITLE[kind]}
      description={
        input?.detail ??
        (current
          ? kind === 'duration'
            ? `El plan ${current.name} acepta videos de hasta ${formatMinutes(current.max_video_duration_seconds)}.`
            : kind === 'resolution'
              ? `El plan ${current.name} renderiza hasta ${current.max_resolution}.`
              : `El plan ${current.name} no lo incluye.`
          : undefined)
      }
      footer={
        <>
          <Button
            variant="secondary"
            onClick={() => {
              onClose();
              input?.onChangeOptions?.();
            }}
          >
            Cambiar opciones
          </Button>
          <Button variant="primary" href="/uso/planes" onClick={onClose} data-autofocus>
            Ver planes
          </Button>
        </>
      }
    >
      {current ? (
        <div className={styles.planCmp}>
          <div className={styles.planRow}>
            <div>
              <p className={styles.strong}>
                {current.name}{' '}
                <Badge tone="outline" size="sm">
                  Tu plan
                </Badge>
              </p>
              <p className="t-small muted">{planFeature(kind, current)}</p>
            </div>
            <span className="num t-small muted">{formatUsd(current.price_usd_monthly)}/mes</span>
          </div>
          {better ? (
            <Link className={`${styles.planRow} ${styles.planFit}`} href="/uso/planes" onClick={onClose}>
              <div>
                <p className={styles.strong}>
                  {better.name}{' '}
                  <Badge tone="ok" size="sm" icon="check">
                    Lo permite
                  </Badge>
                </p>
                <p className="t-small muted">{planFeature(kind, better)}</p>
              </div>
              <span className="num t-small muted">{formatUsd(better.price_usd_monthly)}/mes</span>
            </Link>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}

/** Mounted once in the root layout. Screens open the dialogs through useBillingDialogs(). */
export function BillingDialogsProvider({ children }: { children: ReactNode }) {
  const [insufficient, setInsufficient] = useState<InsufficientBalanceInput | null>(null);
  const [limit, setLimit] = useState<PlanLimitInput | null>(null);

  const handleError = useCallback<BillingDialogs['handleError']>((error, options = {}) => {
    if (!(error instanceof ApiError)) return false;
    if (error.isInsufficientFunds) {
      setInsufficient({ required: error.requiredTokens ?? 0, concept: options.concept });
      return true;
    }
    if (error.isPlanLimit) {
      setLimit({ kind: planLimitKind(error), onChangeOptions: options.onChangeOptions });
      return true;
    }
    return false;
  }, []);

  const value = useMemo<BillingDialogs>(
    () => ({ showInsufficientBalance: setInsufficient, showPlanLimit: setLimit, handleError }),
    [handleError],
  );

  return (
    <BillingDialogsContext.Provider value={value}>
      {children}
      <InsufficientBalanceDialog input={insufficient} onClose={() => setInsufficient(null)} />
      <PlanLimitDialog input={limit} onClose={() => setLimit(null)} />
    </BillingDialogsContext.Provider>
  );
}

/** D2 (402) and D3 (403 from pricing) for any paid action. */
export function useBillingDialogs(): BillingDialogs {
  const value = useContext(BillingDialogsContext);
  if (!value) throw new Error('useBillingDialogs necesita <BillingDialogsProvider> (ya está en el layout raíz).');
  return value;
}
