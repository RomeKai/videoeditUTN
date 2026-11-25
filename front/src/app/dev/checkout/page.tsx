'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';
import { API_MOCKS } from '@/api/config';
import { getMockPurchase, resolveMockPurchase } from '@/api/mocks/control';
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

function Checkout() {
  const search = useSearchParams();
  const purchaseId = search.get('compra') ?? '';
  const volver = search.get('volver');
  const [purchase, setPurchase] = useState<{ tokens: number; amount_usd: number; plan_name: string | null } | null>(null);
  useEffect(() => setPurchase(API_MOCKS ? getMockPurchase(purchaseId) : null), [purchaseId]);

  const finish = (approved: boolean) => {
    resolveMockPurchase(purchaseId, approved);
    const url = new URL(safeReturn(volver), window.location.origin);
    url.searchParams.set('compra', purchaseId);
    window.location.assign(url.toString());
  };

  return (
    <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 16 }}>
      <AuthCard title="Checkout de prueba" description="Esta página reemplaza al proveedor de pagos mientras la API está simulada.">
        {purchase ? (
          <>
            <p>
              {purchase.plan_name ? `Plan ${purchase.plan_name}` : <span className="num">{formatNumber(purchase.tokens, 0)} tokens</span>} por{' '}
              <span className="num">{formatUsd(purchase.amount_usd)}</span>
              {purchase.plan_name ? ' por mes' : ''}
            </p>
            <Button variant="primary" block onClick={() => finish(true)}>
              Pagar
            </Button>
            <Button variant="secondary" block onClick={() => finish(false)}>
              Simular pago rechazado
            </Button>
          </>
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

/** Mock-only: stands in for the payment provider's checkout (see CONTRACT.md · billing.createPurchase). */
export default function MockCheckoutPage() {
  return (
    <Suspense>
      <Checkout />
    </Suspense>
  );
}
