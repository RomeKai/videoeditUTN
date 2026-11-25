'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTokenPurchase, useWallet } from '@/api/hooks';
import { keys } from '@/api/query';
import { formatNumber, formatUsd } from '@/lib/format';
import { LoadError } from '@/shell/LoadError';
import { RequireRole } from '@/shell/RequireSession';
import { Banner, Button, Icon, IconButton, Spinner, useToast } from '@/ui';
import { UsageOverview } from './UsageOverview';
import styles from './uso.module.css';

const ANNOUNCED_KEY = 'oc.uso.compras-avisadas';

/** Purchases whose outcome was already announced in this browser session: a reload must not toast again. */
function wasAnnounced(id: string): boolean {
  try {
    return (JSON.parse(window.sessionStorage.getItem(ANNOUNCED_KEY) ?? '[]') as string[]).includes(id);
  } catch {
    return false;
  }
}

function markAnnounced(id: string): void {
  try {
    const ids = JSON.parse(window.sessionStorage.getItem(ANNOUNCED_KEY) ?? '[]') as string[];
    window.sessionStorage.setItem(ANNOUNCED_KEY, JSON.stringify([...ids.filter((item) => item !== id), id].slice(-20)));
  } catch {
    // Private mode: the worst case is a repeated toast.
  }
}

function PurchaseNotice() {
  const search = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();
  const id = search.get('compra');
  const purchase = useTokenPurchase(id);
  const wallet = useWallet();
  const [closed, setClosed] = useState(false);
  const announced = useRef<string | null>(null);

  const status = purchase.data?.status;
  const tokens = purchase.data?.tokens ?? 0;
  useEffect(() => {
    if (!id || status === undefined || status === 'pending' || announced.current === id) return;
    announced.current = id;
    // The balance, the movements and the usage changed (or the pending row went away).
    void queryClient.invalidateQueries({ queryKey: keys.billing.all });
    void queryClient.invalidateQueries({ queryKey: keys.me });
    if (status === 'approved' && !wasAnnounced(id)) {
      markAnnounced(id);
      toast.success(
        <>
          Compra acreditada: <span className="num">+{formatNumber(tokens, 0)} tokens</span>
        </>,
        { action: { label: 'Ver movimiento', href: '/uso/movimientos' } },
      );
    }
  }, [id, queryClient, status, toast, tokens]);

  if (!id) {
    return (
      <Banner
        tone="warn"
        className={styles.notice}
        title="No encontramos esa compra"
        action={
          <Button size="sm" variant="secondary" href="/uso/comprar">
            Comprar tokens
          </Button>
        }
      >
        Si pagaste recién, el movimiento aparece en unos minutos.
      </Banner>
    );
  }
  if (purchase.isError) {
    return <LoadError error={purchase.error} onRetry={() => void purchase.refetch()} className={styles.notice} fullPageForMissing={false} />;
  }
  if (!purchase.data || purchase.data.status === 'pending') {
    return (
      <div className={styles.strip} role="status" aria-busy="true">
        <Spinner />
        <p className="t-small">
          <span className={styles.strong}>Estamos confirmando tu pago.</span>{' '}
          <span className="muted">Suele tardar unos segundos. El saldo se actualiza solo; podés quedarte acá o seguir trabajando.</span>
        </p>
      </div>
    );
  }
  if (purchase.data.status === 'approved' || closed) {
    return (
      <p className="sr-only" role="status">
        {purchase.data.status === 'approved' ? `Compra acreditada: +${formatNumber(tokens, 0)} tokens.` : ''}
      </p>
    );
  }
  // Rejected: the balance did not change. The notice stays until it is closed or retried.
  // Both actions go back to Comprar tokens with the same amount chosen.
  const retryHref = tokens > 0 ? `/uso/comprar?tokens=${tokens}` : '/uso/comprar';
  return (
    <div className={styles.payFail} role="alert">
      <span className={styles.payFailIco}>
        <Icon name="error" />
      </span>
      <div className={styles.payFailBody}>
        <p className={styles.strong}>El pago no se completó. No se te cobró nada.</p>
        <p className="t-small muted">
          {purchase.data.failure_reason ? `${purchase.data.failure_reason} ` : ''}
          {tokens > 0 ? (
            <>
              Compra de <span className="num">{formatNumber(tokens, 0)}</span> tokens · <span className="num">{formatUsd(purchase.data.amount_usd)}</span>.{' '}
            </>
          ) : null}
          {wallet.data ? (
            <>
              Tu saldo sigue en <span className="num">{formatNumber(wallet.data.available_balance)}</span> tokens.
            </>
          ) : null}
        </p>
      </div>
      <div className={styles.payFailActions}>
        <Button size="sm" variant="secondary" href={retryHref}>
          Elegir otro medio
        </Button>
        <Button size="sm" variant="primary" href={retryHref}>
          Reintentar
        </Button>
        <IconButton
          icon="close"
          label="Cerrar aviso"
          size="sm"
          onClick={() => {
            setClosed(true);
            router.replace('/uso');
          }}
        />
      </div>
    </div>
  );
}

/**
 * G5 · Resultado de la compra: the provider's checkout comes back here with
 * `?compra=<id>`. It is the Resumen with the outcome on top: a toast when the
 * tokens were credited, a notice above the cards when the payment did not go
 * through, and a line while the provider has not answered yet.
 */
export function PurchaseResultScreen() {
  return (
    <RequireRole action="billing.manage">
      <UsageOverview notice={<PurchaseNotice />} />
    </RequireRole>
  );
}
