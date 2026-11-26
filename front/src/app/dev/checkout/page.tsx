'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';
import { API_MOCKS } from '@/api/config';
import { getTokenPurchase } from '@/api/contracts/billing';
import { usePlans, useSimulatePurchase, useTokenPurchase } from '@/api/hooks';
import { getMockPurchase, resolveMockPurchase } from '@/api/mocks/control';
import { routeOf } from '@/api/routing';
import { leaveTo } from '@/features/uso/leave';
import { errorMessage } from '@/lib/error-messages';
import { formatNumber, formatUsd } from '@/lib/format';
import { AuthCard } from '@/shell/PublicShell';
import { Banner, Button } from '@/ui';

const DEFAULT_RETURN = '/uso/comprar/resultado';

/** `volver` comes from the URL: only a page of this same app is accepted, never another site. */
function safeReturn(value: string | null): string {
  if (!value) return DEFAULT_RETURN;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin ? `${url.pathname}${url.search}` : DEFAULT_RETURN;
  } catch {
    return DEFAULT_RETURN;
  }
}

interface CheckoutPurchase {
  tokens: number;
  amount_usd: number;
  plan_name: string | null;
  /** False once the payment is settled: there is nothing left to decide. */
  pending: boolean;
}

function Checkout() {
  const search = useSearchParams();
  const purchaseId = search.get('compra') ?? '';
  const volver = search.get('volver');
  // With `billing` on the backend (hybrid mode) the purchase is the backend's, made by its simulated provider
  // (PAYMENTS_PROVIDER=simulated): it is read from there and decided with `billing.simulatePurchase`.
  // Otherwise it lives in the mock database.
  const real = routeOf(getTokenPurchase) === 'real';
  const remote = useTokenPurchase(real && purchaseId ? purchaseId : null);
  const plans = usePlans({ enabled: real });
  const simulate = useSimulatePurchase();
  const [mock, setMock] = useState<CheckoutPurchase | null>(null);
  useEffect(() => {
    const found = API_MOCKS && !real ? getMockPurchase(purchaseId) : null;
    setMock(found ? { ...found, pending: true } : null);
  }, [purchaseId, real]);

  const purchase: CheckoutPurchase | null = real
    ? remote.data
      ? {
          tokens: remote.data.tokens,
          amount_usd: remote.data.amount_usd,
          plan_name: remote.data.plan_id ? (plans.data?.find((plan) => plan.id === remote.data.plan_id)?.name ?? 'nuevo') : null,
          pending: remote.data.status === 'pending',
        }
      : null
    : mock;

  const goBack = () => {
    const url = new URL(safeReturn(volver), window.location.origin);
    url.searchParams.set('compra', purchaseId);
    leaveTo(url.toString());
  };
  const finish = (approved: boolean) => {
    if (real) {
      simulate.mutate({ id: purchaseId, approved }, { onSuccess: goBack });
      return;
    }
    resolveMockPurchase(purchaseId, approved);
    goBack();
  };

  return (
    <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 16 }}>
      <AuthCard title="Checkout de prueba" description="Esta página reemplaza al proveedor de pagos: no cobra nada.">
        {purchase ? (
          <>
            <p>
              {purchase.plan_name ? `Plan ${purchase.plan_name}` : <span className="num">{formatNumber(purchase.tokens, 0)} tokens</span>} por{' '}
              <span className="num">{formatUsd(purchase.amount_usd)}</span>
              {purchase.plan_name ? ' por mes' : ''}
            </p>
            {simulate.isError ? <Banner tone="err" title={errorMessage(simulate.error, 'No pudimos registrar el pago de prueba')} /> : null}
            {purchase.pending ? (
              <>
                <Button variant="primary" block onClick={() => finish(true)} loading={simulate.isPending && simulate.variables?.approved === true} disabled={simulate.isPending}>
                  Pagar
                </Button>
                <Button variant="secondary" block onClick={() => finish(false)} loading={simulate.isPending && simulate.variables?.approved === false} disabled={simulate.isPending}>
                  Simular pago rechazado
                </Button>
              </>
            ) : (
              <>
                <Banner tone="info" title="Este pago ya está resuelto" />
                <Button variant="primary" block onClick={goBack}>
                  Ver el resultado
                </Button>
              </>
            )}
          </>
        ) : real && remote.isLoading ? (
          <p className="t-small muted" role="status">
            Buscando la compra…
          </p>
        ) : (
          <Banner tone="warn" title="No encontramos esa compra">
            Volvé a Comprar tokens y empezá de nuevo.
          </Banner>
        )}
        <Button variant="ghost" block href={purchase?.plan_name ? '/uso/planes' : '/uso/comprar'}>
          {purchase?.plan_name ? 'Volver a Planes' : 'Volver a Comprar tokens'}
        </Button>
      </AuthCard>
    </div>
  );
}

/**
 * Dev-only (modes mock and hybrid): stands in for the payment provider's checkout (see CONTRACT.md ·
 * billing.createPurchase and billing.simulatePurchase). In a `real` build /dev does not exist.
 */
export default function MockCheckoutPage() {
  return (
    <Suspense>
      <Checkout />
    </Suspense>
  );
}
