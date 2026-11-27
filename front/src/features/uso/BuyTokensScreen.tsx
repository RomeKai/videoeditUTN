'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { usePricing, useCreateTokenPurchase, useSubscription, useTokenPackages, useWallet } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { formatMinutes, formatMoney, formatNumber, formatPercent, formatUsd } from '@/lib/format';
import { secondsForTokens } from '@/lib/pricing';
import { LoadError } from '@/shell/LoadError';
import { RequireRole } from '@/shell/RequireSession';
import { Badge, Banner, Button, Card, Field, Input, Skeleton } from '@/ui';
import { leaveTo } from './leave';
import styles from './uso.module.css';

/**
 * "2.500" or "2500" → 2500. Null when it is not a whole positive number: a dot only counts as a
 * thousands separator when groups of three digits follow it, so "2.5" and "2,5" are decimals, not 25.
 */
export function parseTokenAmount(text: string): number | null {
  const clean = text.trim();
  if (!/^(\d+|\d{1,3}(\.\d{3})+)$/.test(clean)) return null;
  const value = Number(clean.replace(/\./g, ''));
  return value > 0 ? value : null;
}

function BuyTokens() {
  const search = useSearchParams();
  const packages = useTokenPackages();
  const wallet = useWallet();
  const pricing = usePricing();
  const subscription = useSubscription();
  const purchase = useCreateTokenPurchase();
  const groupId = useId();
  const group = useRef<HTMLDivElement>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [custom, setCustom] = useState('');
  const [leaving, setLeaving] = useState(false);

  // Coming back from the checkout with the browser's Back button restores this page from the
  // back/forward cache, still "leaving": the button must work again.
  const { reset: resetPurchase } = purchase;
  useEffect(() => {
    const onShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      setLeaving(false);
      resetPurchase();
    };
    window.addEventListener('pageshow', onShow);
    return () => window.removeEventListener('pageshow', onShow);
  }, [resetPurchase]);

  const data = packages.data;
  const plan = subscription.data?.plan ?? null;
  const list = data?.packages ?? [];
  // Default: the pack the insufficient-balance dialog suggested, else the middle one.
  // `tokens` comes from a purchase that did not go through (G5): the same pack, or the same free amount.
  const preferred = search.get('paquete');
  const wanted = parseTokenAmount(search.get('tokens') ?? '');
  const fallback =
    list.find((item) => item.id === preferred)?.id ?? list.find((item) => item.tokens === wanted)?.id ?? list[Math.min(1, list.length - 1)]?.id ?? null;
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || !data || wanted === null) return;
    prefilled.current = true;
    if (!data.packages.some((item) => item.tokens === wanted)) setCustom(formatNumber(wanted, 0));
  }, [data, wanted]);
  const selectedId = chosen ?? fallback;
  const pack = list.find((item) => item.id === selectedId) ?? null;

  const customTyped = custom.trim() !== '';
  const customTokens = customTyped ? parseTokenAmount(custom) : null;
  const minimum = data?.custom_min_tokens ?? 1;
  const customError = !customTyped
    ? null
    : customTokens === null
      ? 'Escribí una cantidad de tokens, sin decimales.'
      : customTokens < minimum
        ? `El mínimo es ${formatNumber(minimum, 0)} tokens.`
        : null;

  const tokens = customTyped ? (customError ? null : customTokens) : (pack?.tokens ?? null);
  const usd = !data || tokens === null ? null : customTyped ? Math.round(tokens * data.custom_price_per_token_usd * 100) / 100 : (pack?.price_usd ?? null);
  const local = (amount: number) => (data ? `≈ ${formatMoney(Math.round(amount * data.usd_to_local_rate), data.local_currency)}` : '');
  const minutes = (amount: number) => (pricing.data ? formatMinutes(secondsForTokens(amount, pricing.data, plan)) : null);
  const available = wallet.data?.available_balance ?? null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = list.findIndex((item) => item.id === selectedId);
    let next = index;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % list.length;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + list.length) % list.length;
    else return;
    if ((event.target as HTMLElement).getAttribute('role') !== 'radio') return;
    event.preventDefault();
    const target = list[next];
    if (!target) return;
    setChosen(target.id);
    setCustom('');
    group.current?.querySelectorAll<HTMLElement>('[role="radio"]')[next]?.focus();
  };

  // One key per purchase the person asks for: pressing Pagar again for the same amount after a lost answer sends
  // the same key, and the server answers the purchase it already started instead of opening a second checkout.
  const purchaseKey = useRef<{ signature: string; key: string } | null>(null);
  const pay = () => {
    if (tokens === null) return;
    const body = { ...(customTyped ? { custom_tokens: tokens } : { package_id: pack?.id }), return_url: `${window.location.origin}/uso/comprar/resultado` };
    const signature = JSON.stringify(body);
    if (purchaseKey.current?.signature !== signature) purchaseKey.current = { signature, key: crypto.randomUUID() };
    purchase.mutate(
      { ...body, idempotencyKey: purchaseKey.current.key },
      {
        onSuccess: (result) => {
          purchaseKey.current = null;
          setLeaving(true);
          leaveTo(result.checkout_url ?? `/uso/comprar/resultado?compra=${result.id}`);
        },
      },
    );
  };

  if (packages.isError) return <LoadError error={packages.error} onRetry={() => void packages.refetch()} />;

  return (
    <section className={styles.stack} aria-labelledby={`${groupId}-title`}>
      <div>
        <h2 className="t-h3" id={`${groupId}-title`}>
          Elegí cuántos tokens comprar
        </h2>
        <p className="t-small muted" style={{ marginTop: 4 }}>
          No vencen y los usa todo el workspace. La duración es un estimado a 720p
          {plan && plan.base_discount_rate > 0 ? ` con el ${formatPercent(plan.base_discount_rate)} de descuento de tu plan ${plan.name}` : ''}.
        </p>
      </div>

      {data ? (
        <div className={styles.packs} role="radiogroup" aria-labelledby={`${groupId}-title`} ref={group} onKeyDown={onKeyDown}>
          {list.map((item) => {
            const checked = !customTyped && item.id === selectedId;
            const time = minutes(item.tokens);
            return (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={checked}
                tabIndex={item.id === selectedId ? 0 : -1}
                className={styles.pack}
                onClick={() => {
                  setChosen(item.id);
                  setCustom('');
                }}
              >
                <span className={styles.packN}>
                  <span className="num">{formatNumber(item.tokens, 0)}</span>
                  <span className={styles.unit}>tokens</span>
                </span>
                {time ? (
                  <span className="t-small muted">
                    <span className="num">≈ {time}</span> de video a 720p
                  </span>
                ) : null}
                <span className={styles.packPrice}>
                  <span className={cx('num', styles.usd)}>{formatUsd(item.price_usd)}</span>
                  {item.discount_rate > 0 ? (
                    <Badge tone="outline" size="sm">
                      {formatPercent(item.discount_rate)} menos
                    </Badge>
                  ) : null}
                </span>
                <span className={cx('num', styles.local)}>{local(item.price_usd)}</span>
              </button>
            );
          })}
          <div className={cx(styles.pack, styles.packFree, customTyped && !customError && styles.packOn)}>
            <Field
              className={styles.freeField}
              label="Otra cantidad"
              error={customError ?? undefined}
              hint={
                <>
                  <span className="num">{formatUsd(data.custom_price_per_token_usd * 1000)}</span> cada <span className="num">1.000</span> tokens, sin
                  descuento por cantidad. Reemplaza al paquete elegido.
                </>
              }
            >
              <Input className="num" inputMode="numeric" placeholder="2.500" value={custom} onChange={(event) => setCustom(event.target.value)} autoComplete="off" />
            </Field>
          </div>
        </div>
      ) : (
        <div className={styles.packs} aria-busy="true">
          <span className="sr-only">Cargando los paquetes</span>
          {[0, 1, 2, 3].map((item) => (
            <Skeleton key={item} height={168} radius={8} />
          ))}
        </div>
      )}

      {purchase.isError ? (
        <Banner tone="err" title={errorMessage(purchase.error, 'No pudimos iniciar la compra')}>
          No se te cobró nada. Probá de nuevo.
        </Banner>
      ) : null}

      <Card className={styles.summary} as="section" aria-labelledby={`${groupId}-sum`}>
        <h2 className="sr-only" id={`${groupId}-sum`}>
          Resumen de la compra
        </h2>
        <dl className={styles.sum}>
          <div>
            <dt>{customTyped ? 'Cantidad' : 'Paquete'}</dt>
            <dd className="num">{tokens !== null ? `${formatNumber(tokens, 0)} tokens` : '—'}</dd>
          </div>
          <div>
            <dt>Saldo disponible hoy</dt>
            <dd className="num">{available !== null ? `${formatNumber(available)} tokens` : '—'}</dd>
          </div>
          <div>
            <dt>Saldo después de la compra</dt>
            <dd className="num">{available !== null && tokens !== null ? `${formatNumber(available + tokens)} tokens` : '—'}</dd>
          </div>
        </dl>
        <div className={styles.sumPay}>
          <div className={styles.sumTotal}>
            <span className="t-label muted">Total</span>
            <span className={cx('num', styles.total)}>{usd !== null ? formatUsd(usd) : '—'}</span>
            <span className={cx('num', styles.local)}>{usd !== null ? local(usd) : ''}</span>
          </div>
          <Button variant="primary" size="lg" onClick={pay} loading={purchase.isPending || leaving} disabled={tokens === null || usd === null}>
            Ir al pago
          </Button>
        </div>
      </Card>

      <div className={styles.buyFoot}>
        <p className="t-small subtle">
          Pagás en el checkout del proveedor, en tu moneda. No guardamos tu tarjeta.
          {data ? (
            <>
              {' '}
              El monto en {data.local_currency === 'ARS' ? 'pesos' : data.local_currency} es de referencia (
              <span className="num">1 USD = {formatMoney(data.usd_to_local_rate, data.local_currency)}</span>).
            </>
          ) : null}
        </p>
        <Link className={cx(styles.link, 't-small', styles.nowrap)} href="/uso">
          Volver a Uso
        </Link>
      </div>
    </section>
  );
}

/** G4 · Uso › Comprar tokens. Only Dueño and Admin buy (map 1.7). */
export function BuyTokensScreen() {
  return (
    <RequireRole action="billing.manage">
      <BuyTokens />
    </RequireRole>
  );
}
