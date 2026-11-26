'use client';

import { useState, type ReactNode } from 'react';
import type { Clip, Project } from '@/api/contracts/projects';
import { usePricing, useRetryClip, useWallet } from '@/api/hooks';
import { errorMessage } from '@/lib/error-messages';
import { formatTokens } from '@/lib/format';
import { estimateCost, planAllowsResolution, type CostEstimate } from '@/lib/pricing';
import { usePlan } from '@/shell/session';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { useToast } from '@/ui/Toast';
import { useBillingDialogs } from './BillingDialogs';
import { ConfirmCostDialog } from './ConfirmCostDialog';

type RetryTarget = Pick<Clip, 'id' | 'title' | 'start_time' | 'end_time'>;

/** Seconds the backend bills for a clip (`render_cost`): rounded to the millisecond, then up to the second. */
function billedSeconds(clip: Pick<Clip, 'start_time' | 'end_time'>): number {
  return Math.ceil(Math.round(Math.max(0, clip.end_time - clip.start_time) * 1000) / 1000);
}

export interface RetryClipFlow {
  /** Starts the retry of a failed clip: the plan and balance checks first, then "Confirmar costo" (D1). */
  ask: (clip: RetryTarget) => void;
  /** What retrying this clip costs, or null until the prices load. */
  estimateFor: (clip: Pick<Clip, 'start_time' | 'end_time'>) => CostEstimate | null;
  /** "12,5 tokens" for the hint of the action; a generic text until the prices load. */
  costHint: (clip: Pick<Clip, 'start_time' | 'end_time'>) => string;
  /** The clip whose retry is being sent. */
  retryingId: string | null;
  /** Render it once in the screen. */
  dialog: ReactNode;
}

/**
 * Retrying a failed clip (D3, D4) is a paid render: the share of the clip went back when it failed,
 * so the retry reserves its render again (`POST clips/{id}/retry/`). Like every paid action it
 * shows the cost before it runs (map 1.5): the same "Confirmar costo" dialog of a render, D2 when
 * the balance does not reach and D3 when the plan does not allow the project's resolution.
 */
export function useRetryClipFlow(project: Pick<Project, 'metadata'> | null | undefined): RetryClipFlow {
  const toast = useToast();
  const billing = useBillingDialogs();
  const retry = useRetryClip();
  const pricing = usePricing();
  const wallet = useWallet();
  const { plan } = usePlan();
  const [clip, setClip] = useState<RetryTarget | null>(null);
  const resolution = project?.metadata.render_resolution ?? '720p';

  const estimateFor: RetryClipFlow['estimateFor'] = (target) =>
    pricing.data ? estimateCost({ durationSeconds: billedSeconds(target), resolution }, pricing.data, plan) : null;

  const close = () => {
    retry.reset();
    setClip(null);
  };

  const ask: RetryClipFlow['ask'] = (target) => {
    const estimate = estimateFor(target);
    const balance = wallet.data?.available_balance;
    if (plan && !planAllowsResolution(plan, resolution)) {
      billing.showPlanLimit({ kind: 'resolution', detail: `Este proyecto se creó en ${resolution} y el plan ${plan.name} renderiza hasta ${plan.max_resolution}.` });
      return;
    }
    // The estimate already says it does not fit: straight to "No te alcanza el saldo" (D2).
    if (estimate && balance !== undefined && estimate.total > balance) {
      billing.showInsufficientBalance({ required: estimate.total, concept: 'Costo del render' });
      return;
    }
    retry.reset();
    setClip(target);
  };

  const confirm = () => {
    if (!clip) return;
    retry.mutate(clip.id, {
      onSuccess: (result) => {
        toast.success(`Reintentando el clip. Reservamos ${formatTokens(result.final_cost)}; si vuelve a fallar, te los devolvemos.`);
        setClip(null);
      },
      onError: (error) => {
        if (billing.handleError(error, { concept: 'Costo del render' })) close();
      },
    });
  };

  const estimate = clip ? estimateFor(clip) : null;
  let dialog: ReactNode = null;
  if (clip && estimate && wallet.data) {
    dialog = (
      <ConfirmCostDialog
        open
        onClose={close}
        onConfirm={confirm}
        title="Reintentar el clip"
        subtitle={clip.title}
        durationSeconds={billedSeconds(clip)}
        resolution={resolution}
        estimate={estimate}
        planName={plan?.name}
        balance={wallet.data.available_balance}
        baseRate={pricing.data?.base_cost_per_second_720p}
        confirmLabel="Reintentar"
        loading={retry.isPending}
        error={retry.isError ? errorMessage(retry.error, 'No pudimos reintentar el clip.') : undefined}
      />
    );
  } else if (clip) {
    const failed = pricing.isError || wallet.isError;
    dialog = (
      <Dialog
        open
        onClose={close}
        title="Reintentar el clip"
        description={clip.title}
        footer={
          <Button variant="ghost" onClick={close} data-autofocus>
            Cancelar
          </Button>
        }
      >
        <p className="t-small muted" role={failed ? 'alert' : 'status'}>
          {failed ? 'No pudimos calcular el costo. Cerrá y probá de nuevo.' : 'Calculando el costo…'}
        </p>
      </Dialog>
    );
  }

  return {
    ask,
    estimateFor,
    costHint: (target) => {
      const cost = estimateFor(target);
      return cost ? formatTokens(cost.total) : 'Cuesta tokens';
    },
    retryingId: retry.isPending ? (retry.variables ?? null) : null,
    dialog,
  };
}
